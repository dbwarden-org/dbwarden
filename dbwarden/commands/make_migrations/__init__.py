import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from dbwarden import __version__
from dbwarden.config import get_database, get_multi_db_config
from dbwarden.constants import RUNS_ALWAYS_FILE_PREFIX, RUNS_ON_CHANGE_FILE_PREFIX
from dbwarden.engine.discovery import (
    get_all_model_tables,
    auto_discover_model_paths,
    filter_model_tables_by_name,
    validate_model_tables_exist,
)
from dbwarden.engine.migration_name import Change
from dbwarden.engine.core.model_state import model_state_json_dumps
from dbwarden.engine.offline import model_state_to_dict
from dbwarden.engine.version import (
    get_migrations_directory,
    get_next_migration_number,
    generate_migration_filename,
    generate_repeatable_filename,
)
from dbwarden.logging import get_logger
from dbwarden.output import error, info, plain, success, sql, warning

from dbwarden.commands.make_migrations.ch_ops import (
    _check_recreate_rename_conflict,
    _resolve_clickhouse_recreate_ops,
)
from dbwarden.commands.make_migrations.cli_parsing import (
    RenameIntent,
    _format_rename_warning,
    _format_table_rename_warning,
    _parse_rename_flags,
    _parse_rename_table_flags,
    _validate_table_rename_intents,
)
from dbwarden.commands.make_migrations.migrate_plan import (
    _build_table_rename_ops,
    _check_migration_scope,
    _resolve_migration_description,
    build_migration_plan,
)
from dbwarden.commands.make_migrations.pipeline import (
    generate_migration_sql,
    get_current_model_state_path,
    get_model_state_path,
    get_pending_migration_statements,
    _build_domain_sql,
    _build_sequence_sql,
    _drop_domain_sql,
    _drop_sequence_sql,
    _run_offline_migrations,
)
from dbwarden.commands.make_migrations.prompts import (
    _detect_table_rename_candidates,
    _prompt_rename_confirmations,
    _prompt_table_rename_confirmations,
)


logger = get_logger()


def make_migrations_cmd(
    description: str | None = None,
    verbose: bool = False,
    database: str | None = None,
    output_plan: bool = False,
    output_sql: bool = False,
    rename_flags: list[str] | None = None,
    safe_type_change: bool = False,
    rename_table_flags: list[str] | None = None,
    concurrent: bool = True,
    offline: bool = False,
    migration_type: str = "versioned",
    clickhouse_engine_recreate: bool = False,
    drop_preserved_clickhouse_table: bool | None = None,
    postgres_auto_using: bool = False,
) -> None:
    logger = get_logger(verbose=verbose)

    if offline:
        _run_offline_migrations(
            description=description, database=database, migration_type=migration_type,
            clickhouse_engine_recreate=clickhouse_engine_recreate,
            drop_preserved_clickhouse_table=drop_preserved_clickhouse_table,
            rename_flags=rename_flags,
            rename_table_flags=rename_table_flags,
        )
        return

    config = get_database(database)
    multi_config = get_multi_db_config()
    db_name = database or multi_config.default
    model_paths = config.model_paths

    if model_paths is None:
        model_paths = auto_discover_model_paths()

    if not model_paths:
        logger.warning("No model paths found. Please set model_paths in warden.toml")
        warning(
            "No SQLAlchemy models found. Please:\n"
            "1. Create models/ directory with your SQLAlchemy models\n"
            "2. Or set model_paths in dbwarden config"
        )
        return

    logger.log_model_paths(model_paths)
    logger.info(f"Discovering models in: {model_paths}")
    tables = get_all_model_tables(model_paths, db_name=db_name)
    validate_model_tables_exist(tables, config.model_tables, db_name)
    tables = filter_model_tables_by_name(tables, config.model_tables)

    if not tables:
        logger.warning("No tables found in models")
        warning("No tables found in the specified model paths.")
        return

    for table in tables:
        logger.log_model_discovered(table.name, [c.name for c in table.columns])

    logger.info(f"Found {len(tables)} tables in models")

    rename_intents: list[RenameIntent] = []
    if rename_flags:
        rename_intents = _parse_rename_flags(rename_flags)

    confirmed_renames: set[tuple[str, str, str]] = set()
    resolved_from_map: dict[tuple[str, str, str], str] = {}

    for intent in rename_intents:
        key = (intent.table, intent.old_name, intent.new_name)
        confirmed_renames.add(key)
        resolved_from_map[key] = "rename_flag"

    confirmed_table_intents: set[tuple[str, str]] = set()
    table_resolved_from_map: dict[tuple[str, str], str] = {}

    if rename_table_flags:
        table_intents = _parse_rename_table_flags(rename_table_flags)
        for intent in table_intents:
            key = (intent["old_table"], intent["new_table"])
            confirmed_table_intents.add(key)
            table_resolved_from_map[key] = "rename_flag"

    try:
        from dbwarden.engine.snapshot import (
            find_latest_snapshot,
            diff_models_against_snapshot,
            _compute_table_overlap,
            RENAME_TABLE_OVERLAP_THRESHOLD,
        )
        snapshot = find_latest_snapshot(db_name)
    except Exception:
        snapshot = None

    if snapshot is not None and confirmed_table_intents:
        snapshot_tables = dict(snapshot.get("tables", {}))
        for old_table, new_table in confirmed_table_intents:
            if old_table in snapshot_tables:
                snapshot_tables[new_table] = snapshot_tables.pop(old_table)
        snapshot["tables"] = snapshot_tables

    if snapshot is not None:
        model_by_name = {t.name: t for t in tables}
        snapshot_tables = snapshot.get("tables", {})
        auto_detected_renames: list[tuple[str, str, str]] = []

        model_table_names = {t.name for t in tables}
        snap_table_names = set(snapshot_tables.keys())
        dropped_tables_list = snap_table_names - model_table_names
        added_tables_list = model_table_names - snap_table_names

        table_candidates: list[tuple[str, str, float]] = []
        for dropped in dropped_tables_list:
            for added in added_tables_list:
                if (dropped, added) in confirmed_table_intents:
                    continue
                overlap = _compute_table_overlap(dropped, added, snapshot, tables)
                if overlap >= RENAME_TABLE_OVERLAP_THRESHOLD:
                    table_candidates.append((dropped, added, overlap))

        if table_candidates:
            if sys.stdin.isatty():
                prompted_tables = _prompt_table_rename_confirmations(table_candidates)
                for intent in prompted_tables:
                    key = (intent["old_table"], intent["new_table"])
                    confirmed_table_intents.add(key)
                    table_resolved_from_map[key] = "prompt"
            else:
                logger.warning(_format_table_rename_warning(table_candidates))
                warning(_format_table_rename_warning(table_candidates))

        if confirmed_table_intents:
            snapshot_tables = dict(snapshot.get("tables", {}))
            for old_table, new_table in confirmed_table_intents:
                if old_table in snapshot_tables:
                    snapshot_tables[new_table] = snapshot_tables.pop(old_table)
            snapshot["tables"] = snapshot_tables

        for table in tables:
            if table.name not in snapshot_tables:
                continue
            snap_cols = snapshot_tables[table.name].get("columns", {})
            model_cols = {c.name: c for c in table.columns}

            dropped = [(n, snap_cols[n]) for n in snap_cols if n not in model_cols]
            added = [(n, model_cols[n]) for n in model_cols if n not in snap_cols]

            if not dropped or not added:
                continue

            from dbwarden.engine.snapshot import detect_renames
            renames = detect_renames(table.name, dropped, added)
            for old, new in renames:
                key = (table.name, old, new)
                if key not in confirmed_renames:
                    auto_detected_renames.append((table.name, old, new))

        if auto_detected_renames:
            if sys.stdin.isatty():
                prompted = _prompt_rename_confirmations(auto_detected_renames)
                for key in prompted:
                    confirmed_renames.add(key)
                    resolved_from_map[key] = "prompt"
            else:
                logger.warning(_format_rename_warning(
                    (t, o, n, f"{t}.{o}:{n}")
                    for t, o, n in auto_detected_renames
                ))
                warning(
                    _format_rename_warning(
                        (t, o, n, f"{t}.{o}:{n}")
                        for t, o, n in auto_detected_renames
                    )
                )

    migrations_dir = get_migrations_directory(database)
    next_number = get_next_migration_number(migrations_dir)

    upgrade_sql, rollback_sql, changes = generate_migration_sql(
        tables, migrations_dir, database, db_name,
        confirmed_renames=confirmed_renames,
        resolved_from_map=resolved_from_map,
        safe_type_change=safe_type_change,
        confirmed_table_intents=confirmed_table_intents,
        table_resolved_from_map=table_resolved_from_map,
        concurrent=concurrent,
        clickhouse_engine_recreate=clickhouse_engine_recreate,
        drop_preserved_clickhouse_table=drop_preserved_clickhouse_table,
        postgres_auto_using=postgres_auto_using,
    )

    safe_desc = _resolve_migration_description(description, changes)
    if migration_type in ("runs_always", "ra"):
        filename = generate_repeatable_filename(db_name, safe_desc, RUNS_ALWAYS_FILE_PREFIX)
    elif migration_type in ("runs_on_change", "roc"):
        filename = generate_repeatable_filename(db_name, safe_desc, RUNS_ON_CHANGE_FILE_PREFIX)
    else:
        filename = generate_migration_filename(db_name, safe_desc, next_number)
    plan = build_migration_plan(
        migration_id=Path(filename).stem,
        changes=changes,
        upgrade_sql=upgrade_sql,
    )

    if output_plan:
        plain(json.dumps(plan, indent=2))
        return

    if output_sql:
        sql(upgrade_sql)
        return

    if not upgrade_sql.strip():
        info("No new migrations to generate - all models already covered by existing migrations.")
        return

    filepath = os.path.join(migrations_dir, filename)
    plan_filepath = str(Path(filepath).with_suffix(".plan.json"))

    migrations_dir_canonical = os.path.realpath(migrations_dir)
    filepath_canonical = os.path.realpath(filepath)
    if not filepath_canonical.startswith(migrations_dir_canonical + os.sep):
        raise ValueError(
            f"Invalid migration path: {filename} resolves outside migrations directory. "
            "Path traversal not allowed."
        )

    content = f"""-- upgrade

{upgrade_sql}

-- rollback

{rollback_sql}
"""

    with open(filepath, "w") as f:
        f.write(content)

    with open(plan_filepath, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)
        f.write("\n")

    logger.info(f"Created migration file: {filename}")
    success(f"Created migration file: {filepath}")
    success(f"Created migration plan: {plan_filepath}")
    info(f"Tables included: {', '.join(t.name for t in tables)}")

    state = model_state_to_dict(tables, dbwarden_version=__version__)
    state_payload = model_state_json_dumps(state)
    state_path = get_model_state_path(db_name)
    legacy_path = get_model_state_path(db_name, legacy=True)
    if legacy_path != state_path:
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(state_payload)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(state_payload)
    logger.info(f"Model state written: {state_path}")

    try:
        from dbwarden.engine.snapshot import write_snapshot
        snapshot = model_state_to_dict(tables, dbwarden_version=__version__)
        snapshot["format_version"] = 1
        write_snapshot(
            snapshot,
            database=database,
            migration_id=Path(filename).stem,
        )
    except Exception:
        logger.exception("Failed to write schema snapshot")


def new_migration_cmd(
    description: str,
    version: str | None = None,
    database: str | None = None,
    migration_type: str = "versioned",
) -> None:
    logger = get_logger()

    multi_config = get_multi_db_config()
    db_name = database or multi_config.default

    migrations_dir = get_migrations_directory(database)

    safe_description = re.sub(r"[^a-zA-Z0-9]", "_", description).lower()
    safe_description = re.sub(r"_+", "_", safe_description).strip("_")
    if not safe_description:
        raise ValueError("Migration description cannot be empty or contain only special characters.")

    is_repeatable = migration_type in ("runs_always", "ra", "runs_on_change", "roc")
    if is_repeatable and version is not None:
        logger.warning("--version is ignored for repeatable migrations (RA/ROC).")

    if migration_type in ("runs_always", "ra"):
        migration_type = "runs_always"
        filename = generate_repeatable_filename(
            db_name, safe_description, RUNS_ALWAYS_FILE_PREFIX
        )
    elif migration_type in ("runs_on_change", "roc"):
        migration_type = "runs_on_change"
        filename = generate_repeatable_filename(
            db_name, safe_description, RUNS_ON_CHANGE_FILE_PREFIX
        )
    else:
        migration_type = "versioned"
        if version is None:
            version = get_next_migration_number(migrations_dir)
        filename = generate_migration_filename(db_name, safe_description, version)

    filepath = os.path.join(migrations_dir, filename)

    migrations_dir_canonical = os.path.realpath(migrations_dir)
    filepath_canonical = os.path.realpath(filepath)
    if not filepath_canonical.startswith(migrations_dir_canonical + os.sep):
        raise ValueError(
            f"Invalid migration path: {filename} resolves outside migrations directory. "
            "Path traversal not allowed."
        )

    content = f"""-- upgrade

-- {description}

-- rollback

-- {description}
"""

    with open(filepath, "w") as f:
        f.write(content)

    logger.info(f"Created migration file ({migration_type}): {filename}")
    success(f"Created migration file: {filepath}")
