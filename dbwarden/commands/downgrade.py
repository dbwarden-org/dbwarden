from __future__ import annotations

import time
import uuid

from dbwarden.config import get_database
from dbwarden.engine.file_parser import parse_rollback_statements
from dbwarden.engine.version import get_migration_filepaths_by_version, get_migrations_directory
from dbwarden.exceptions import LockError
from dbwarden.logging import get_logger
from dbwarden.output import error, info, success, warning
from dbwarden.repositories import (
    create_lock_table_if_not_exists,
    create_migrations_table_if_not_exists,
    get_migrated_versions,
    run_migration,
)
from dbwarden.repositories.lock_repo import acquire_lock, check_lock, release_lock


def downgrade_cmd(
    to_version: str,
    verbose: bool = False,
    database: str | None = None,
    perf: bool = False,
) -> None:
    from dbwarden.commands.perf import PhaseTimer

    config = get_database(database)
    actual_db_name = database or config.sqlalchemy_url.split("/")[-1].split("?")[0]
    logger = get_logger(
        verbose=verbose, db_name=actual_db_name, db_type=config.database_type
    )

    migrations_dir = get_migrations_directory(database)

    create_migrations_table_if_not_exists(database)
    create_lock_table_if_not_exists(database)

    lock_acquired = False
    lock_owner = uuid.uuid4().hex
    try:
        with PhaseTimer(logger, "Lock acquisition", perf=perf):
            if not acquire_lock(database, lock_owner):
                raise LockError("Could not acquire migration lock.")
            lock_acquired = True

        with PhaseTimer(logger, "Rollback preparation", perf=perf):
            applied_versions = get_migrated_versions(database)
            if not applied_versions:
                info("Nothing to downgrade.")
                return

            if to_version not in applied_versions:
                error(f"Target version {to_version} has not been applied. Cannot downgrade.")
                raise SystemExit(1)

            versions_to_revert = [v for v in applied_versions if v > to_version]
            if not versions_to_revert:
                info(f"Already at version {to_version}. Nothing to downgrade.")
                return

            filepaths = get_migration_filepaths_by_version(
                directory=migrations_dir,
                version_to_start_from=to_version,
                end_version=versions_to_revert[-1],
            )

        reverted = []
        for version in reversed(versions_to_revert):
            if version not in filepaths:
                error(f"Migration file for version {version} not found.")
                continue

            filepath = filepaths[version]
            filename = filepath.split("/")[-1]
            sql_statements = parse_rollback_statements(filepath)

            if not sql_statements:
                logger.info(f"No rollback statements found for {filename}, skipping.")
                continue

            for sql in sql_statements:
                logger.log_sql_statement(sql)
                logger.log_sql_trace(sql)

            start_time = time.time()
            logger.info(f"Downgrading migration: {filename} (version: {version})")

            run_migration(
                sql_statements=sql_statements,
                version=version,
                migration_operation="rollback",
                filename=filename,
                db_name=database,
                perf=perf,
            )

            duration = time.time() - start_time
            logger.info(f"Downgrade completed: {filename} in {duration:.2f}s")
            reverted.append(version)

        if reverted:
            success(f"Downgrade completed: {len(reverted)} migration(s) reverted to version {to_version}.")
        else:
            warning("No migrations were downgraded.")
    finally:
        if lock_acquired:
            if not release_lock(database, lock_owner):
                logger.error("Migration lock was not released by owner %s", lock_owner)
