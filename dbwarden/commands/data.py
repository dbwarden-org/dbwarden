from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any

import typer

from dbwarden.files import atomic_write_text

data_app = typer.Typer(help="Render and document declarative data migrations")
transition_app = typer.Typer(help="Validate, plan, and audit data transitions")
data_app.add_typer(transition_app, name="transition")
_DATA_KINDS = ("managed_rows", "transformations", "validations", "transitions")


def make_data_migration(
    description: str,
    *,
    database: str | None = None,
    parameters: dict[str, Any] | None = None,
    dry_run: bool = False,
):
    """Generate a data-only migration through the shared generation pipeline."""
    from dbwarden.commands.make_migrations.generation import run_generation

    return run_generation(
        description=description,
        database=database,
        data_only=True,
        parameters=dict(parameters or {}),
        dry_run=dry_run,
    )


def _compiled(
    database: str | None,
    parameters: dict[str, Any] | None = None,
    *,
    frozen_fallback: bool = False,
):
    from dbwarden.config import get_database, get_multi_db_config
    from dbwarden.data.compiler import compile_data, discover_data, required_parameters
    from dbwarden.data.planning import plan_data, previous_data_spec
    from dbwarden.engine.model_discovery import auto_discover_model_paths
    from dbwarden.engine.version import (
        get_migration_filepaths_by_version,
        get_migrations_directory,
    )

    config = get_database(database)
    name = database or get_multi_db_config().default
    model_paths = config.model_paths or auto_discover_model_paths()
    if not model_paths and frozen_fallback:
        return _frozen_compiled(database)
    if not model_paths:
        raise typer.BadParameter("No model paths found. Configure model_paths first.")
    try:
        discovered = discover_data(
            model_paths, config.data_paths, model_tables=config.model_tables
        )
    except FileNotFoundError:
        if not frozen_fallback:
            raise
        return _frozen_compiled(database)
    files = get_migration_filepaths_by_version(get_migrations_directory(database))
    previous = previous_data_spec(files, database=name, backend=config.database_type)
    if parameters is None:
        from dbwarden.data.planning import decode_value

        needed = set(required_parameters(discovered))
        parameters = {}

        def collect(value):
            if isinstance(value, dict):
                for binding in value.get("bound_parameters", []):
                    if binding["name"] in needed:
                        decoded = decode_value(binding["value"])
                        if (
                            binding["name"] in parameters
                            and parameters[binding["name"]] != decoded
                        ):
                            raise ValueError(
                                "Frozen parameter bindings disagree; supply explicit --param values"
                            )
                        parameters[binding["name"]] = decoded
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(previous)
    spec = compile_data(
        discovered,
        database=name,
        backend=config.database_type,
        parameters=parameters,
        project_root=Path.cwd(),
        registry_path=config.snapshot_registry,
        schema_dir=Path(".dbwarden/schemas"),
    )
    operations = plan_data(spec, previous)
    execution = {
        "upgrade": [
            step for operation in operations for step in operation["data_upgrade"]
        ],
        "rollback": [
            step for operation in operations for step in operation["data_rollback"]
        ],
    }
    plan = {
        "data_spec": spec,
        "data_bundle": {
            "manifest_version": 1,
            "spec_checksum": spec["canonical_checksum"],
        },
        "data_execution": execution,
        "operations": operations,
    }
    return config, discovered, spec, plan


def _frozen_compiled(database: str | None):
    from dbwarden.data.artifacts import verify_bundle
    from dbwarden.data.frozen import read_frozen
    from dbwarden.engine.version import get_migrations_directory
    from dbwarden.merge.marker import is_superseded

    files = sorted(
        path
        for path in Path(get_migrations_directory(database)).glob("*.data.py")
        if not is_superseded(path.with_suffix("").with_suffix(".sql"))
    )
    if not files:
        raise typer.BadParameter(
            "No live model paths or frozen data artifacts found. Configure model_paths or generate a data migration first."
        )
    frozen = files[-1]
    verify_bundle(frozen.with_suffix("").with_suffix(".sql"))
    plan_path = frozen.with_suffix("").with_suffix(".plan.json")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(
            f"Cannot read frozen data plan: {frozen.name}"
        ) from exc
    if not isinstance(plan.get("data_execution"), dict):
        raise typer.BadParameter(
            f"Frozen data plan has no execution data: {frozen.name}"
        )
    spec = read_frozen(frozen)
    operations = plan.get("operations", plan.get("upgrade_ops", []))
    return (
        SimpleNamespace(),
        SimpleNamespace(models=()),
        spec,
        {
            **plan,
            "data_spec": spec,
            "operations": operations,
        },
    )


def _parameters(values: list[str] | None) -> dict[str, Any] | None:
    if not values:
        return None
    result = {}
    for value in values or []:
        name, separator, raw = value.partition("=")
        if not separator or not name or name in result:
            raise typer.BadParameter("--param requires distinct name=value entries")
        try:
            result[name] = json.loads(raw)
        except json.JSONDecodeError:
            result[name] = raw
    return result


def dry_run_data_plan(plan: dict, connection=None) -> dict:
    """Render frozen data steps and optionally execute read-only guard probes."""
    observed = []
    if connection is not None:
        from datetime import datetime, timezone

        from dbwarden.data.execution import _read_only_sql
        from dbwarden.data.ir import digest
        from dbwarden.data.sql import statement_text as text

        for step in plan["data_execution"]["upgrade"]:
            for guard in step.get("guards", []):
                if not guard.get("dry_run", True):
                    continue
                query = guard.get("dry_run_query", guard["query"])
                probe = {
                    "probe_id": guard["probe_id"],
                    "scope_checksum": digest(query, "probe-scope"),
                    "database": plan["data_spec"]["database"],
                    "isolation_level": connection.get_isolation_level(),
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "timing": guard["timing"],
                    "thresholds": guard.get("threshold", {}),
                    "count": connection.execute(
                        text(_read_only_sql(query))
                    ).scalar_one(),
                }
                observed.append({**probe, "checksum": digest(probe, "probe-evidence")})
    return {"dry_run": True, "plan": _redact(plan, False), "probes": observed}


def _redact(value: Any, show_values: bool) -> Any:
    if show_values:
        return value
    if isinstance(value, list):
        return [_redact(item, show_values) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"rows", "literal", "value", "values"}:
            result[key] = "<redacted>"
        elif key in {"sql", "query", "dry_run_query", "normalized_sql"}:
            result[key] = "<redacted SQL>"
        else:
            result[key] = _redact(item, show_values)
    return result


def _emit(payload: Any, format: str, *, show_values: bool = False) -> None:
    show_values = show_values or format == "sql"
    payload = _redact(payload, show_values)
    if format == "json":
        typer.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return
    if format == "sql":
        for step in payload["data_execution"]["upgrade"]:
            typer.echo("\n".join(step["sql"]))
        return
    if format == "operations":
        typer.echo(
            "\n".join(
                f"{item['id']} {item['data_kind']} {item['table']} {item['data_severity']}"
                for item in payload["operations"]
            )
        )
        return
    if format == "mapping":
        mappings = []
        for item in payload["data_spec"]["transitions"]:
            for target in item["targets"]:
                mappings.append(
                    {
                        "transition": item["declaration_id"],
                        "source": item["source"],
                        "target": target["target_table"],
                        "mappings": target["mappings"],
                    }
                )
        typer.echo(
            json.dumps(
                _redact(mappings, show_values), indent=2, sort_keys=True, default=str
            )
        )
        return
    typer.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _filter_spec(spec: dict, model: str | None, only_with_data: bool) -> dict:
    if model is None and not only_with_data:
        return spec
    needle = model.rsplit(":", 1)[-1] if model else None
    result = dict(spec)

    def matches(item):
        table = item["target_table"]
        qualified = ".".join(
            part for part in (table.get("schema"), table["table"]) if part
        )
        return (
            needle is None
            or needle in item["declaration_id"]
            or needle in {table["table"], qualified}
        )

    result["managed_rows"] = [item for item in spec["managed_rows"] if matches(item)]
    result["transformations"] = [
        item for item in spec["transformations"] if matches(item)
    ]
    result["validations"] = [
        item for item in spec.get("validations", []) if matches(item)
    ]
    result["transitions"] = [
        item
        for item in spec["transitions"]
        if needle is None
        or needle in item["declaration_id"]
        or needle == item["source"]["table"]
        or any(needle == target["target_table"]["table"] for target in item["targets"])
    ]
    selected_tables = {
        (item["target_table"].get("schema"), item["target_table"]["table"])
        for kind in ("managed_rows", "transformations", "validations")
        for item in result[kind]
    }
    selected_tables.update(
        (target["target_table"].get("schema"), target["target_table"]["table"])
        for item in result["transitions"]
        for target in item["targets"]
    )
    result["model_schema"] = [
        table
        for table in spec.get("model_schema", [])
        if (table.get("schema"), table["name"]) in selected_tables
        or (
            not only_with_data
            and needle
            in {
                table["name"],
                ".".join(part for part in (table.get("schema"), table["name"]) if part),
            }
        )
    ]
    if only_with_data and not any(result.get(key) for key in _DATA_KINDS):
        raise typer.BadParameter(f"No data declaration matches '{model}'.")
    return result


def _view_options(include: str, relationship_depth: int) -> None:
    requested = {item.strip() for item in include.split(",") if item.strip()}
    if not requested or not requested <= {
        "schema",
        "constraints",
        "relationships",
        "data",
        "transitions",
    }:
        raise typer.BadParameter(
            "include supports schema, constraints, relationships, data, and transitions"
        )
    if relationship_depth > 5:
        raise typer.BadParameter("relationship-depth must be at most 5")


def _included_sections(spec: dict, include: str) -> dict:
    requested = {item.strip() for item in include.split(",")}
    result = dict(spec)
    if "data" not in requested:
        result["managed_rows"] = []
        result["transformations"] = []
        result["validations"] = []
    if "transitions" not in requested:
        result["transitions"] = []
    result["model_schema"] = (
        [
            {
                key: value
                for key, value in table.items()
                if key in {"name", "schema"}
                or (key == "columns" and "schema" in requested)
                or (
                    key in {"unique_constraints", "check_constraints"}
                    and "constraints" in requested
                )
                or (key == "foreign_keys" and "relationships" in requested)
            }
            for table in spec.get("model_schema", [])
        ]
        if requested & {"schema", "constraints", "relationships"}
        else []
    )
    return result


def _filtered_plan(plan: dict, spec: dict) -> dict:
    ids = {
        item["declaration_id"] for kind in _DATA_KINDS for item in spec.get(kind, [])
    }
    operations = [
        item for item in plan["operations"] if item.get("declaration_id") in ids
    ]
    operation_ids = {item["id"] for item in operations}
    return {
        **plan,
        "data_spec": spec,
        "operations": operations,
        "data_execution": {
            direction: [step for step in steps if step["operation_id"] in operation_ids]
            for direction, steps in plan["data_execution"].items()
        },
    }


@data_app.command("render")
def render_data(
    database: str | None = typer.Option(None, "--database", "-d"),
    model: str | None = typer.Option(None, "--model"),
    format: str = typer.Option("text", "--format"),
    include: str = typer.Option(
        "schema,constraints,relationships,data,transitions", "--include"
    ),
    only_with_data: bool = typer.Option(False, "--only-with-data"),
    relationship_depth: int = typer.Option(0, "--relationship-depth", min=0),
    show_managed_values: bool = typer.Option(False, "--show-managed-values"),
    param: Annotated[list[str] | None, typer.Option("--param")] = None,
) -> None:
    if format not in {"text", "json", "mapping", "sql", "operations"}:
        raise typer.BadParameter(
            "format must be text, json, mapping, sql, or operations"
        )
    _view_options(include, relationship_depth)
    _, _, spec, plan = _compiled(database, _parameters(param), frozen_fallback=True)
    plan = _filtered_plan(
        plan, _included_sections(_filter_spec(spec, model, only_with_data), include)
    )
    _emit(plan, format, show_values=show_managed_values or format == "sql")


@data_app.command("describe")
def describe_data(
    database: str | None = typer.Option(None, "--database", "-d"),
    model: str | None = typer.Option(None, "--model"),
    format: str = typer.Option("text", "--format"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
    include: str = typer.Option(
        "schema,constraints,relationships,data,transitions", "--include"
    ),
    only_with_data: bool = typer.Option(False, "--only-with-data"),
    relationship_depth: int = typer.Option(0, "--relationship-depth", min=0),
    show_managed_values: bool = typer.Option(False, "--show-managed-values"),
    param: Annotated[list[str] | None, typer.Option("--param")] = None,
) -> None:
    if format not in {"text", "json", "markdown"}:
        raise typer.BadParameter("format must be text, json, or markdown")
    _view_options(include, relationship_depth)
    _, discovered, spec, _ = _compiled(
        database, _parameters(param), frozen_fallback=True
    )
    spec = _included_sections(_filter_spec(spec, model, only_with_data), include)
    if format == "markdown":
        body = _markdown_spec(
            spec, include, relationship_depth, show_managed_values, discovered.models
        )
    elif format == "json":
        body = json.dumps(
            _redact(spec, show_managed_values), indent=2, sort_keys=True, default=str
        )
    else:
        body = _describe_text(
            spec, include, relationship_depth, show_managed_values, discovered.models
        )
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(output, body + "\n")
    else:
        typer.echo(body)


@data_app.command("docs")
def docs_data(
    output: Annotated[Path, typer.Option("--output")],
    database: str | None = typer.Option(None, "--database", "-d"),
    diagrams: str | None = typer.Option(None, "--diagrams"),
    show_managed_values: bool = typer.Option(False, "--show-managed-values"),
    param: Annotated[list[str] | None, typer.Option("--param")] = None,
) -> None:
    if diagrams not in {None, "mermaid"}:
        raise typer.BadParameter("diagrams supports only mermaid")
    _, discovered, spec, _ = _compiled(
        database, _parameters(param), frozen_fallback=True
    )
    output.mkdir(parents=True, exist_ok=True)
    models_dir, transitions_dir = output / "models", output / "transitions"
    models_dir.mkdir(exist_ok=True)
    transitions_dir.mkdir(exist_ok=True)
    files: dict[str, str] = {}
    model_items: dict[str, dict[str, list[dict]]] = {}
    for kind in ("managed_rows", "transformations", "validations"):
        for item in spec.get(kind, []):
            table = item["target_table"]["table"]
            model_items.setdefault(
                table, {"managed_rows": [], "transformations": [], "validations": []}
            )[kind].append(item)
    for table, items in model_items.items():
        path = models_dir / f"{_safe_name(table)}.md"
        atomic_write_text(
            path,
            _markdown_spec(
                {**_filter_spec(spec, table, False), **items, "transitions": []},
                "schema,constraints,relationships,data",
                1,
                show_managed_values,
                discovered.models,
            )
            + "\n",
        )
        files[str(path.relative_to(output))] = _sha(path)
    for item in spec["transitions"]:
        path = transitions_dir / f"{item['transition_id'][:16]}.md"
        atomic_write_text(
            path,
            _markdown_spec(
                {
                    **spec,
                    "managed_rows": [],
                    "transformations": [],
                    "validations": [],
                    "transitions": [item],
                },
                "schema,constraints,relationships,data,transitions",
                1,
                show_managed_values,
                discovered.models,
                diagrams,
            )
            + "\n",
        )
        files[str(path.relative_to(output))] = _sha(path)
    index = (
        "# DBWarden data declarations\n\n"
        + "\n".join(f"- `{name}`" for name in sorted(files))
        + "\n"
    )
    atomic_write_text(output / "index.md", index)
    files["index.md"] = _sha(output / "index.md")
    atomic_write_text(
        output / "checksums.json", json.dumps(files, indent=2, sort_keys=True) + "\n"
    )
    typer.echo(str(output))


def _transition(plan: dict, name: str) -> dict:
    matches = [
        item
        for item in plan["data_spec"]["transitions"]
        if item["declaration_id"] == name or item["declaration_id"].endswith(name)
    ]
    if len(matches) != 1:
        raise typer.BadParameter(f"Transition '{name}' is missing or ambiguous")
    transition = matches[0]
    ids = {
        operation["id"]
        for operation in plan["operations"]
        if operation.get("declaration_id") == transition["declaration_id"]
    }
    return {
        **plan,
        "data_spec": {
            **plan["data_spec"],
            "managed_rows": [],
            "transformations": [],
            "transitions": [transition],
        },
        "operations": [item for item in plan["operations"] if item["id"] in ids],
        "data_execution": {
            key: [step for step in steps if step["operation_id"] in ids]
            for key, steps in plan["data_execution"].items()
        },
    }


@transition_app.command("validate")
def validate_transition(
    name: str | None = typer.Argument(None),
    database: str | None = typer.Option(None, "--database", "-d"),
    param: Annotated[list[str] | None, typer.Option("--param")] = None,
) -> None:
    _, _, _, plan = _compiled(database, _parameters(param))
    if name:
        _transition(plan, name)
    typer.echo("Data declarations are valid.")


@transition_app.command("plan")
def plan_transition(
    name: str,
    database: str | None = typer.Option(None, "--database", "-d"),
    format: str = typer.Option("text", "--format"),
    param: Annotated[list[str] | None, typer.Option("--param")] = None,
) -> None:
    if format not in {"text", "json", "sql", "operations", "mapping"}:
        raise typer.BadParameter("Unsupported plan format")
    _, _, _, plan = _compiled(database, _parameters(param))
    _emit(_transition(plan, name), format)


@transition_app.command("dry-run")
def dry_run_transition(
    name: str,
    database: str | None = typer.Option(None, "--database", "-d"),
    probes: bool = typer.Option(False, "--probes"),
    param: Annotated[list[str] | None, typer.Option("--param")] = None,
) -> None:
    _, _, _, plan = _compiled(database, _parameters(param))
    selected = _transition(plan, name)
    if probes:
        from dbwarden.connection.connection import get_db_connection

        with get_db_connection(database) as connection:
            preview = dry_run_data_plan(selected, connection)
    else:
        preview = dry_run_data_plan(selected)
    typer.echo(
        json.dumps(
            preview,
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


@transition_app.command("render")
def render_transition(
    name: str,
    database: str | None = typer.Option(None, "--database", "-d"),
    format: str = typer.Option("text", "--format"),
    show_managed_values: bool = typer.Option(False, "--show-managed-values"),
    param: Annotated[list[str] | None, typer.Option("--param")] = None,
) -> None:
    _, _, _, plan = _compiled(database, _parameters(param), frozen_fallback=True)
    _emit(
        _transition(plan, name),
        format,
        show_values=show_managed_values or format == "sql",
    )


@transition_app.command("describe")
def describe_transition(
    name: str,
    database: str | None = typer.Option(None, "--database", "-d"),
    format: str = typer.Option("text", "--format"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
    param: Annotated[list[str] | None, typer.Option("--param")] = None,
) -> None:
    if format not in {"text", "json", "markdown"}:
        raise typer.BadParameter("format must be text, json, or markdown")
    _, _, _, plan = _compiled(database, _parameters(param), frozen_fallback=True)
    selected = _transition(plan, name)["data_spec"]
    body = (
        _markdown_spec(selected, "data", 0, False)
        if format == "markdown"
        else json.dumps(_redact(selected, False), indent=2, sort_keys=True)
        if format == "json"
        else _describe_text(selected, "data", 0, False)
    )
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(output, body + "\n")
    else:
        typer.echo(body)


@transition_app.command("audit")
def audit_transition(
    name: str,
    database: str | None = typer.Option(None, "--database", "-d"),
    format: str = typer.Option("json", "--format"),
) -> None:
    if format not in {"json", "text"}:
        raise typer.BadParameter("format must be json or text")
    from dbwarden.engine.version import get_migrations_directory

    directory = Path(get_migrations_directory(database))
    files = [path for path in directory.glob("*.data.py") if name in path.stem]
    if len(files) != 1:
        raise typer.BadParameter(f"Frozen artifact '{name}' is missing or ambiguous")
    from dbwarden.data.artifacts import verify_bundle
    from dbwarden.data.frozen import read_frozen

    bundle = verify_bundle(files[0].with_suffix("").with_suffix(".sql"))
    frozen = read_frozen(files[0])
    tree = ast.parse(files[0].read_text(encoding="utf-8"), filename=str(files[0]))
    assignments = [
        node.targets[0].id
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ]
    payload = {
        "path": str(files[0]),
        "assignments": assignments,
        "sha256": _sha(files[0]),
        "imported": False,
        "bundle": bundle,
        "declaration_checksum": frozen.get("canonical_checksum"),
        "transitions": [
            {
                "id": item["declaration_id"],
                "coverage": item["coverage"],
                "rollback": item["rollback"],
            }
            for item in frozen.get("transitions", [])
        ],
    }
    typer.echo(
        json.dumps(payload, indent=2, sort_keys=True)
        if format == "json"
        else f"{files[0]}\nassignments: {', '.join(assignments)}"
    )


@transition_app.command("reconcile")
def reconcile_transition(
    migration_id: str,
    database: str | None = typer.Option(None, "--database", "-d"),
    apply: bool = typer.Option(False, "--apply"),
    decision: str | None = typer.Option(None, "--decision"),
) -> None:
    if apply and decision not in {"abandon", "verified_retry"}:
        raise typer.BadParameter(
            "--apply requires --decision abandon or verified_retry"
        )
    if decision and not apply:
        raise typer.BadParameter("--decision requires --apply")
    from dbwarden.connection.connection import get_db_connection
    from dbwarden.data.execution import reconcile_data_plan

    plan = None
    if apply:
        from dbwarden.data.integration import load_data_plan
        from dbwarden.engine.version import get_migrations_directory

        files = [
            path
            for root in (
                Path(get_migrations_directory(database)),
                Path(".dbwarden/reconciliations"),
            )
            for path in root.rglob("*.sql")
            if path.stem == migration_id
        ]
        if len(files) != 1 or (plan := load_data_plan(files[0], database)) is None:
            raise typer.BadParameter(
                "Reconciliation requires one verified frozen migration bundle"
            )

    if not apply:
        with get_db_connection(database) as connection:
            result = reconcile_data_plan(connection, migration_id)
    else:
        from dbwarden.lock import acquire_lock, release_lock
        from dbwarden.repositories import create_lock_table_if_not_exists

        create_lock_table_if_not_exists(database)
        lock = acquire_lock(database)
        if not lock.acquired:
            raise RuntimeError(f"Migration lock unavailable: {lock.holder_description}")
        try:
            if lock.connection is not None:
                result = reconcile_data_plan(
                    lock.connection,
                    migration_id,
                    apply=True,
                    decision=decision,
                    plan=plan,
                )
                lock.connection.commit()
            else:
                with get_db_connection(database) as connection:
                    result = reconcile_data_plan(
                        connection,
                        migration_id,
                        apply=True,
                        decision=decision,
                        plan=plan,
                    )
        finally:
            release_lock(database, strategy=lock.strategy)
    typer.echo(json.dumps(result, indent=2, sort_keys=True, default=str))


@transition_app.command("new")
def new_transition(
    name: str,
    database: str | None = typer.Option(None, "--database", "-d"),
    manual: bool = typer.Option(False, "--manual"),
    from_table: str | None = typer.Option(None, "--from-table"),
    from_model: str | None = typer.Option(None, "--from-model"),
    source_snapshot: str | None = typer.Option(None, "--source-snapshot"),
    to_model: Annotated[list[str] | None, typer.Option("--to-model")] = None,
    map: Annotated[list[str] | None, typer.Option("--map")] = None,
    source_key: str | None = typer.Option(None, "--source-key"),
    key: Annotated[list[str] | None, typer.Option("--key")] = None,
    where: Annotated[list[str] | None, typer.Option("--where")] = None,
    preserve_source: bool = typer.Option(True, "--preserve-source/--drop-source"),
    acknowledge_drop: bool = typer.Option(False, "--acknowledge-drop"),
    coverage: str = typer.Option("all", "--coverage"),
    overlap: str = typer.Option("error", "--overlap"),
    priority: Annotated[list[str] | None, typer.Option("--priority")] = None,
) -> None:
    to_model, map, key, where = to_model or [], map or [], key or [], where or []
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise typer.BadParameter("name must be an identifier")
    direct = bool(
        from_table
        or from_model
        or source_snapshot
        or to_model
        or map
        or source_key
        or key
        or where
        or priority
    )
    if manual and direct:
        raise typer.BadParameter(
            "Use --manual alone, or supply a complete direct transition"
        )
    if not manual and not direct and sys.stdin.isatty():
        from_table = typer.prompt("Historical source table")
        source_snapshot = typer.prompt("Pinned snapshot ID")
        to_model = [
            value.strip()
            for value in typer.prompt(
                "Target models (module:Class, comma separated)"
            ).split(",")
        ]
        source_key = typer.prompt("Source identity columns (comma separated)")
        key = [typer.prompt("Target identity columns (Model.column, comma separated)")]
        map = [
            value.strip()
            for value in typer.prompt(
                "Mappings (source:Model.column, comma separated)"
            ).split(",")
        ]
    from dbwarden.data.declarations import _normalize_transition_coverage

    try:
        coverage = _normalize_transition_coverage(coverage, overlap)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not preserve_source and not acknowledge_drop:
        raise typer.BadParameter("--drop-source requires --acknowledge-drop")
    from dbwarden.config import get_database

    config = get_database(database)
    if not config.data_paths:
        raise typer.BadParameter(
            "Configure data_paths before creating a live transition"
        )
    destination = Path(config.data_paths[0]) / f"{name}.py"
    if (
        Path(config.data_paths[0]).suffix == ".py"
        or not destination.resolve().is_relative_to(Path.cwd().resolve())
        or destination.is_symlink()
    ):
        raise typer.BadParameter(
            "new requires a data_paths directory inside the project"
        )
    if destination.exists():
        raise typer.BadParameter(f"Live declaration already exists: {destination}")
    if manual:
        source = (
            "# Fill source snapshot, model, and mappings; remove this draft guard when ready.\nif False:\n    from models import Target\n    from dbwarden.data import DataTransition, historical_table, into\n\n    source = historical_table('legacy_table', snapshot='primary__0001')\n\n    class "
            + name
            + "(DataTransition):\n        source = source\n        source_identity = [source.id]\n        coverage = 'all'\n        on_complete = 'preserve'\n        rollback = 'restore_preserved_source'\n        targets = [into(Target, map={Target.id: source.id}, key=[Target.id])]\n"
        )
    else:
        if from_model:
            if from_table:
                raise typer.BadParameter("Choose --from-model or --from-table")
            import importlib

            module, klass = _model_ref(from_model)
            model = getattr(importlib.import_module(module), klass)
            from_table = model.__table__.fullname
        if not (
            from_table and source_snapshot and to_model and map and source_key and key
        ):
            raise typer.BadParameter(
                "Direct transition requires a source, --source-snapshot, --to-model, --map, --source-key, and --key"
            )
        targets = [_model_ref(value) for value in to_model]
        classes = [klass for _, klass in targets]
        if len(set(classes)) != len(classes):
            raise typer.BadParameter("Target model class names must be distinct")
        mappings = {klass: [] for klass in classes}
        keys = {klass: [] for klass in classes}
        predicates = {klass: "None" for klass in classes}
        priorities = {}
        for value in priority or []:
            klass, marker, number = value.partition(":")
            if (
                not marker
                or klass not in classes
                or klass in priorities
                or not re.fullmatch(r"-?\d+", number)
            ):
                raise typer.BadParameter(
                    "--priority requires distinct Model:INTEGER entries"
                )
            priorities[klass] = int(number)
        if overlap == "priority":
            if set(priorities) != set(classes) or len(set(priorities.values())) != len(
                classes
            ):
                raise typer.BadParameter(
                    "Priority overlap requires one distinct --priority value per target"
                )
        elif priorities:
            raise typer.BadParameter("--priority requires --overlap priority")
        for value in map:
            source_column, marker, target = value.partition(":")
            if not marker:
                raise typer.BadParameter("--map requires source:Model.column")
            klass, column = _target_column(target, classes)
            _maps([source_column + ":" + column])
            mappings[klass].append((source_column, column))
        for value in (column for group in key for column in group.split(",")):
            klass, column = _target_column(value.strip(), classes)
            keys[klass].append(column)
        for value in where:
            if len(classes) == 1 and ":" not in value:
                klass, predicate = classes[0], value
            else:
                klass, marker, predicate = value.partition(":")
                if not marker or klass not in classes:
                    raise typer.BadParameter(
                        "Multi-target --where requires Model:column=value"
                    )
            predicates[klass] = _where(predicate, from_table)
        source_identity = [value.strip() for value in source_key.split(",")]
        if any(not value.isidentifier() for value in source_identity):
            raise typer.BadParameter("Source identity must contain column identifiers")
        lines = [f"from {module} import {klass}" for module, klass in targets]
        lines += [
            "from dbwarden.data import DataTransition, historical_table, into",
            "",
            "",
        ]
        schema, _, table = from_table.rpartition(".")
        lines += [
            f"source = historical_table({table!r}, snapshot={source_snapshot!r}, schema={schema or None!r})",
            "",
            "",
            f"class {name}(DataTransition):",
            "    source = source",
            f"    source_identity = {source_identity!r}",
            f"    coverage = {coverage!r}",
            f"    overlap = {overlap!r}",
            f"    on_unmatched = {'ignore' if coverage == 'subset' else 'error'!r}",
            f"    on_complete = {'preserve' if preserve_source else 'drop'!r}",
            f"    acknowledge_drop = {acknowledge_drop!r}",
            f"    rollback = {'restore_preserved_source' if preserve_source else 'irreversible'!r}",
            "    targets = [",
        ]
        for klass in classes:
            if not mappings[klass] or not keys[klass]:
                raise typer.BadParameter(f"Missing mappings or identity for {klass}")
            _maps([left + ":" + right for left, right in mappings[klass]])
            pairs = ", ".join(
                f"{klass}.{right}: source.{left}" for left, right in mappings[klass]
            )
            lines.append(
                f"        into({klass}, map={{{pairs}}}, key={keys[klass]!r}, where={predicates[klass]}, priority={priorities.get(klass)!r}),"
            )
        source = "\n".join([*lines, "    ]", ""])
    destination.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(destination, source)
    typer.echo(str(destination))


def _model_ref(value):
    module, separator, klass = value.partition(":")
    if not separator or any(
        not part.isidentifier() for part in module.split(".") + [klass]
    ):
        raise typer.BadParameter("Model references must be module:Class")
    return module, klass


def _target_column(value, classes):
    klass, separator, column = value.partition(".")
    if not separator and len(classes) == 1:
        klass, column = classes[0], klass
    if klass not in classes or not column.isidentifier():
        raise typer.BadParameter(
            "Target columns must be Model.column (column alone for one target)"
        )
    return klass, column


def _maps(values: list[str]) -> list[tuple[str, str]]:
    result = []
    for value in values:
        source, marker, target = value.partition(":")
        if (
            not marker
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", source)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target)
        ):
            raise typer.BadParameter("--map must use source:target identifiers")
        result.append((source, target))
    if len({target for _, target in result}) != len(result):
        raise typer.BadParameter("--map target columns must be distinct")
    return result


def _where(value: str, source: str) -> str:
    match = re.fullmatch(
        r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:'([^']*)'|(\d+)|true|false|null)\s*",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        raise typer.BadParameter(
            "--where supports only column = string, integer, true, false, or null; raw SQL is not accepted"
        )
    column, string, integer = match.group(1), match.group(2), match.group(3)
    literal = (
        repr(string)
        if string is not None
        else integer
        if integer is not None
        else value.rsplit("=", 1)[1]
        .strip()
        .lower()
        .replace("true", "True")
        .replace("false", "False")
        .replace("null", "None")
    )
    return f"source.{column} == {literal}"


def _describe_text(
    spec: dict, include: str, relationship_depth: int, show_values: bool, models=()
) -> str:
    return (
        _markdown_spec(spec, include, relationship_depth, show_values, models)
        .replace("# ", "")
        .replace("## ", "")
    )


def _markdown_spec(
    spec: dict,
    include: str,
    relationship_depth: int,
    show_values: bool,
    models=(),
    diagrams=None,
) -> str:
    data = _redact(spec, show_values)
    lines = [
        "# DBWarden data declarations",
        "",
        f"Database: `{data['database']['name']}`",
        "",
    ]
    requested = {item.strip() for item in include.split(",")}
    if requested & {"schema", "constraints", "relationships"}:
        if models:
            selected = {
                (table.get("schema"), table["name"])
                for table in spec.get("model_schema", [])
            }
            visible_models = (
                [
                    model
                    for model in models
                    if (model.__table__.schema, model.__table__.name) in selected
                ]
                if "model_schema" in spec
                else models
            )
            lines.extend(_model_markdown(visible_models, requested, relationship_depth))
        else:
            lines.extend(
                _frozen_model_markdown(spec.get("model_schema", []), requested)
            )
    for kind in _DATA_KINDS:
        lines.extend([f"## {kind.replace('_', ' ').title()}", ""])
        for item in data.get(kind, []):
            lines.extend(
                [
                    f"### `{item['declaration_id']}`",
                    "",
                ]
            )
            if "rollback" in item:
                lines.extend(
                    [
                        f"Rollback: `{item['rollback']['policy']}`. Proof: `{item['rollback']['proof_status']}`.",
                        "",
                    ]
                )
            if kind == "managed_rows":
                lines.extend(
                    [
                        f"Table: `{item['target_table']['table']}`. Key: `{', '.join(item['key_columns'])}`.",
                        f"Owned columns: `{', '.join(item['owned_columns'])}`. Missing rows: `{item['on_missing']}`.",
                        f"Input: `{item['row_source']['kind']}`; path: `{item['row_source']['path']}`; checksum: `{item['row_source']['checksum']}`.",
                    ]
                )
                if show_values:
                    lines.extend(
                        [
                            "",
                            "```json",
                            json.dumps(
                                item["row_source"]["rows"], indent=2, ensure_ascii=False
                            ),
                            "```",
                        ]
                    )
            elif kind == "transformations":
                lines.extend(
                    [
                        f"Target: `{item['target_table']['table']}.{item['target_columns'][0]}`.",
                        f"Determinism: `{item['expression']['determinism_class']}`. Unmatched rows: `{item['on_unmatched']}`.",
                        f"Row limit: `{item['max_rows']}`.",
                        "",
                        "Expression:",
                        "",
                        "```json",
                        json.dumps(
                            item["expression"]["canonical_ast"],
                            indent=2,
                            ensure_ascii=False,
                        ),
                        "```",
                    ]
                )
            elif kind == "validations":
                for validation in item["validations"]:
                    lines.extend(
                        [
                            f"- {validation['message']}",
                            "",
                            "```json",
                            json.dumps(
                                validation["expression"]["canonical_ast"], indent=2
                            ),
                            "```",
                            "",
                        ]
                    )
            else:
                lines.extend(
                    [
                        f"Source: `{item['source']['table']}` at snapshot `{item['source_snapshot']['snapshot_id']}`.",
                        f"Transition ID: `{item['transition_id']}`.",
                        f"Identity: `{', '.join(item['identity']['source_identity'])}`; cardinality: `{item['identity']['relationship_cardinality']}`.",
                        f"Coverage: `{item['coverage']['mode']}`. Overlap: `{item['coverage']['overlap_policy']}`. Unmatched: `{item['coverage']['on_unmatched']}`.",
                        f"Source completion: `{item['completion']['policy']}`; preserved table: `{item['rollback']['preservation_ref']}`.",
                    ]
                )
                for target in item["targets"]:
                    lines.extend(
                        [
                            "",
                            f"Target `{target['target_table']['table']}`: key `{', '.join(target['identity'])}`, conflicts `{target['conflict']['policy']}`.",
                            "",
                            "```json",
                            json.dumps(
                                target["mappings"], indent=2, ensure_ascii=False
                            ),
                            "```",
                        ]
                    )
            lines.append("")
        if not data.get(kind):
            lines.append("- None")
        lines.append("")
    if diagrams == "mermaid":
        lines.extend(["## Relationship diagram", "", "```mermaid", "graph LR"])
        for model in models:
            for relation in model.__mapper__.relationships:
                lines.append(
                    f"  {model.__tablename__} --> {relation.mapper.class_.__tablename__}"
                )
        for index, item in enumerate(data["transitions"]):
            source = json.dumps(item["source"]["table"])
            for target_index, target in enumerate(item["targets"]):
                destination = json.dumps(target["target_table"]["table"])
                lines.append(
                    f"  source{index}[{source}] --> target{index}_{target_index}[{destination}]"
                )
        if not models:
            for index, table in enumerate(spec.get("model_schema", [])):
                for fk_index, foreign_key in enumerate(table["foreign_keys"]):
                    lines.append(
                        f"  table{index}[{json.dumps(table['name'])}] --> fk{index}_{fk_index}[{json.dumps(foreign_key['target']['table'])}]"
                    )
        lines.extend(["```", ""])
    return "\n".join(lines)


def _frozen_model_markdown(tables, requested):
    lines = ["## Models", ""]
    for table in tables:
        name = ".".join(part for part in (table["schema"], table["name"]) if part)
        lines.extend([f"### {name}", ""])
        if "schema" in requested:
            lines.extend(
                ["| Column | Type | Null | Primary key |", "|---|---|---|---|"]
            )
            for column in table["columns"]:
                lines.append(
                    f"| {column['name']} | {column['type']} | {'yes' if column['nullable'] else 'no'} | {'yes' if column['primary_key'] else 'no'} |"
                )
        if "constraints" in requested:
            for key in ("unique_constraints", "check_constraints"):
                for constraint in table[key]:
                    lines.append(f"- {key}: `{json.dumps(constraint, sort_keys=True)}`")
        if "relationships" in requested:
            for foreign_key in table["foreign_keys"]:
                lines.append(
                    f"- `{', '.join(foreign_key['columns'])}` → `{foreign_key['target']['table']}.{', '.join(foreign_key['target']['columns'])}`"
                )
        lines.append("")
    return lines


def _model_markdown(models, requested, depth):
    lines = ["## Models", ""]
    seen = set()

    def visit(model, remaining):
        if model.__tablename__ in seen:
            return
        seen.add(model.__tablename__)
        lines.extend([f"### {model.__tablename__}", ""])
        if "schema" in requested:
            lines.extend(["| Column | Type | Null | Default |", "|---|---|---|---|"])
            for column in model.__table__.columns:
                default = getattr(getattr(column, "server_default", None), "arg", "")
                lines.append(
                    f"| {column.name} | {column.type} | {'yes' if column.nullable else 'no'} | {default} |"
                )
        if "constraints" in requested:
            lines.extend(
                [
                    "",
                    "Constraints:",
                    *[f"- {item}" for item in model.__table__.constraints],
                ]
            )
        if "relationships" in requested:
            lines.extend(["", "Relationships:"])
            for relation in model.__mapper__.relationships:
                lines.append(
                    f"- {relation.key}: {relation.mapper.class_.__tablename__} ({relation.direction.name.lower()})"
                )
                if remaining:
                    visit(relation.mapper.class_, remaining - 1)
        lines.append("")

    for model in models:
        visit(model, depth)
    return lines


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "data"
