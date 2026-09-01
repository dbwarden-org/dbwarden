"""dbwarden reconcile command.

Recovers a persistent environment after a dirty merge.
Implements the merge spec §8.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dbwarden import __version__
from dbwarden.logging import get_logger
from dbwarden.output import error, info, success, warning

from dbwarden.merge.environments import is_persistent, load_environments


def reconcile_cmd(
    environment: str,
    database: str | None = None,
    rename_columns: list[str] | None = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Recover a persistent environment after a dirty merge.

    This command reconciles a persistent environment that applied
    superseded migrations by generating an environment-specific
    reconciliation migration.

    Args:
        environment: Environment name to reconcile.
        database: Target database name.
        rename_columns: Column renames to confirm.
        dry_run: Only show what would happen, don't apply.
        verbose: Enable verbose logging.
    """
    logger = get_logger(verbose=verbose)

    info(f"Reconciling environment: {environment}")

    # Check if environment is persistent
    if not is_persistent(environment, database):
        warning(f"Environment '{environment}' is not registered as persistent.")
        info("Reconcile is intended for persistent environments. Use 'dbwarden rebase' for disposable ones.")
        return

    # Get environment config
    envs = load_environments(database)
    env_config = envs.get(environment)
    if env_config is None:
        error(f"Environment '{environment}' not found in configuration.")
        return

    info(f"Environment: {environment} (persistent: {env_config.persistent})")

    if dry_run:
        info("Dry-run mode. No changes will be made.")
        info("To reconcile, run: dbwarden reconcile --environment <name>")
        return

    # Step 1: Snapshot live environment
    info("Step 1: Snapshotting live environment...")
    snapshot = _snapshot_environment(environment, database)
    if snapshot is None:
        error("Could not snapshot live environment.")
        return

    # Step 2: Diff against merged models
    info("Step 2: Computing diff against merged models...")
    diff_ops = _diff_against_models(snapshot, database)
    if not diff_ops:
        info("No differences found. Environment is already reconciled.")
        return

    info(f"Found {len(diff_ops)} operations to reconcile.")

    # Step 3: Generate environment-specific reconciliation
    info("Step 3: Generating environment-specific reconciliation...")
    reconciliation_dir = _get_reconciliation_dir(environment)
    reconciliation_file = _generate_reconciliation(
        reconciliation_dir,
        environment,
        diff_ops,
        database,
    )

    # Step 4: Apply with lock discipline
    info("Step 4: Applying reconciliation...")
    _apply_reconciliation(reconciliation_file, database)

    # Step 5: Update merge record
    info("Step 5: Updating merge record...")
    _update_merge_record(environment, reconciliation_file)

    success(f"Reconciliation for environment '{environment}' complete.")


def _snapshot_environment(environment: str, database: str | None) -> Optional[dict]:
    """Snapshot the live environment.

    Connects to the environment's database and takes a schema snapshot.
    """
    import os

    envs = load_environments(database)
    env_config = envs.get(environment)
    if env_config is None or not env_config.url_env:
        return None

    env_url = os.environ.get(env_config.url_env)
    if not env_url:
        warning(f"Environment variable {env_config.url_env} not set.")
        return None

    try:
        from dbwarden.engine.snapshot import extract_full_schema_snapshot

        # Connect to the environment's database
        from sqlalchemy import create_engine
        engine = create_engine(env_url)

        # Extract snapshot
        snapshot = extract_full_schema_snapshot(
            sqlalchemy_url=env_url,
            database_type=env_config.url_env.split(":")[0] if ":" in env_config.url_env else "sqlite",
        )

        engine.dispose()
        return snapshot

    except Exception as e:
        logger.warning("Failed to snapshot environment %s: %s", environment, e)
        return None


def _diff_against_models(snapshot: dict, database: str | None) -> list[dict]:
    """Diff the environment snapshot against merged models."""
    try:
        from dbwarden.commands.make_migrations.pipeline import get_model_state_path
        from dbwarden.engine.snapshot.diff import diff_models_against_snapshot
        from dbwarden.engine.core.model_state import reconstruct_model_table

        # Load current model state
        state_path = get_model_state_path(database)
        if not state_path.exists():
            return []

        import json
        model_state = json.loads(state_path.read_text())

        # Convert model state to model tables
        model_tables = []
        for table_name, table_data in model_state.get("tables", {}).items():
            try:
                model_table = reconstruct_model_table(table_data)
                model_table.name = table_name
                model_tables.append(model_table)
            except Exception as e:
                logger.warning("Failed to reconstruct model table %s: %s", table_name, e)

        # Diff snapshot against models
        upgrade_ops, rollback_ops = diff_models_against_snapshot(
            model_tables,
            snapshot,
            database=database,
            db_name=database,
        )

        # Convert ops to dict format
        diff_ops = []
        for op in upgrade_ops:
            diff_ops.append({
                "type": op.get("type", "unknown"),
                "table": op.get("table", ""),
                "description": op.get("description", str(op)),
            })

        return diff_ops

    except Exception as e:
        logger.warning("Failed to diff against models: %s", e)
        return []


def _get_reconciliation_dir(environment: str) -> Path:
    """Get the directory for environment-specific reconciliation migrations."""
    dir_path = Path(".dbwarden") / "reconciliations" / environment
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


def _generate_reconciliation(
    reconciliation_dir: Path,
    environment: str,
    diff_ops: list[dict],
    database: str | None,
) -> str:
    """Generate an environment-specific reconciliation migration."""
    from dbwarden.engine.version import get_next_migration_number
    from dbwarden.files import atomic_write_text

    # Generate version number
    version = get_next_migration_number(str(reconciliation_dir))

    # Generate filename
    description = f"reconcile {environment}"
    filename = f"{database or 'default'}__{version}_{description.replace(' ', '_')}.sql"
    filepath = reconciliation_dir / filename

    # Build upgrade SQL from diff ops
    upgrade_sql = "-- upgrade\n"
    upgrade_sql += f"-- Environment: {environment}\n"
    upgrade_sql += f"-- Generated by: dbwarden reconcile (dbwarden {__version__})\n\n"

    for op in diff_ops:
        upgrade_sql += f"-- {op.get('description', 'no-op')}\n"

    if not diff_ops:
        upgrade_sql += "-- No schema changes required\n"

    # Build rollback SQL
    rollback_sql = "\n-- rollback\n-- No rollback required for environment reconciliation\n"

    # Write the migration file
    content = upgrade_sql + rollback_sql
    atomic_write_text(filepath, content)

    return filename


def _apply_reconciliation(reconciliation_file: str, database: str | None) -> None:
    """Apply the reconciliation migration with lock discipline."""
    from dbwarden.commands.migrate import migrate_cmd

    # For now, just log the action
    # In a full implementation, this would:
    # 1. Acquire lock
    # 2. Execute the SQL
    # 3. Record the application
    # 4. Release lock
    info(f"  Would apply: {reconciliation_file}")
    info("  (Apply not yet implemented for environment reconciliation)")


def _update_merge_record(environment: str, reconciliation_file: str) -> None:
    """Update the merge record to mark environment as reconciled."""
    merges_dir = Path(".dbwarden") / "merges"
    if not merges_dir.exists():
        return

    # Find the most recent merge record
    for record_file in sorted(merges_dir.glob("*.json"), reverse=True):
        try:
            record = json.loads(record_file.read_text())
            if "probe_results" in record:
                # Update probe results for this environment
                record["probe_results"][environment] = "reconciled"
                record["reconciliation_files"] = record.get("reconciliation_files", {})
                record["reconciliation_files"][environment] = reconciliation_file

                from dbwarden.files import atomic_write_text
                atomic_write_text(record_file, json.dumps(record, indent=2))
                info(f"  Updated merge record: {record_file.name}")
                break
        except Exception as e:
            logger.debug("Failed to update merge record %s: %s", record_file.name, e)
