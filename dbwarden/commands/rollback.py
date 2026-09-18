import os
import time
import uuid
from pathlib import Path

from dbwarden.commands.migrate.hooks import (
    MigrationPlanEntry,
    PreMigrationRunContext,
    PreMigrationContext,
    PostMigrationContext,
    MigrationFailureContext,
    PostMigrationRunContext,
    execute_veto_hook,
    execute_observer_hook,
    execute_failure_hook,
)
from dbwarden.commands.migrate import _collect_hooks
from dbwarden.config import get_database
from dbwarden.engine.file_parser import parse_rollback_statements
from dbwarden.engine.version import get_migrations_directory
from dbwarden.exceptions import LockError
from dbwarden.logging import get_logger
from dbwarden.output import error, info, success, warning
from dbwarden.repositories import (
    create_lock_table_if_not_exists,
    create_migrations_table_if_not_exists,
    get_latest_versions,
    run_migration,
)
from dbwarden.lock import acquire_lock, check_lock, release_lock


def rollback_cmd(
    count: int | None = None,
    to_version: str | None = None,
    verbose: bool = False,
    database: str | None = None,
    perf: bool = False,
    include_superseded: bool = False,
) -> None:
    """
    Rollback the last applied migration.

    Args:
        count: Number of migrations to rollback.
        to_version: Rollback to a specific version.
        verbose: Enable verbose logging.
        database: Target database name.
        perf: Emit per-statement timing breakdowns for executed SQL.
        include_superseded: Include superseded files in rollback (for rebase).
    """
    from dbwarden.commands.perf import PhaseTimer
    from dbwarden.commands.migrate.hooks import MigrationProgressContext
    from dbwarden.plugin import validate_migration_hooks

    config = get_database(database)
    actual_db_name = database or config.sqlalchemy_url.split("/")[-1].split("?")[0]

    # Section 11, step 6: Validate merged lifecycle hook registry before command runs
    validate_migration_hooks(database)

    logger = get_logger(
        verbose=verbose, db_name=actual_db_name, db_type=config.database_type
    )

    if count is not None and to_version is not None:
        raise ValueError("Cannot specify both 'count' and 'to-version'.")

    migrations_dir = get_migrations_directory(database)

    create_migrations_table_if_not_exists(database)
    create_lock_table_if_not_exists(database)

    lock_acquired = False
    lock_owner = uuid.uuid4().hex
    _lock_strategy = None
    _migration_conn = None
    try:
        with PhaseTimer(logger, "Lock acquisition", perf=perf):
            lock_result = acquire_lock(database)
            if not lock_result.acquired:
                raise LockError(
                    f"Could not acquire migration lock. "
                    f"{lock_result.holder_description}"
                )
            lock_acquired = True
            lock_owner = lock_result.owner_id
            _lock_strategy = lock_result.strategy

            # Open long-lived migration connection for DDL execution.
            # For SQLite, reuse the lock connection which already holds
            # BEGIN IMMEDIATE; a new connection would block on the write lock.
            if config.database_type == "sqlite" and lock_result.connection is not None:
                _migration_conn = lock_result.connection
            else:
                from dbwarden.connection.connection import hold_migration_connection
                _migration_conn = hold_migration_connection(database)

        if count is None and to_version is None:
            count = 1

        with PhaseTimer(logger, "Rollback preparation", perf=perf):
            latest_versions = get_latest_versions(
                database, limit=count, starting_version=to_version
            )

            if not latest_versions:
                info("Nothing to rollback.")
                return

            versions_to_rollback = _get_versions_to_rollback(
                latest_versions=latest_versions,
                migrations_dir=migrations_dir,
                include_superseded=include_superseded,
            )

        # Build pending rollback entries for hooks
        rollback_entries: list[MigrationPlanEntry] = []
        for version, filepath in reversed(list(versions_to_rollback.items())):
            sql_stmts = parse_rollback_statements(filepath)
            rollback_entries.append(
                MigrationPlanEntry(
                    version=version,
                    description=os.path.basename(filepath).split("__", 1)[-1].rsplit(".", 1)[0],
                    file_path=Path(filepath),
                    checksum="",
                    kind="versioned",
                    statement_count=len(sql_stmts),
                )
            )

        # Execute pre_migration_run hooks
        _run_hooks = _collect_hooks("pre_migration_run", database)
        if _run_hooks:
            pre_run_ctx = PreMigrationRunContext(
                database=actual_db_name,
                command="rollback",
                dry_run=False,
                dev=False,
                pending_migrations=tuple(rollback_entries),
            )
            execute_veto_hook("pre_migration_run", _run_hooks, pre_run_ctx, command="rollback")

        reverted = 0
        missing = 0
        for version, filepath in reversed(list(versions_to_rollback.items())):
            filename = filepath.split("/")[-1]
            if not os.path.exists(filepath):
                error(f"Migration file not found: {filename}")
                missing += 1
                continue
            sql_statements = parse_rollback_statements(filepath)

            # Execute pre_migration hooks
            _pre_hooks = _collect_hooks("pre_migration", database)
            if _pre_hooks:
                plan_entry = MigrationPlanEntry(
                    version=version,
                    description=os.path.basename(filepath).split("__", 1)[-1].rsplit(".", 1)[0],
                    file_path=Path(filepath),
                    checksum="",
                    kind="versioned",
                    statement_count=len(sql_statements),
                )
                pre_ctx = PreMigrationContext(
                    database=actual_db_name,
                    command="rollback",
                    dry_run=False,
                    dev=False,
                    migration=plan_entry,
                )
                execute_veto_hook("pre_migration", _pre_hooks, pre_ctx, command="rollback")

            for sql in sql_statements:
                logger.log_sql_statement(sql)
                logger.log_sql_trace(sql)

            start_time = time.time()
            logger.info(f"Rolling back migration: {filename} (version: {version})")

            # Section 8: Per-statement progress callback
            _progress_hooks = _collect_hooks("migration_progress", database)
            _stmt_start_time = start_time

            def _on_progress(stmt_idx: int, total: int) -> None:
                if _progress_hooks:
                    progress_ctx = MigrationProgressContext(
                        database=actual_db_name,
                        command="rollback",
                        dry_run=False,
                        dev=False,
                        migration=plan_entry,
                        statement_index=stmt_idx,
                        total_statements=total,
                        progress_pct=(stmt_idx / total * 100) if total else 100.0,
                        elapsed_ms=(time.time() - _stmt_start_time) * 1000,
                    )
                    execute_observer_hook("migration_progress", _progress_hooks, progress_ctx)

            try:
                run_migration(
                    sql_statements=sql_statements,
                    version=version,
                    migration_operation="rollback",
                    filename=filename,
                    db_name=database,
                    perf=perf,
                    connection=_migration_conn,
                    progress_callback=_on_progress,
                )
            except Exception as exc:
                # Execute on_migration_failure hooks
                _failure_hooks = _collect_hooks("on_migration_failure", database)
                if _failure_hooks:
                    failure_ctx = MigrationFailureContext(
                        database=actual_db_name,
                        command="rollback",
                        dry_run=False,
                        dev=False,
                        migration=plan_entry,
                        error=exc,
                        statements_executed_before_failure=0,
                    )
                    exc = execute_failure_hook("on_migration_failure", _failure_hooks, failure_ctx, exc)
                raise

            duration = time.time() - start_time
            logger.info(f"Rollback completed: {filename} in {duration:.2f}s")
            reverted += 1

            # Execute post_migration hooks
            _post_hooks = _collect_hooks("post_migration", database)
            if _post_hooks:
                post_ctx = PostMigrationContext(
                    database=actual_db_name,
                    command="rollback",
                    dry_run=False,
                    dev=False,
                    migration=plan_entry,
                    duration_ms=duration * 1000,
                    statements_executed=len(sql_statements),
                )
                execute_observer_hook("post_migration", _post_hooks, post_ctx)

        if reverted:
            success(f"Rollback completed successfully: {reverted} migration(s) reverted.")
        if missing:
            warning(f"Warning: {missing} migration file(s) not found. Skipped.")

        # Execute post_migration_run hooks
        _post_run_hooks = _collect_hooks("post_migration_run", database)
        if _post_run_hooks:
            post_run_ctx = PostMigrationRunContext(
                database=actual_db_name,
                command="rollback",
                dry_run=False,
                dev=False,
                applied_migrations=(),
                total_duration_ms=0,
            )
            execute_observer_hook("post_migration_run", _post_run_hooks, post_run_ctx)
    finally:
        # Close migration connection before releasing the lock
        if _migration_conn is not None:
            try:
                _migration_conn.close()
            except Exception:
                pass
        if lock_acquired:
            if not release_lock(database, strategy=_lock_strategy):
                logger.error("Migration lock was not released by owner %s", lock_owner)


def _get_versions_to_rollback(
    latest_versions: list[str],
    migrations_dir: str,
    include_superseded: bool = False,
) -> dict[str, str]:
    """Get migration file paths for versions to rollback."""
    from dbwarden.engine.version import get_migration_filepaths_by_version
    from dbwarden.merge.marker import is_superseded

    if not latest_versions:
        return {}

    filepaths = get_migration_filepaths_by_version(
        directory=migrations_dir,
    )

    result: dict[str, str] = {}
    for v in latest_versions:
        if v in filepaths:
            filepath = filepaths[v]
            # Skip superseded files unless include_superseded is True (for rebase)
            if include_superseded or not is_superseded(filepath):
                result[v] = filepath

    return dict(reversed(list(result.items())))
