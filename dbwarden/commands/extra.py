from __future__ import annotations

import json

from dbwarden.config import get_database, get_multi_db_config
from dbwarden.engine.migration_name import Change
from dbwarden.engine.version import get_migrations_directory
from dbwarden.logging import get_logger
from dbwarden.output import (
    data_table,
    emit_json,
    error,
    info,
    json_mode,
    plain,
    render,
    section,
    sql,
    success,
    warning,
)


def diff_cmd(
    output_format: str = "table",
    verbose: bool = False,
    database: str | None = None,
    offline: bool = False,
) -> None:
    """
    Show structural differences between models and database.

    Args:
        output_format: Output format (table, json, sql).
        verbose: Enable verbose logging.
        database: Target database name.
        offline: Use model state file instead of live database snapshot.
    """
    config = get_database(database)
    actual_db_name = database or get_multi_db_config().default
    logger = get_logger(
        verbose=verbose, db_name=actual_db_name, db_type=config.database_type
    )

    from dbwarden.engine.model_discovery import (
        get_all_model_tables,
        filter_model_tables_by_name,
        validate_model_tables_exist,
    )

    if not config.model_paths:
        warning("No model paths configured. Add model_paths to your dbwarden.py config.")
        return

    tables = get_all_model_tables(config.model_paths, db_name=actual_db_name)
    validate_model_tables_exist(tables, config.model_tables, actual_db_name)
    tables = filter_model_tables_by_name(tables, config.model_tables)
    if not tables:
        warning("No SQLAlchemy models found in the configured model paths.")
        return

    if offline:
        snapshot = _load_offline_snapshot(actual_db_name)
        if snapshot is None:
            return
    else:
        snapshot = _load_live_snapshot(database, logger)

    if snapshot is None:
        warning("Could not load schema snapshot. Run 'dbwarden migrate' to create one.")
        return

    from dbwarden.engine.snapshot import (
        _filter_duplicates_from_snapshot_diff,
        diff_models_against_snapshot,
        snapshot_diff_to_sql,
    )

    upgrade_ops, rollback_ops = diff_models_against_snapshot(
        tables, snapshot, database=database, db_name=actual_db_name
    )

    if not upgrade_ops:
        success("No differences found between models and database.")
        return

    from dbwarden.engine.snapshot import _apply_rename_intents

    upgrade_ops, rollback_ops = _apply_rename_intents(upgrade_ops, rollback_ops, set())

    upgrade_sql, rollback_sql, changes = snapshot_diff_to_sql(
        upgrade_ops, rollback_ops, database=database, db_name=actual_db_name,
    )

    _filter_migration_snapshots(database, upgrade_sql, upgrade_ops, rollback_sql, rollback_ops, changes)

    if output_format == "json":
        _display_json(changes)
    elif output_format == "sql":
        _display_sql(upgrade_sql)
    else:
        _display_table(changes)


def _load_live_snapshot(database: str | None, logger) -> dict | None:
    """Extract a full schema snapshot from the live database."""
    from dbwarden.engine.snapshot import extract_full_schema_snapshot
    try:
        return extract_full_schema_snapshot(database=database)
    except Exception as exc:
        logger.warning("Failed to extract live schema snapshot: %s", exc)
        return None


def _load_offline_snapshot(database: str | None) -> dict | None:
    """Load the model state from an exported JSON file."""
    from dbwarden.commands.make_migrations import get_current_model_state_path, get_model_state_path

    state_path = get_current_model_state_path(database)

    if not state_path.exists():
        warning(
            f"No model state file found at {get_model_state_path(database)}. "
            "Run 'dbwarden export-models' first."
        )
        return None
    try:
        import json
        return json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        error(f"Failed to read model state file: {exc}")
        return None


def _filter_migration_snapshots(database, upgrade_sql, upgrade_ops, rollback_sql, rollback_ops, changes):
    """Try to deduplicate against existing migration statements."""
    from dbwarden.commands.make_migrations import get_pending_migration_statements
    from dbwarden.engine.snapshot import _filter_duplicates_from_snapshot_diff

    migrations_dir = get_migrations_directory(database)
    if not migrations_dir:
        return

    existing_statements = get_pending_migration_statements(migrations_dir)
    if upgrade_sql.strip():
        upgrade_sql, rollback_sql, changes = _filter_duplicates_from_snapshot_diff(
            upgrade_sql, rollback_sql, list(changes), existing_statements
        )


def _display_table(changes: list[Change]) -> None:
    """Display changes as a Rich table."""
    if not changes:
        success("No differences found after filtering.")
        return

    render(
        data_table(
            "Schema Diff",
            ("Operation", "Table", "Target", "Severity"),
            (
                (change.operation, change.table or "", change.target or "", _severity_for(change.operation))
                for change in changes
            ),
        )
    )
    info(f"Total changes: {len(changes)}")


def _display_sql(upgrade_sql: str) -> None:
    """Display the generated upgrade SQL."""
    if not upgrade_sql.strip():
        success("No SQL changes to display.")
        return

    import re
    statements = [s.strip() for s in re.split(r"\n\n+", upgrade_sql) if s.strip()]
    section("Generated Migration SQL")
    for stmt in statements:
        sql(stmt)


def _display_json(changes: list[Change]) -> None:
    """Display changes as JSON."""
    data = [
        {
            "operation": c.operation,
            "table": c.table,
            "target": c.target,
            "severity": _severity_for(c.operation),
        }
        for c in changes
    ]
    plain(json.dumps(data, indent=2))


def _severity_for(operation: str) -> str:
    """Map an operation to a severity level."""
    destructive = {"drop_table", "drop_column", "drop_index", "drop_foreign_key",
                   "drop_unique_constraint", "drop_check_constraint", "drop_exclude_constraint"}
    if operation in destructive:
        return "WARNING"
    return "INFO"


def lock_status_cmd(database: str | None = None) -> None:
    """Check if migration is currently locked."""
    from dbwarden.repositories import check_lock

    is_locked = check_lock(database)
    db_name = database or "default"
    if json_mode():
        emit_json({"database": db_name, "locked": is_locked})
        return
    if is_locked:
        warning("Migration lock: ACTIVE")
        info("Another migration process may be running.")
    else:
        success("Migration lock: INACTIVE")


def unlock_cmd(database: str | None = None) -> None:
    """Release the migration lock."""
    from dbwarden.repositories import check_lock, force_release_lock

    if not check_lock(database):
        warning("Migration lock is not currently held.")
        return

    if force_release_lock(database):
        success("Migration lock released successfully.")
    else:
        error("Failed to release migration lock.")
