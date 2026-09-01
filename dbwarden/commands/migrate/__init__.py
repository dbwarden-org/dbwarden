from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

from dbwarden.commands.backup import create_backup
from dbwarden.commands.migrate.state import (
    _write_migration_snapshot,
    _write_model_state,
)
from dbwarden.constants import RUNS_ALWAYS_FILE_PREFIX
from dbwarden.engine.file_parser import parse_upgrade_statements
from dbwarden.exceptions import DBDisconnectedError, LockError
from dbwarden.engine.version import (
    get_migrations_directory,
    get_runs_always_filepaths,
    get_runs_on_change_filepaths,
)
from dbwarden.logging import get_logger
from dbwarden.metrics import (
    increment_migration_errors,
    increment_migrations_total,
    metrics_enabled,
    observe_migration_duration,
    set_pending_migrations,
    set_schema_version,
)
from dbwarden import __version__
from dbwarden.output import error, info, section, sql as render_sql, success, warning
from dbwarden.repositories import (
    create_migrations_table_if_not_exists,
    create_lock_table_if_not_exists,
    get_existing_runs_always_filenames,
    get_applied_checksums,
    get_migrated_versions,
    run_migration,
    run_repeatable_migration,
)
from dbwarden.repositories.lock_repo import acquire_lock, check_lock, release_lock
from dbwarden.engine.discovery import (
    get_all_model_tables,
    filter_model_tables_by_name,
    validate_model_tables_exist,
)
from dbwarden.engine.offline import model_state_to_dict


def set_baseline_migration(
    migrations_dir: str, version: str, db_name: str | None = None
) -> list[str] | None:
    """
    Mark all migrations up to and including the specified version as applied.

    Args:
        migrations_dir: Path to migrations directory.
        version: Version to set as baseline.
        db_name: Database name.

    Returns:
        list[str]: List of applied versions.
    """
    from dbwarden.engine.file_parser import parse_migration_header
    from dbwarden.engine.checksum import calculate_checksum
    from dbwarden.engine.version import get_migration_filepaths_by_version

    filepaths = get_migration_filepaths_by_version(migrations_dir)

    applied = []
    for v, fp in sorted(filepaths.items()):
        if v <= version:
            statements = parse_upgrade_statements(fp)
            checksum = calculate_checksum(statements)
            filename = fp.split("/")[-1]
            description = parse_migration_header(fp).description or filename

            run_migration(
                sql_statements=statements,
                version=v,
                migration_operation="upgrade",
                filename=filename,
                db_name=db_name,
            )
            applied.append(v)

    return applied


def migrate_single(
    db_name: str | None = None,
    count: int | None = None,
    to_version: str | None = None,
    verbose: bool = False,
    baseline: bool = False,
    with_backup: bool = False,
    backup_dir: str | None = None,
    dry_run: bool = False,
    sandbox: bool = False,
    apply_seeds: bool = False,
    perf: bool = False,
    defer_snapshots: bool = False,
) -> None:
    """
    Apply pending migrations to a single database.

    Args:
        db_name: Database name.
        count: Number of migrations to apply.
        to_version: Apply migrations up to this version.
        verbose: Enable verbose logging.
        baseline: Mark migrations as applied without executing.
        with_backup: Create a backup before migrating.
        backup_dir: Directory for backup files.
        dry_run: Display pending migrations without executing them.
        sandbox: Apply migrations in a temporary sandbox database instead.
        apply_seeds: Apply pending seeds after migrations (overrides config).
        perf: Emit per-statement timing breakdowns for executed SQL.
        defer_snapshots: Write only the final schema snapshot for a batch.
    """
    from dbwarden.commands.perf import PhaseTimer
    from dbwarden.config import get_database

    config = get_database(db_name)
    sqlalchemy_url = config.sqlalchemy_url
    actual_db_name = db_name or config.sqlalchemy_url.split("/")[-1].split("?")[0]

    logger = get_logger(
        verbose=verbose, db_name=actual_db_name, db_type=config.database_type
    )

    if db_name:
        section(f"Migrating database: {db_name}")

    sandbox_provider = None
    _sandbox_cm = None
    _sandbox_started = False
    if sandbox:
        from dbwarden.connection.connection import sandbox_override as _sandbox_ctx
        from dbwarden.plugin import HookRegistry

        sandbox_url: str | None = None
        sandbox_db_type: str | None = None
        if HookRegistry.is_registered("sandbox_provider_start"):
            result = HookRegistry.execute_single("sandbox_provider_start", config.database_type)
            if isinstance(result, tuple) and len(result) == 2:
                sandbox_url, sandbox_db_type = result
                warning(f"Sandbox started via plugin ({sandbox_db_type}): {sandbox_url}")
        if sandbox_url is None:
            from dbwarden.engine.sandbox import SQLiteSandboxProvider
            sandbox_provider = SQLiteSandboxProvider()
            sandbox_url = sandbox_provider.start()
            sandbox_db_type = sandbox_provider.get_database_type()
            warning(f"Sandbox started (built-in {sandbox_provider.__class__.__name__}): {sandbox_url}")
        _sandbox_cm = _sandbox_ctx(sandbox_url, sandbox_db_type)
        _sandbox_cm.__enter__()
        _sandbox_started = True

    lock_acquired = False
    lock_owner = uuid.uuid4().hex
    try:
        if with_backup and not sandbox:
            backup_directory = backup_dir or os.path.join(os.getcwd(), "backups")
            backup_path = create_backup(sqlalchemy_url, backup_directory)
            logger.log_backup_created(backup_path)

        migrations_dir = get_migrations_directory(db_name)

        if not dry_run:
            with PhaseTimer(logger, "Lock acquisition", perf=perf):
                create_migrations_table_if_not_exists(db_name)
                create_lock_table_if_not_exists(db_name)

                if not acquire_lock(db_name, lock_owner):
                    raise LockError("Could not acquire migration lock.")
                lock_acquired = True

        applied_versions = set()
        applied_checksums = set()
        if not dry_run:
            applied_versions = set(get_migrated_versions(db_name))
            applied_checksums = get_applied_checksums(db_name)

        if baseline:
            if not to_version:
                raise ValueError("--baseline requires --to-version to be specified.")

            applied = set_baseline_migration(migrations_dir, to_version, db_name)
            logger.log_baseline_set(to_version)
            success(f"Baseline set at version: {to_version}")
            if applied:
                _write_migration_snapshot(
                    db_name=db_name,
                    migration_id=f"baseline-{applied[-1]}",
                )
            return

        filepaths_by_version = _get_filepaths_by_version(
            count=count,
            to_version=to_version,
            migrations_dir=migrations_dir,
            applied_versions=applied_versions,
            db_name=db_name,
        )

        runs_always_filepaths = get_runs_always_filepaths(migrations_dir)
        runs_on_change_filepaths = get_runs_on_change_filepaths(
            migrations_dir, changed_only=not dry_run, db_name=db_name
        )

        if (
            not filepaths_by_version
            and not runs_always_filepaths
            and not runs_on_change_filepaths
        ):
            info("Migrations are up to date.")
            return

        if filepaths_by_version:
            logger.log_pending_migrations(list(filepaths_by_version.keys()))

        if metrics_enabled() and not dry_run:
            set_pending_migrations(
                actual_db_name, len(filepaths_by_version)
            )

        if dry_run:
            warning("DRY RUN - No changes applied")

        versioned_count = 0
        latest_version = "0"

        from dbwarden.engine.checksum import calculate_checksum

        for version, filepath in filepaths_by_version.items():
            filename = filepath.split("/")[-1]
            sql_statements = parse_upgrade_statements(filepath)
            checksum = calculate_checksum(sql_statements)

            if checksum in applied_checksums:
                logger.log_migration_skipped(version, filename, checksum)
                continue

            for sql in sql_statements:
                logger.log_sql_statement(sql)
                logger.log_sql_trace(sql)

            if dry_run:
                warning(f"Would apply version {version}: {filename}")
                for statement in sql_statements:
                    render_sql(statement)
                continue

            start_time = time.time()
            logger.log_migration_start(version, filename)

            run_migration(
                sql_statements=sql_statements,
                version=version,
                migration_operation="upgrade",
                filename=filename,
                db_name=db_name,
                perf=perf,
            )

            if not defer_snapshots:
                with PhaseTimer(logger, "Snapshot write", perf=perf):
                    _write_migration_snapshot(
                        db_name=db_name,
                        migration_id=Path(filename).stem,
                    )

            duration = time.time() - start_time
            logger.log_migration_end(version, filename, duration)
            versioned_count += 1
            applied_checksums.add(checksum)
            latest_version = version
            increment_migrations_total(actual_db_name, version, success=True)
            observe_migration_duration(actual_db_name, version, duration)

        if dry_run:
            info(
                "Dry-run summary: "
                f"{len(filepaths_by_version)} versioned, "
                f"{len(runs_always_filepaths)} runs-always, "
                f"{len(runs_on_change_filepaths)} runs-on-change "
                "migrations would be applied."
            )
            return

        existing_runs_always = get_existing_runs_always_filenames(db_name)

        for filepath in runs_always_filepaths:
            filename = filepath.split("/")[-1]
            sql_statements = parse_upgrade_statements(filepath)

            start_time = time.time()
            logger.log_migration_start("RA", filename)

            if filename in existing_runs_always:
                run_repeatable_migration(
                    sql_statements=sql_statements,
                    filename=filename,
                    migration_type="runs_always",
                    db_name=db_name,
                    perf=perf,
                )
            else:
                run_migration(
                    sql_statements=sql_statements,
                    version=None,
                    migration_operation="upgrade",
                    filename=filename,
                    migration_type="runs_always",
                    db_name=db_name,
                    perf=perf,
                )

            duration = time.time() - start_time
            logger.log_migration_end("RA", filename, duration)

        for filepath in runs_on_change_filepaths:
            filename = filepath.split("/")[-1]
            sql_statements = parse_upgrade_statements(filepath)

            start_time = time.time()
            logger.log_migration_start("ROC", filename)

            run_repeatable_migration(
                sql_statements=sql_statements,
                filename=filename,
                migration_type="runs_on_change",
                db_name=db_name,
                perf=perf,
            )

            duration = time.time() - start_time
            logger.log_migration_end("ROC", filename, duration)

        # After migrations complete, auto-apply pending seeds if configured or --apply-seeds
        if config.auto_apply_seeds or apply_seeds:
            _apply_pending_seeds_after_migrate(db_name)

        if defer_snapshots and (versioned_count > 0 or runs_always_filepaths or runs_on_change_filepaths):
            with PhaseTimer(logger, "Final snapshot write", perf=perf):
                _write_migration_snapshot(
                    db_name=db_name,
                    migration_id=latest_version or "final",
                )

        if versioned_count > 0:
            success(f"Migrations completed successfully: {versioned_count} migrations applied.")
            if metrics_enabled():
                set_schema_version(actual_db_name, latest_version)
                set_pending_migrations(actual_db_name, 0)
            with PhaseTimer(logger, "Model state write", perf=perf):
                _write_model_state(config=config, db_name=db_name)
        elif runs_always_filepaths or runs_on_change_filepaths:
            with PhaseTimer(logger, "Model state write", perf=perf):
                _write_model_state(config=config, db_name=db_name)

    except Exception:
        if metrics_enabled():
            increment_migration_errors(actual_db_name)
        raise
    finally:
        if lock_acquired:
            if not release_lock(db_name, lock_owner):
                logger.error("Migration lock was not released by owner %s", lock_owner)
        if _sandbox_started:
            from dbwarden.plugin import HookRegistry
            if HookRegistry.is_registered("sandbox_provider_stop"):
                HookRegistry.execute_single("sandbox_provider_stop")
            elif sandbox_provider is not None:
                sandbox_provider.stop()
            warning("Sandbox stopped.")
        if _sandbox_cm is not None:
            _sandbox_cm.__exit__(None, None, None)


def _apply_pending_seeds_after_migrate(db_name: str | None = None) -> None:
    from dbwarden.plugin import HookRegistry

    if HookRegistry.is_registered("seed_apply"):
        HookRegistry.execute_single(
            "seed_apply",
            database=db_name,
            verbose=False,
        )


def migrate_cmd(
    count: int | None = None,
    to_version: str | None = None,
    verbose: bool = False,
    database: str | None = None,
    all_databases: bool = False,
    baseline: bool = False,
    with_backup: bool = False,
    backup_dir: str | None = None,
    dry_run: bool = False,
    sandbox: bool = False,
    apply_seeds: bool = False,
    perf: bool = False,
    defer_snapshots: bool = False,
) -> None:
    """
    Apply pending migrations to the database.

    Args:
        count: Number of migrations to apply.
        to_version: Apply migrations up to this version.
        verbose: Enable verbose logging.
        database: Database name to target.
        all_databases: Run migrations on all databases sequentially.
        baseline: Mark migrations as applied without executing.
        with_backup: Create a backup before migrating.
        backup_dir: Directory for backup files.
        dry_run: Display pending migrations without executing them.
        sandbox: Apply migrations in a temporary sandbox database instead.
        apply_seeds: Apply pending seeds after migrations (overrides config).
        perf: Emit per-statement timing breakdowns for executed SQL.
    """
    if count is not None and to_version is not None:
        raise ValueError("Cannot specify both 'count' and 'to-version'.")

    if count is not None and count < 1:
        raise ValueError("'count' must be a positive integer.")

    if all_databases:
        from dbwarden.config import get_multi_db_config
        from dbwarden.connection.availability import (
            DatabaseAvailability,
            MultiDatabaseResult,
            probe_database,
        )

        config = get_multi_db_config()
        databases = config.databases

        result = MultiDatabaseResult()
        for db_name in databases:
            configured = databases[db_name]
            availability = (
                probe_database(db_name, optional=True, config=configured)
                if hasattr(configured, "sqlalchemy_url")
                else None
            )
            if availability is None:
                availability = DatabaseAvailability(database=db_name, available=True)
            if availability.skipped:
                result.skipped.append(availability)
                warning(f"{db_name}: skipped, connection failed after retries")
                continue
            if not availability.available:
                result.failed.append(availability)
                error(f"Error migrating database '{db_name}': {availability.message}")
                continue
            try:
                migrate_single(
                    db_name=db_name,
                    count=count,
                    to_version=to_version,
                    verbose=verbose,
                    baseline=baseline,
                    with_backup=with_backup,
                    backup_dir=backup_dir,
                    dry_run=dry_run,
                    sandbox=sandbox,
                    apply_seeds=apply_seeds,
                    perf=perf,
                    defer_snapshots=defer_snapshots,
                )
                result.succeeded.append(db_name)
            except Exception as e:
                error(f"Error migrating database '{db_name}': {e}")
                result.failed.append(
                    DatabaseAvailability(
                        database=db_name,
                        available=False,
                        error_code="migration_failed",
                        message=str(e),
                    )
                )
        if result.skipped:
            from dbwarden.output import emit_json, json_mode
            if json_mode():
                emit_json(result.as_dict())
            if result.failed:
                details = "; ".join(
                    f"{item.database}: {item.message}" for item in result.failed
                )
                raise RuntimeError(
                    f"Migration failed for {len(result.failed)} database(s): {details}"
                )
            if result.succeeded:
                success("Migration completed with partial success.")
            import typer
            raise typer.Exit(code=result.exit_code)
        if result.failed:
            details = "; ".join(
                f"{item.database}: {item.message}" for item in result.failed
            )
            raise RuntimeError(f"Migration failed for {len(result.failed)} database(s): {details}")
    else:
        migrate_single(
            db_name=database,
            count=count,
            to_version=to_version,
            verbose=verbose,
            baseline=baseline,
            with_backup=with_backup,
            backup_dir=backup_dir,
            dry_run=dry_run,
            sandbox=sandbox,
            apply_seeds=apply_seeds,
            perf=perf,
            defer_snapshots=defer_snapshots,
        )


def _get_filepaths_by_version(
    count: int | None = None,
    to_version: str | None = None,
    migrations_dir: str | None = None,
    applied_versions: set[str] | None = None,
    db_name: str | None = None,
) -> dict[str, str]:
    """Get pending migration file paths."""
    from dbwarden.engine.version import resolve_migration_order

    if migrations_dir is None:
        migrations_dir = get_migrations_directory(db_name)

    ordered = resolve_migration_order(
        directory=migrations_dir,
        applied_versions=applied_versions or set(),
    )
    filepaths = {version: filepath for version, filepath, _deps, _seed in ordered}

    if count:
        filepaths = dict(list(filepaths.items())[:count])
    elif to_version:
        seen = {}
        for v, p in filepaths.items():
            seen[v] = p
            if v == to_version:
                break
        filepaths = seen

    return filepaths
