import os
import time
import uuid

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
) -> None:
    """
    Rollback the last applied migration.

    Args:
        count: Number of migrations to rollback.
        to_version: Rollback to a specific version.
        verbose: Enable verbose logging.
        database: Target database name.
        perf: Emit per-statement timing breakdowns for executed SQL.
    """
    from dbwarden.commands.perf import PhaseTimer

    config = get_database(database)
    actual_db_name = database or config.sqlalchemy_url.split("/")[-1].split("?")[0]
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

            # Open long-lived migration connection for DDL execution
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
            )

        reverted = 0
        missing = 0
        for version, filepath in reversed(list(versions_to_rollback.items())):
            filename = filepath.split("/")[-1]
            if not os.path.exists(filepath):
                error(f"Migration file not found: {filename}")
                missing += 1
                continue
            sql_statements = parse_rollback_statements(filepath)

            for sql in sql_statements:
                logger.log_sql_statement(sql)
                logger.log_sql_trace(sql)

            start_time = time.time()
            logger.info(f"Rolling back migration: {filename} (version: {version})")

            run_migration(
                sql_statements=sql_statements,
                version=version,
                migration_operation="rollback",
                filename=filename,
                db_name=database,
                perf=perf,
                connection=_migration_conn,
            )

            duration = time.time() - start_time
            logger.info(f"Rollback completed: {filename} in {duration:.2f}s")
            reverted += 1

        if reverted:
            success(f"Rollback completed successfully: {reverted} migration(s) reverted.")
        if missing:
            warning(f"Warning: {missing} migration file(s) not found. Skipped.")
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
            # Skip superseded files (Phase 5: integration with merge handling)
            if not is_superseded(filepath):
                result[v] = filepath

    return dict(reversed(list(result.items())))
