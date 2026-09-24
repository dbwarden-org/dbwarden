"""dbwarden merge command.

Reconciles divergent migration histories after a branch merge.
Implements the merge spec §5.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from dbwarden import __version__
from dbwarden.commands.make_migrations.generation import (
    _artifact_paths,
    _write_artifacts,
    diff_states,
    generate_files,
)
from dbwarden.commands.make_migrations.pipeline import get_model_state_path
from dbwarden.config import get_database, get_multi_db_config
from dbwarden.engine.generation_state import (
    configuration_state,
    schema_state,
    state_checksum,
)
from dbwarden.engine.safety.plans import read_trusted_plan
from dbwarden.engine.version import (
    MIGRATION_PATTERN,
    get_migrations_directory,
    get_next_migration_number,
)
from dbwarden.files import atomic_write_text, preserve_files_on_error
from dbwarden.merge.git_utils import (
    _run_git,
    get_file_at_commit,
    get_merge_base,
    has_conflict_markers,
    is_clean_working_tree,
)
from dbwarden.merge.marker import is_superseded, mark_file_superseded
from dbwarden.merge.reconciliation import (
    ReconciliationHeader,
    format_reconciliation_header,
    is_reconciliation,
)
from dbwarden.output import emit_json, error, info, success


def _repo_path(path: Path) -> str:
    rc, root, _ = _run_git(["rev-parse", "--show-toplevel"])
    if rc:
        raise ValueError(
            "Cannot find Git root. hint: run merge from the project repository"
        )
    return path.resolve().relative_to(Path(root).resolve()).as_posix()


def _check_preconditions(database: str | None) -> bool:
    if not is_clean_working_tree():
        error("Working tree is not clean. hint: commit or stash changes before merge")
        return False
    try:
        candidate_paths = [
            *Path(get_migrations_directory(database)).glob("*.sql"),
            get_model_state_path(database),
        ]
    except Exception:
        # Outside a configured project there is nothing to scan for markers.
        candidate_paths = []
    for path in candidate_paths:
        if path.exists() and has_conflict_markers(str(path)):
            error(f"Conflict markers in {path}. hint: resolve them first")
            return False
    if get_merge_base() is None:
        error("Cannot resolve merge base. hint: run after committing the Git merge")
        return False
    return True


def _find_superseded_files(migrations_dir: str, merge_base: str) -> list[str]:
    candidates = []
    for path in sorted(Path(migrations_dir).glob("*.sql")):
        if (
            not MIGRATION_PATTERN.match(path.name)
            or is_superseded(path)
            or is_reconciliation(path)
        ):
            continue
        if get_file_at_commit(merge_base, _repo_path(path)) is None:
            candidates.append(path.name)
    return candidates


def _get_merge_base_state(merge_base: str, database: str | None) -> dict:
    for path in (
        get_model_state_path(database),
        get_model_state_path(database, legacy=True),
    ):
        content = get_file_at_commit(merge_base, _repo_path(path))
        if content is not None:
            value = json.loads(content)
            if not isinstance(value, dict) or not isinstance(value.get("tables"), dict):
                raise ValueError(
                    "Malformed merge-base model state. hint: restore it in Git"
                )
            return value
    raise ValueError(
        "No model state at merge base. hint: export and commit model state before branching"
    )


def _rebuild_current_state(database: str | None) -> dict:
    from dbwarden.engine.core.model_state import model_state_to_dict
    from dbwarden.engine.discovery import (
        auto_discover_model_paths,
        filter_model_tables_by_name,
        get_all_model_tables,
        validate_model_tables_exist,
    )

    config = get_database(database)
    paths = config.model_paths or auto_discover_model_paths()
    if not paths:
        raise ValueError("No model paths. hint: configure model_paths before merging")
    tables = get_all_model_tables(paths, db_name=database)
    validate_model_tables_exist(
        tables, config.model_tables, database or get_multi_db_config().default
    )
    return configuration_state(
        model_state_to_dict(filter_model_tables_by_name(tables, config.model_tables)),
        config,
        desired=True,
    )


def _probe_persistent_environments(
    database: str | None, superseded_versions: set[str]
) -> dict[str, str]:
    from sqlalchemy.exc import SQLAlchemyError

    from dbwarden.connection.connection import sandbox_override
    from dbwarden.merge.environments import load_environments
    from dbwarden.repositories import get_migrated_versions, migrations_table_exists

    results = {}
    for env in load_environments(database):
        if not env.persistent:
            continue
        url = os.environ.get(env.url_env or "")
        results[env.name] = "unknown"
        if url:
            try:
                with sandbox_override(url, get_database(database).database_type):
                    applied = (
                        set(get_migrated_versions(database))
                        if migrations_table_exists(database)
                        else set()
                    )
                    results[env.name] = (
                        "dirty" if applied & superseded_versions else "clean"
                    )
            except (SQLAlchemyError, OSError, ValueError):
                pass
    return results


def merge_cmd(
    database: str | None = None,
    rename_columns: list[str] | None = None,
    rename_tables: list[str] | None = None,
    force: bool = False,
    commit: bool = False,
    json_output: bool = False,
    verbose: bool = False,
    dry_run: bool = False,
    split_at_severity: str | None = None,
) -> None:
    from dbwarden.engine.core.model_state import model_state_json_dumps
    from dbwarden.merge.rename_capture import harvest_rename_intents

    if not _check_preconditions(database):
        raise ValueError("Merge preconditions failed")
    db_name = database or get_multi_db_config().default
    config = get_database(db_name)
    merge_base = get_merge_base()
    if merge_base is None:
        raise ValueError(
            "No merge base. hint: merge branches before running dbwarden merge"
        )
    directory = Path(get_migrations_directory(db_name))
    recorded_base = _get_merge_base_state(merge_base, db_name)
    baseline = configuration_state(recorded_base, config)
    target = _rebuild_current_state(db_name)
    names = _find_superseded_files(str(directory), merge_base)
    edited = [name for name in names if read_trusted_plan(directory / name)[0] is None]
    if edited and not force:
        raise ValueError(
            f"Hand-edited or untrusted migrations: {', '.join(edited)}. hint: review them and pass --force"
        )
    columns = [value.replace("=", ":", 1) for value in rename_columns or []]
    tables = [value.replace("=", ":", 1) for value in rename_tables or []]
    for intent in harvest_rename_intents([directory / name for name in names]):
        old, new = intent.get("from"), intent.get("to")
        if not isinstance(old, str) or not isinstance(new, str):
            raise TypeError(
                "Malformed recorded rename. hint: repair the branch migration"
            )
        if intent.get("type") == "table":
            tables.append(f"{old}:{new}")
        else:
            table, column = old.rsplit(".", 1)
            new_table, new_column = new.rsplit(".", 1)
            if table != new_table:
                raise ValueError("Cross-table column rename is unsupported")
            columns.append(f"{table}.{column}:{new_column}")
    upgrade, rollback = diff_states(
        baseline,
        target,
        db_name=db_name,
        rename_columns=sorted(set(columns)),
        rename_tables=sorted(set(tables)),
    )
    from dbwarden.data.compiler import compile_data, discover_data
    from dbwarden.data.planning import plan_data, previous_data_spec
    from dbwarden.engine.discovery import auto_discover_model_paths

    paths = config.model_paths or auto_discover_model_paths()
    data_spec = compile_data(
        discover_data(paths, config.data_paths, model_tables=config.model_tables),
        database=db_name,
        backend=config.database_type,
        registry_path=config.snapshot_registry,
    )
    previous_data = previous_data_spec(
        {
            path.name: path
            for path in directory.glob("*.sql")
            if MIGRATION_PATTERN.match(path.name) and path.name not in names
        },
        database=db_name,
        backend=config.database_type,
    )
    data_ops = plan_data(data_spec, previous_data)
    from dbwarden.commands.make_migrations.generation import (
        append_data_contract,
        data_expansion_state,
        include_data_operations,
    )

    expanded = data_expansion_state(baseline, target, data_ops)
    if expanded != target:
        upgrade, rollback = diff_states(baseline, expanded, db_name=db_name, rename_columns=sorted(set(columns)), rename_tables=sorted(set(tables)))
    upgrade = include_data_operations(upgrade, data_ops)
    append_data_contract(upgrade, baseline, expanded, target, db_name=db_name)
    versions = {
        match.group(1) for name in names if (match := MIGRATION_PATTERN.match(name))
    }
    probes = _probe_persistent_environments(db_name, versions)
    rc, base_files, detail = _run_git(
        ["ls-tree", "-r", "--name-only", merge_base, "--", _repo_path(directory)]
    )
    if rc:
        raise ValueError(f"Cannot list merge-base migrations: {detail}")
    base_version = max(
        (
            match.group(1)
            for line in base_files.splitlines()
            if (match := MIGRATION_PATTERN.match(Path(line).name))
        ),
        default="0000",
    )
    header = ReconciliationHeader(
        merge_base,
        state_checksum(baseline),
        names,
        probes,
        f"dbwarden merge (dbwarden {__version__})",
    )
    artifacts = generate_files(
        upgrade,
        rollback,
        migrations_dir=str(directory),
        database=db_name,
        db_name=db_name,
        description=f"merge reconciliation {merge_base[:8]}",
        baseline=baseline,
        target=target,
        threshold=split_at_severity
        if split_at_severity is not None
        else config.split_at_severity,
        header=format_reconciliation_header(header),
        write=False,
        data_spec=data_spec,
    )
    reconciliation_versions = [artifact["version"] for artifact in artifacts]
    record_id = (
        reconciliation_versions[0]
        if reconciliation_versions
        else get_next_migration_number(str(directory))
    )
    report = {
        "version": record_id,
        "database": db_name,
        "merge_base": merge_base,
        "merge_base_version": base_version,
        "merge_base_checksum": state_checksum(baseline),
        "superseded_files": names,
        "superseded_versions": sorted(versions),
        "reconciliation_version": reconciliation_versions[0]
        if reconciliation_versions
        else None,
        "reconciliation_versions": reconciliation_versions,
        "reconciliation_files": [artifact["filename"] for artifact in artifacts],
        "probe_results": probes,
        "forced": force,
        "generated_by": f"dbwarden {__version__}",
        "no_op": not artifacts,
    }
    if json_output:
        emit_json(
            {
                **report,
                "dry_run": dry_run,
                "plans": [artifact["plan"] for artifact in artifacts],
            }
        )
    else:
        info(
            f"{'Would supersede' if dry_run else 'Superseding'}: {', '.join(names) or 'none'}"
        )
        for artifact in artifacts:
            info(
                f"{artifact['filename']}: {len(artifact['plan']['upgrade_ops'])} operations, severity {artifact['plan']['severity']['file']}"
            )
        info(
            "Environment status: "
            + (
                ", ".join(f"{name}={status}" for name, status in probes.items())
                or "none registered"
            )
        )
    if dry_run:
        return
    record_path = (
        Path(".dbwarden/merges") / f"{db_name}__{record_id}_{merge_base[:8]}.json"
    )
    state = {
        **target,
        "generation_base": schema_state(
            recorded_base.get("generation_base", recorded_base)
        ),
        "generation_applied": recorded_base.get("generation_applied", []),
        "generation_versions": sorted(
            set(recorded_base.get("generation_versions", []))
            | set(reconciliation_versions)
        ),
    }
    state_paths = {
        get_model_state_path(db_name),
        get_model_state_path(db_name, legacy=True),
    }
    changed = [
        *[directory / name for name in names],
        *[
            (directory / name).with_suffix(".superseded.json")
            for name in names
            if _is_data_bundle(directory / name)
        ],
        record_path,
        *state_paths,
        *_artifact_paths(artifacts, directory),
    ]
    with preserve_files_on_error(changed):
        _write_artifacts(artifacts, directory)
        for name in names:
            mark_file_superseded(
                directory / name,
                merged_into=record_id if artifacts else "none",
                merge_base=merge_base,
                branch="merge",
                applied_persistent=",".join(
                    name for name, status in probes.items() if status != "clean"
                )
                or "none",
            )
        atomic_write_text(
            record_path, json.dumps(report, sort_keys=True, indent=2) + "\n"
        )
        for state_path in state_paths:
            atomic_write_text(state_path, model_state_json_dumps(state))
    if commit:
        subprocess.run(
            ["git", "add", "--", *[str(path) for path in changed]],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"dbwarden merge: reconcile {db_name}"],
            check=True,
            capture_output=True,
        )
    success(
        "Merge reconciliation written. Dirty persistent environments require dbwarden reconcile."
    )


def _is_data_bundle(path: Path) -> bool:
    try:
        return isinstance(
            json.loads(path.with_suffix(".plan.json").read_text(encoding="utf-8")).get(
                "data_bundle"
            ),
            dict,
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
