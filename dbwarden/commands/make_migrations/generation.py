from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from dbwarden.engine.core.statement_order import _assemble_migration
from dbwarden.engine.generation_state import (
    apply_changes,
    attach_state_changes,
    schema_state,
    state_checksum,
)
from dbwarden.engine.safety.classifiers import (
    LEGACY_SEVERITY,
    Safety,
    classify_operation,
)
from dbwarden.engine.safety.partition import group_operations
from dbwarden.engine.safety.plans import bind_plan
from dbwarden.engine.snapshot.sql_gen import collect_rollback_warnings, snapshot_diff_to_sql
from dbwarden.engine.version import (
    generate_migration_filename,
    generate_repeatable_filename,
    get_next_migration_number,
)
from dbwarden.files import atomic_write_text, preserve_files_on_error


def _json_value(value):
    import attrs
    from sqlalchemy.types import TypeEngine

    from dbwarden.engine.core.model_state import _table_to_state_entry
    from dbwarden.engine.core.models import ModelColumn, ModelTable

    if isinstance(value, ModelColumn):
        return _json_value(value.to_dict())
    if isinstance(value, ModelTable):
        return _json_value(_table_to_state_entry(value))
    if attrs.has(type(value)):
        return _json_value(attrs.asdict(value, recurse=False))
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_value(item) for item in value)
    if isinstance(value, (Enum, TypeEngine)):
        return value.value if isinstance(value, Enum) else str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(
        f"Cannot serialize typed operation value {type(value).__name__}. hint: provide JSON-compatible handler attributes"
    )


def include_data_operations(upgrade: list[dict], data_ops: list[dict]) -> list[dict]:
    sources = {
        (
            op["data_declaration"]["source"].get("schema"),
            op["data_declaration"]["source"]["table"],
        )
        for op in data_ops
        if op["data_kind"] == "transitions"
    }
    sources.update(
        (source["source"].get("schema"), source["source"]["table"])
        for op in data_ops
        for source in op["data_declaration"].get("merge", {}).get("inputs", [])
    )
    return [
        op
        for op in upgrade
        if not (
            op["type"] == "drop_table"
            and (op.get("state_table", {}).get("schema"), op.get("table")) in sources
        )
    ] + data_ops


def data_expansion_state(baseline: dict, target: dict, data_ops: list[dict]) -> dict:
    expanded = deepcopy(target)
    for op in data_ops:
        if op["data_kind"] != "transformations":
            continue
        name = op["data_declaration"]["target_table"]["table"]
        if name not in baseline.get("tables", {}):
            continue
        for column in op["data_declaration"]["target_columns"]:
            desired = expanded["tables"][name]["columns"][column]
            before = baseline["tables"][name]["columns"].get(column)
            if not desired.get("nullable", True) and (
                before is None or before.get("nullable", True)
            ):
                desired["nullable"] = True
    return expanded


def append_data_contract(
    upgrade: list[dict], baseline: dict, expanded: dict, target: dict, *, db_name: str
) -> None:
    if expanded == target:
        return
    contract, _ = diff_states(expanded, target, db_name=db_name)
    attach_state_changes(upgrade, baseline, expanded)
    attach_state_changes(contract, expanded, target)
    for op in contract:
        op["data_phase"] = "contract"
    upgrade.extend(contract)


def _schema_rollback_steps(op, step, backend):
    if op["type"] != "create_table":
        return [step]

    import sqlglot
    from sqlglot import exp

    from dbwarden.data.ir import digest
    from dbwarden.data.planning import (
        _guard,
        _mysql_rename_step,
        _mysql_table_exists,
        table_sql,
    )

    dialect = {"postgresql": "postgres", "mariadb": "mysql"}.get(backend, backend)
    if len(step["sql"]) != 1:
        raise ValueError("Create-table rollback requires one DROP TABLE statement")
    drop = sqlglot.parse_one(step["sql"][0], read=dialect)
    tables = list(drop.find_all(exp.Table))
    if (
        not isinstance(drop, exp.Drop)
        or drop.args.get("kind") != "TABLE"
        or len(tables) != 1
    ):
        raise ValueError("Create-table rollback requires DROP TABLE")
    table_ref = {"schema": tables[0].db or None, "table": tables[0].name}
    table = table_sql(table_ref, backend)
    operation_id = op["id"]
    if backend in {"mysql", "mariadb"}:
        rollback_ref = {
            **table_ref,
            "table": "_dbwarden_rollback_"
            + digest([operation_id, table_ref], "rollback-table")[:20],
        }
        renamed = table_sql(rollback_ref, backend)
        rename_step = _mysql_rename_step(table_ref, rollback_ref, backend)
        rename_step.update(
            operation_id=operation_id,
            step_id=digest(
                [operation_id, "rollback", "schema-rename"], "execution-step"
            ),
        )
        drop_step = {
            "operation_id": operation_id,
            "step_id": digest(
                [operation_id, "rollback", "schema-drop"], "execution-step"
            ),
            "sql": [f"DROP TABLE {renamed};"],
            "guards": [
                _guard(
                    f"SELECT COUNT(*) FROM {renamed}",
                    "Newly created table contains rows; rollback refused",
                ),
                _guard(
                    _mysql_table_exists(rollback_ref, backend),
                    "Rollback table removal did not complete",
                    timing="after",
                    dry_run=False,
                ),
            ],
        }
        return [rename_step, drop_step]

    if backend in {"postgresql", "sqlite"}:
        step["guards"].append(
            _guard(
                f"SELECT COUNT(*) FROM {table}",
                "Newly created table contains rows; rollback refused",
            )
        )
        step["step_id"] = digest(
            [operation_id, "rollback", "schema-drop"], "execution-step"
        )
        step["lock_tables"] = [table_ref]
    return [step]


def _execution_sql(steps):
    statements = []
    for step in steps:
        for sql in step["sql"]:
            sql = sql.rstrip()
            statements.append(sql if sql.endswith(";") else sql + ";")
    return "\n".join(statements)


def generate_files(
    upgrade_ops: list[dict],
    rollback_ops: list[dict],
    *,
    migrations_dir: str,
    database: str | None,
    db_name: str,
    description: str | None = None,
    baseline: dict | None = None,
    target: dict | None = None,
    threshold: str | None = None,
    migration_type: str = "versioned",
    concurrent: bool = True,
    postgres_auto_using: bool = False,
    write: bool = True,
    header: str = "",
    data_spec: dict | None = None,
) -> list[dict]:
    from dbwarden.commands.make_migrations.migrate_plan import (
        _resolve_migration_description,
        build_migration_plan,
    )
    from dbwarden.config import get_database

    backend = get_database(database).database_type
    if threshold is not None and migration_type != "versioned":
        raise ValueError(
            "Repeatable migrations cannot be split. hint: use --type versioned"
        )
    if not upgrade_ops:
        return []
    operations = deepcopy(upgrade_ops)
    if (
        any(op["type"] == "declarative_data" for op in operations)
        and migration_type != "versioned"
    ):
        raise ValueError(
            "Declarative data migrations must be versioned; repeatable SQL remains a separate feature"
        )
    from dbwarden.engine.core.models import IndexInfo
    from dbwarden.engine.snapshot.index_utils import _index_op_from_info

    for op in list(operations):
        if op["type"] == "create_table" and op.get("state_table"):
            indexes = op["state_table"].pop("indexes", [])
            operations.extend(
                _index_op_from_info(IndexInfo.from_dict(index), op["table"])
                for index in indexes
            )
    sink: list = []
    _, _, changes = snapshot_diff_to_sql(
        operations,
        deepcopy(rollback_ops),
        database=database,
        db_name=db_name,
        concurrent=concurrent,
        postgres_auto_using=postgres_auto_using,
        enforce_rollback_contract=True,
        statement_sink=sink,
    )
    for op, stmt in sink:
        if op.get("data_phase") == "contract":
            stmt.order = 19.5
    ordered = sorted(sink, key=lambda pair: pair[1].order)
    unique: list[dict] = []
    seen = set()
    for op, _ in ordered:
        if id(op) not in seen:
            seen.add(id(op))
            op.setdefault("id", f"op:{len(unique)}")
            unique.append(op)
    if any(id(op) not in seen for op in operations):
        raise ValueError(
            "An operation emitted no SQL. hint: correct its handler before generating"
        )
    for index, op in enumerate(unique):
        if op["type"] == "declarative_data":
            declaration = op["data_declaration"]
            ref = declaration.get("target_table", declaration.get("source"))
            op["state_table_name"] = ref["table"]
            op["referenced_tables"] = sorted(
                set(op.get("referenced_tables", []))
                | {
                    ref["table"],
                    *(
                        target["target_table"]["table"]
                        for target in declaration.get("targets", [])
                    ),
                }
            )
        touched = {
            op.get("table"),
            op.get("state_table_name"),
            *op.get("referenced_tables", []),
        } - {None}
        if op["type"] == "declarative_data" or op.get("data_phase") == "contract":
            dependencies = set(op.get("depends_on", []))
            for earlier in unique[:index]:
                if touched & {
                    earlier.get("table"),
                    earlier.get("state_table_name"),
                    *earlier.get("referenced_tables", []),
                } and (
                    earlier["type"]
                    in {
                        "declarative_data",
                        "add_column",
                        "create_table",
                        "recreate_sq_table",
                    }
                ):
                    dependencies.add(earlier["id"])
            op["depends_on"] = sorted(dependencies)
    if (baseline is None) != (target is None):
        raise ValueError(
            "Generation requires both baseline and target state, or neither"
        )
    if any(classify_operation(op, backend) == Safety.UNKNOWN for op in unique):
        unknown = sorted(
            {
                op["type"]
                for op in unique
                if classify_operation(op, backend) == Safety.UNKNOWN
            }
        )
        raise ValueError(
            f"Unclassified operations: {', '.join(unknown)}. hint: extend the shared classifier before generating"
        )
    if baseline is not None and target is not None and not all("state_changes" in op for op in unique):
        attach_state_changes(unique, baseline, target)
    groups = group_operations(unique, threshold, backend)
    if migration_type != "versioned" and (
        len(groups) > 1 or groups[0][0] not in {"base", "unsplit"}
    ):
        raise ValueError(
            "Repeatable migrations cannot use deferred or custom categories. hint: use --type versioned"
        )
    first_version = int(get_next_migration_number(migrations_dir))
    versions = [f"{first_version + index:04d}" for index in range(len(groups))]
    desc = _resolve_migration_description(description, changes)
    state = schema_state(baseline) if baseline is not None else None
    artifacts: list[dict[str, Any]] = []
    for index, (role, ops) in enumerate(groups):
        version = versions[index]
        name = desc
        if migration_type in ("ra", "runs_always", "roc", "runs_on_change"):
            from dbwarden.constants import (
                RUNS_ALWAYS_FILE_PREFIX,
                RUNS_ON_CHANGE_FILE_PREFIX,
            )

            prefix = (
                RUNS_ALWAYS_FILE_PREFIX
                if migration_type in ("ra", "runs_always")
                else RUNS_ON_CHANGE_FILE_PREFIX
            )
            filename = generate_repeatable_filename(db_name, name, prefix)
        else:
            filename = generate_migration_filename(db_name, name, version)
            if role not in {"base", "unsplit"}:
                filename = filename.removesuffix(".sql") + f"__{role}.sql"
        ids = {op["id"] for op in ops}
        statements = [stmt for op, stmt in ordered if op["id"] in ids]
        upgrade, rollback = _assemble_migration(statements)
        typed = _json_value(ops)
        plan: dict[str, Any] = build_migration_plan(
            Path(filename).stem,
            [],
            upgrade,
            rollback_warnings=collect_rollback_warnings(
                [(op, stmt) for op, stmt in ordered if op["id"] in ids]
            ),
        )
        plan["operations"] = [
            dict(op, severity=LEGACY_SEVERITY[classify_operation(op, backend)])
            for op in typed
        ]
        plan["summary"] = {
            "total_operations": len(ops),
            "operation_counts": {
                kind: sum(op["type"] == kind for op in ops)
                for kind in sorted({op["type"] for op in ops})
            },
        }
        for field, kind in (
            ("create_tables", "create_table"),
            ("drop_tables", "drop_table"),
            ("drop_columns", "drop_column"),
        ):
            plan["summary"][field] = plan["summary"]["operation_counts"].get(kind, 0)
        plan["upgrade_ops"] = typed
        if state is not None:
            plan["base_checksum"] = state_checksum(state)
            for op in ops:
                apply_changes(state, op["state_changes"])
            plan["target_checksum"] = state_checksum(state)
        content = header
        if any(op["type"] == "declarative_data" for op in ops):
            content += "-- dbwarden: data-bundle\n"
        renames = []
        for op in ops:
            if op["type"] == "rename_column":
                renames.append(
                    {
                        "from": f"{op['table']}.{op['old_name']}",
                        "to": f"{op['table']}.{op['new_name']}",
                    }
                )
            elif op["type"] == "rename_table":
                renames.append(
                    {"type": "table", "from": op["old_table"], "to": op["new_table"]}
                )
        if renames:
            content += "-- renames: " + json.dumps(renames, sort_keys=True) + "\n"
        if plan.get("base_checksum"):
            content += f"-- base_checksum: {plan['base_checksum']}\n"
        bind_plan(plan, "", typed, backend)
        if role != "unsplit":
            from dbwarden.plugin import ObjectPluginRegistry

            plan["category"] = ObjectPluginRegistry.categories()[role]
        content += f"-- dbwarden: file-severity {plan['severity']['file']}\n"
        if role != "unsplit":
            sibling = versions[1 - index] if len(versions) == 2 else None
            plan["severity"]["split"] = {
                "threshold": threshold,
                "role": role,
                "paired_with": sibling,
            }
            content += f"-- dbwarden: category {role}\n"
            if role == "deferred":
                content += f"-- dbwarden: split-from {sibling or 'none'} (threshold {threshold})\n"
            if index:
                content += f'-- depends_on: ["{versions[index - 1]}"]\n'
        content += f"-- upgrade\n\n{upgrade}\n\n-- rollback\n\n{rollback}\n"
        from dbwarden.engine.safety.plans import content_hash

        plan["content_hash"] = content_hash(content)
        artifact = {
            "filename": filename,
            "version": version,
            "content": content,
            "plan": plan,
        }
        if any(op["type"] == "declarative_data" for op in ops):
            from dbwarden.data.artifacts import build_manifest
            from dbwarden.data.frozen import render_frozen
            from dbwarden.engine.file_parser import _extract_section_statements

            if data_spec is None:
                raise ValueError("Data operations require their frozen canonical spec")
            plan["data_spec"] = data_spec
            plan["semantics"] = {
                "data_spec_checksum": data_spec["canonical_checksum"],
                "operation_ids": [op["id"] for op in ops],
                "backend": backend,
            }
            plan["review_evidence"] = {"probes": [], "status": "not_observed"}
            execution = {"upgrade": [], "rollback": []}
            for op, stmt in ordered:
                if op["id"] not in ids:
                    continue
                if op["type"] == "declarative_data":
                    execution["upgrade"].extend(deepcopy(op["data_upgrade"]))
                    execution["rollback"][0:0] = deepcopy(op["data_rollback"])
                else:
                    for direction, sql_text in (
                        ("upgrade", stmt.upgrade_sql),
                        ("rollback", stmt.rollback_sql),
                    ):
                        if (
                            "@dbwarden:autocommit" in sql_text
                            or " CONCURRENTLY " in sql_text.upper()
                        ):
                            raise ValueError(
                                "Data migrations require transactional DDL; use --no-concurrent or a separate schema migration"
                            )
                        step = {
                            "operation_id": op["id"],
                            "sql": _extract_section_statements(
                                "-- upgrade\n" + sql_text + "\n-- rollback\n",
                                "-- upgrade",
                            ),
                            "guards": [],
                        }
                        if direction == "upgrade":
                            execution[direction].append(step)
                        else:
                            execution[direction][0:0] = _schema_rollback_steps(
                                op, step, backend
                            )
            plan["data_execution"] = execution
            irreversible = "\n".join(
                line
                for line in rollback.splitlines()
                if line.lstrip().startswith("--")
                and "dbwarden: irreversible" in line.lower()
            )
            upgrade = _execution_sql(execution["upgrade"])
            rollback = _execution_sql(execution["rollback"])
            if irreversible:
                rollback = irreversible + "\n" + rollback
            content = content.split("-- upgrade\n", 1)[0]
            content += f"-- upgrade\n\n{upgrade}\n\n-- rollback\n\n{rollback}\n"
            plan["content_hash"] = content_hash(content)
            artifact["content"] = content
            artifact["frozen"] = render_frozen(data_spec, Path(filename).stem)
            plan["data_bundle"] = build_manifest(content, plan, artifact["frozen"])
        artifacts.append(artifact)
    if state is not None and target is not None and state != schema_state(target):
        raise ValueError(
            "Generated operations do not reproduce target state. hint: correct the diff before generating"
        )
    if write:
        _write_artifacts(artifacts, migrations_dir, migration_type)
    return artifacts


def _artifact_paths(artifacts, migrations_dir):
    return [
        path
        for artifact in artifacts
        for path in [
            Path(migrations_dir) / artifact["filename"],
            (Path(migrations_dir) / artifact["filename"]).with_suffix(".plan.json"),
            *(
                [(Path(migrations_dir) / artifact["filename"]).with_suffix(".data.py")]
                if "frozen" in artifact
                else []
            ),
            *([_data_snapshot_path(artifact)] if "frozen" in artifact else []),
        ]
    ]


def _data_snapshot_path(artifact):
    from dbwarden.config import get_database

    database = artifact["plan"]["data_spec"]["database"]["name"]
    config = get_database(database)
    root = Path(config.data_snapshot_dir) / database
    path = root / (Path(artifact["filename"]).stem + ".data.json")
    if not path.resolve().is_relative_to(
        Path.cwd().resolve()
    ) or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Data snapshot path escapes the project")
    return path


def _write_artifacts(artifacts, migrations_dir, migration_type="versioned"):
    paths = _artifact_paths(artifacts, migrations_dir)
    snapshots = {
        _data_snapshot_path(artifact) for artifact in artifacts if "frozen" in artifact
    }
    for path in paths:
        if path.is_symlink() or (
            path not in snapshots
            and not path.resolve().is_relative_to(Path(migrations_dir).resolve())
        ):
            raise ValueError(
                "Migration path escapes migrations directory or is a symlink"
            )
        if migration_type == "versioned" and path.exists():
            raise ValueError(
                f"Migration already exists: {path}. hint: regenerate with a new version"
            )
    with preserve_files_on_error(paths):
        for artifact in artifacts:
            path = Path(migrations_dir) / artifact["filename"]
            if "frozen" in artifact:
                atomic_write_text(path.with_suffix(".data.py"), artifact["frozen"])
                atomic_write_text(
                    _data_snapshot_path(artifact),
                    json.dumps(artifact["plan"]["data_spec"], indent=2, sort_keys=True)
                    + "\n",
                )
            atomic_write_text(path, artifact["content"])
            atomic_write_text(
                path.with_suffix(".plan.json"),
                json.dumps(artifact["plan"], indent=2, sort_keys=True) + "\n",
            )


def run_generation(
    *,
    description=None,
    database=None,
    output_plan=False,
    output_sql=False,
    rename_flags=None,
    safe_type_change=False,
    rename_table_flags=None,
    concurrent=True,
    offline=False,
    migration_type="versioned",
    clickhouse_engine_recreate=False,
    drop_preserved_clickhouse_table=None,
    postgres_auto_using=False,
    split_at_severity=None,
    strict_pending=None,
    data_only=False,
    parameters=None,
    show_managed_values=False,
    dry_run=False,
    verbose=False,
    perf=False,
):
    from dbwarden.commands.make_migrations import get_all_model_tables
    from dbwarden.commands.make_migrations.ch_ops import (
        _check_recreate_rename_conflict,
        _resolve_clickhouse_recreate_ops,
    )
    from dbwarden.commands.make_migrations.pipeline import (
        get_current_model_state_path,
        get_model_state_path,
    )
    from dbwarden.config import get_database, get_multi_db_config
    from dbwarden.engine.core.model_state import (
        model_state_json_dumps,
        model_state_to_dict,
    )
    from dbwarden.engine.discovery import (
        auto_discover_model_paths,
        filter_model_tables_by_name,
        validate_model_tables_exist,
    )
    from dbwarden.engine.generation_state import (
        check_deferred_conflicts,
        configuration_state,
        effective_state,
    )
    from dbwarden.engine.safety.classifiers import severity_level
    from dbwarden.engine.snapshot import (
        extract_full_schema_snapshot,
        find_latest_snapshot,
    )
    from dbwarden.engine.version import (
        get_migration_filepaths_by_version,
        get_migrations_directory,
    )
    from dbwarden.merge.detection import detect_merge_signals, get_diagnostic_message
    from dbwarden.output import info, plain, sql

    config = get_database(database)
    db_name = database or get_multi_db_config().default
    threshold = (
        split_at_severity if split_at_severity is not None else config.split_at_severity
    )
    if threshold is not None:
        threshold = severity_level(threshold).value
    strict = config.strict_pending if strict_pending is None else strict_pending
    if threshold is not None and migration_type != "versioned":
        import typer

        raise typer.BadParameter(
            "--split-at-severity cannot be combined with --type ra|roc. hint: use versioned migrations"
        )
    signals = detect_merge_signals(database)
    if signals:
        raise ValueError(
            f"Merge detected: {get_diagnostic_message(signals)}. hint: run dbwarden merge before generating"
        )
    paths = config.model_paths or auto_discover_model_paths()
    if not paths:
        raise ValueError("No model paths found. hint: configure model_paths")
    tables = get_all_model_tables(paths, db_name=db_name)
    validate_model_tables_exist(tables, config.model_tables, db_name)
    tables = filter_model_tables_by_name(tables, config.model_tables)
    migrations_dir = get_migrations_directory(database)
    files = get_migration_filepaths_by_version(migrations_dir)
    state_path = get_current_model_state_path(db_name)
    try:
        recorded = (
            json.loads(state_path.read_text(encoding="utf-8"))
            if state_path.exists()
            else None
        )
    except (ValueError, UnicodeError) as exc:
        raise ValueError(
            f"Invalid model state {state_path}. hint: restore it from version control or run export-models"
        ) from exc
    if recorded is not None and (
        not isinstance(recorded, dict) or not isinstance(recorded.get("tables"), dict)
    ):
        raise ValueError(
            f"Invalid model state {state_path}: expected an object with tables"
        )
    applied: set[str] = (
        set(recorded.get("generation_applied", [])) if recorded else set()
    )
    latest = find_latest_snapshot(db_name)
    if latest and latest.get("source") == "applied":
        baseline = latest.get("model_state", latest)
        applied = set(latest.get("applied_versions", []))
    elif offline:
        if recorded is None:
            raise ValueError(
                "No model state found. hint: run dbwarden export-models first"
            )
        baseline = recorded.get("generation_base", recorded)
        if not files and "generation_base" not in recorded:
            baseline = {
                "format_version": 2,
                "tables": {},
                "indexes": {},
                "constraints": {},
                "enums": {},
            }
    elif recorded and "generation_base" in recorded:
        baseline = recorded["generation_base"]
    else:
        baseline = latest
        if baseline is None:
            baseline = extract_full_schema_snapshot(
                database=db_name,
                sqlalchemy_url=config.sqlalchemy_url,
                database_type=config.database_type,
            )
            from dbwarden.repositories import (
                get_migrated_versions,
                migrations_table_exists,
            )

            if migrations_table_exists(db_name):
                applied = set(get_migrated_versions(db_name))
    baseline = configuration_state(baseline, config)
    target = configuration_state(model_state_to_dict(tables), config, desired=True)
    effective, pending = effective_state(
        baseline, files, applied=applied, strict=strict
    )
    from dbwarden.data.compiler import compile_data, discover_data
    from dbwarden.data.planning import plan_data, previous_data_spec

    data_spec = compile_data(
        discover_data(paths, config.data_paths, model_tables=config.model_tables),
        database=db_name,
        backend=config.database_type,
        parameters=parameters,
        registry_path=config.snapshot_registry,
    )
    previous_data = previous_data_spec(
        files, database=db_name, backend=config.database_type
    )
    data_ops = plan_data(data_spec, previous_data)
    if data_only:
        target = deepcopy(effective)
    check_deferred_conflicts(pending, effective, target)
    expanded = data_expansion_state(effective, target, data_ops)
    upgrade, rollback = diff_states(
        effective,
        expanded,
        db_name=db_name,
        rename_columns=rename_flags,
        rename_tables=rename_table_flags,
    )
    source_tables = {op["table"] for op in data_ops if op["data_kind"] == "transitions"}
    if data_only and source_tables:
        raise ValueError(
            "Transitions require unified make-migrations so source and target schema changes share the plan"
        )
    upgrade = include_data_operations(upgrade, data_ops)
    _check_recreate_rename_conflict(
        upgrade,
        {
            (op["old_table"], op["new_table"])
            for op in upgrade
            if op["type"] == "rename_table"
        },
    )
    _resolve_clickhouse_recreate_ops(
        upgrade, rollback, clickhouse_engine_recreate, drop_preserved_clickhouse_table
    )
    if safe_type_change:
        upgrade = expand_safe_type_changes(
            upgrade, effective, target, config.database_type
        )
    elif not (output_plan or output_sql) and any(
        op["type"] == "alter_column_type" for op in upgrade
    ):
        info(
            "Type change may rewrite the table. hint: consider --safe-type-change --split-at-severity WARN"
        )
    append_data_contract(upgrade, effective, expanded, target, db_name=db_name)
    artifacts = generate_files(
        upgrade,
        rollback,
        migrations_dir=migrations_dir,
        database=database,
        db_name=db_name,
        description=description,
        baseline=effective,
        target=target,
        threshold=threshold,
        migration_type=migration_type,
        concurrent=concurrent,
        postgres_auto_using=postgres_auto_using,
        write=False,
        data_spec=data_spec,
    )
    if output_plan:
        from dbwarden.commands.data import _redact

        visible = [
            _redact(artifact["plan"], show_managed_values)
            if "data_spec" in artifact["plan"]
            else artifact["plan"]
            for artifact in artifacts
        ]
        plain(
            json.dumps(
                visible[0] if len(visible) == 1 else visible,
                indent=2,
            )
        )
    elif output_sql:
        sql("\n".join(a["content"] for a in artifacts))
    elif not artifacts:
        info(
            "No new migrations to generate - all models already covered by existing migrations."
        )
    else:
        for artifact in artifacts:
            info(
                f"{'Would generate' if dry_run else 'Generated'}: {artifact['filename']} ({len(artifact['plan']['upgrade_ops'])} ops, max severity {artifact['plan']['severity']['file']})"
            )
        if not dry_run:
            state = {
                **target,
                "generation_base": schema_state(baseline),
                "generation_applied": sorted(applied),
                "generation_versions": sorted(files)
                + [a["version"] for a in artifacts],
            }
            state_paths = {
                get_model_state_path(db_name),
                get_model_state_path(db_name, legacy=True),
            }
            with preserve_files_on_error(
                [*_artifact_paths(artifacts, migrations_dir), *state_paths]
            ):
                _write_artifacts(artifacts, migrations_dir, migration_type)
                for path in state_paths:
                    atomic_write_text(path, model_state_json_dumps(state))


def diff_states(
    baseline: dict,
    target: dict,
    *,
    db_name=None,
    rename_columns=None,
    rename_tables=None,
) -> tuple[list[dict], list[dict]]:
    import sys

    from dbwarden.commands.make_migrations.cli_parsing import (
        _parse_rename_flags,
        _parse_rename_table_flags,
    )
    from dbwarden.commands.make_migrations.prompts import (
        _detect_table_rename_candidates,
        _prompt_rename_confirmations,
        _prompt_table_rename_confirmations,
    )
    from dbwarden.engine.core.model_state import reconstruct_model_table
    from dbwarden.engine.offline import diff_model_states
    from dbwarden.output import warning

    renamed = schema_state(baseline)
    target = schema_state(target)
    renames = []
    table_intents = _parse_rename_table_flags(rename_tables or [])
    flagged_tables = {
        (intent["old_table"], intent["new_table"]) for intent in table_intents
    }
    table_candidates = _detect_table_rename_candidates(
        renamed,
        [
            reconstruct_model_table({"name": name, **entry})
            for name, entry in target.get("tables", {}).items()
        ],
        {(intent["old_table"], intent["new_table"]) for intent in table_intents},
    )
    if table_candidates:
        if not sys.stdin.isatty():
            flags = ", ".join(
                f"--rename-table {old}:{new}" for old, new, _ in table_candidates
            )
            warning(
                f"Unconfirmed table renames will emit DROP + CREATE. hint: pass {flags} to preserve the tables"
            )
        else:
            table_intents.extend(_prompt_table_rename_confirmations(table_candidates))
    for intent in table_intents:
        old, new = intent["old_table"], intent["new_table"]
        if (
            old not in renamed["tables"]
            or new in renamed["tables"]
            or new not in target["tables"]
        ):
            raise ValueError(
                f"Invalid table rename {old}:{new}. hint: verify baseline and model names"
            )
        renamed["tables"][new] = renamed["tables"].pop(old)
        renamed["tables"][new]["name"] = new
        for resource in (
            *renamed.get("indexes", {}).values(),
            *renamed.get("constraints", {}).values(),
        ):
            for key in ("table", "referenced_table"):
                if resource.get(key) == old:
                    resource[key] = new
        renames.append(
            {
                "type": "rename_table",
                "old_table": old,
                "new_table": new,
                "resolved_from": "rename_flag"
                if (old, new) in flagged_tables
                else "prompt",
            }
        )
    normalized_columns = []
    for value in rename_columns or []:
        old, new = value.replace("=", ":", 1).split(":", 1)
        if "." in new:
            old_table = old.rsplit(".", 1)[0]
            new_table, new = new.rsplit(".", 1)
            if old_table != new_table:
                raise ValueError("Cross-table column rename is unsupported")
        normalized_columns.append(f"{old}:{new}")
    intents = _parse_rename_flags(normalized_columns)
    explicit = {(intent.table, intent.old_name, intent.new_name) for intent in intents}
    flagged_columns = set(explicit)
    detected, _ = diff_model_states(renamed, target, db_name=db_name)
    candidates = [
        (op["table"], op["old_name"], op["new_name"])
        for op in detected
        if op["type"] == "rename_column"
        and (op["table"], op["old_name"], op["new_name"]) not in explicit
    ]
    if candidates:
        if not sys.stdin.isatty():
            flags = ", ".join(
                f"--rename {table}.{old}:{new}" for table, old, new in candidates
            )
            warning(
                f"Unconfirmed column renames will emit DROP + ADD. hint: pass {flags} to preserve column data"
            )
        else:
            explicit.update(_prompt_rename_confirmations(candidates))
    for table, old, new in sorted(explicit):
        columns = renamed.get("tables", {}).get(table, {}).get("columns", {})
        if (
            old not in columns
            or new in columns
            or new not in target["tables"][table]["columns"]
        ):
            raise ValueError(f"Invalid column rename {table}.{old}:{new}")
        columns[new] = columns.pop(old)
        columns[new]["name"] = new
        for resource in (
            *renamed.get("indexes", {}).values(),
            *renamed.get("constraints", {}).values(),
        ):
            if resource.get("table") == table:
                resource["columns"] = [
                    new if value == old else value
                    for value in resource.get("columns", [])
                ]
            if resource.get("referenced_table") == table:
                resource["referenced_columns"] = [
                    new if value == old else value
                    for value in resource.get("referenced_columns", [])
                ]
        renames.append(
            {
                "type": "rename_column",
                "table": table,
                "old_name": old,
                "new_name": new,
                "resolved_from": "rename_flag"
                if (table, old, new) in flagged_columns
                else "prompt",
            }
        )
    upgrade, rollback = diff_model_states(
        renamed, target, db_name=db_name, detect_column_renames=False
    )
    return renames + upgrade, rollback


def expand_safe_type_changes(
    ops: list[dict], baseline: dict, target: dict, backend: str
) -> list[dict]:
    if not any(op["type"] == "alter_column_type" for op in ops):
        return ops
    if backend != "postgresql":
        raise ValueError(
            "Safe type-change splitting currently requires PostgreSQL. hint: use an explicit backend migration"
        )
    changed = {
        (op["table"], op["column"]) for op in ops if op["type"] == "alter_column_type"
    }
    result = []
    for op in ops:
        if op["type"] != "alter_column_type":
            if (op.get("table"), op.get("column")) in changed and op["type"] in {
                "alter_column_default",
                "alter_column_nullable",
                "alter_column_comment",
            }:
                continue
            result.append(op)
            continue
        table, column = op["table"], op["column"]
        temporary = column + "__new"
        if (
            temporary in baseline["tables"][table]["columns"]
            or temporary in target["tables"][table]["columns"]
        ):
            raise ValueError(f"Temporary column {table}.{temporary} already exists")
        definition = deepcopy(target["tables"][table]["columns"][column])
        old = baseline["tables"][table]["columns"][column]
        for state in (baseline, target):
            for resource in (
                *state.get("indexes", {}).values(),
                *state.get("constraints", {}).values(),
            ):
                if (
                    resource.get("table") == table
                    and (
                        column in resource.get("columns", [])
                        or resource.get("expression")
                    )
                ) or (
                    resource.get("referenced_table") == table
                    and column in resource.get("referenced_columns", [])
                ):
                    raise ValueError(
                        f"Safe type change for {table}.{column} has index/constraint dependencies. hint: use an explicit expand/backfill/contract migration preserving those dependencies"
                    )
        if any(
            entry.get(key)
            for entry in (old, definition)
            for key in (
                "primary_key",
                "unique",
                "foreign_key",
                "pg_column",
            )
        ) or any(
            entry.get("autoincrement") not in (None, False, "auto")
            for entry in (old, definition)
        ):
            raise ValueError(
                f"Safe type change for {table}.{column} has identity, key, or PostgreSQL metadata. hint: write an explicit expand/backfill/contract migration"
            )
        final_definition = deepcopy(definition)
        definition.update(name=temporary, nullable=True, default=None)
        result.extend(
            [
                {
                    "type": "add_column",
                    "table": table,
                    "column": temporary,
                    "definition": definition,
                    "safe_type_source": column,
                },
                {
                    "type": "safe_type_contract",
                    "table": table,
                    "column": column,
                    "temporary": temporary,
                    "old_definition": old,
                    "definition": definition,
                    "target_definition": final_definition,
                },
            ]
        )
    return result
