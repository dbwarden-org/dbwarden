from __future__ import annotations

import os
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from dbwarden.commands.backup import create_backup
from dbwarden.commands.migrate.state import (
    _write_migration_snapshot,
    _write_model_state,
)
from dbwarden.commands.migrate.hooks import (
    MigrationPlanEntry,
    PreMigrationRunContext,
    PreMigrationContext,
    MigrationProgressContext,
    PostMigrationContext,
    MigrationFailureContext,
    PostMigrationRunContext,
    execute_veto_hook,
    execute_observer_hook,
    execute_failure_hook,
)
from dbwarden.engine.preflight import run_preflight
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


def _collect_hooks(
    hook_name: str, db_name: str | None = None
) -> list:
    """Collect hooks from plugin registry, global app registry, and per-database config.

    Order: trusted plugins first (plugin discovery order), then project-wide
    app hooks (configuration import order), then per-database app hooks
    (declaration order). Section 8.
    """
    from dbwarden.plugin import HookRegistry, _APP_HOOKS_SOURCE

    hooks = []
    # 1. Plugin hooks (registered via PluginRegistrar.register)
    if HookRegistry.is_registered(hook_name):
        for source, fn in HookRegistry._hooks.get(hook_name, []):
            if source != _APP_HOOKS_SOURCE:
                hooks.append(fn)

    # 2. Project-wide app hooks (registered via register_migration_hooks)
    if HookRegistry.is_registered(hook_name):
        for source, fn in HookRegistry._hooks.get(hook_name, []):
            if source == _APP_HOOKS_SOURCE:
                hooks.append(fn)

    # 3. Per-database app hooks
    if db_name:
        from dbwarden.config import get_database
        try:
            config = get_database(db_name)
            db_hooks = getattr(config, "migration_hooks", None) or {}
            if hook_name in db_hooks:
                hooks.extend(db_hooks[hook_name])
        except Exception:
            pass

    return hooks


class MigrationSignalHandler:
    """Signal handler for graceful migration shutdown.

    Handles SIGTERM/SIGINT by:
    1. Finishing the current statement
    2. Recording progress
    3. Releasing the lock
    4. Exiting with code 75 (EX_TEMPFAIL)
    """

    def __init__(self):
        self._interrupted = False
        self._original_sigterm = None
        self._original_sigint = None

    def install(self) -> None:
        """Install signal handlers for SIGTERM and SIGINT."""
        self._original_sigterm = signal.getsignal(signal.SIGTERM)
        self._original_sigint = signal.getsignal(signal.SIGINT)

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def uninstall(self) -> None:
        """Restore original signal handlers."""
        if self._original_sigterm is not None:
            signal.signal(signal.SIGTERM, self._original_sigterm)
        if self._original_sigint is not None:
            signal.signal(signal.SIGINT, self._original_sigint)

    def _handle_signal(self, signum: int, frame) -> None:
        """Handle SIGTERM/SIGINT by setting interrupted flag."""
        self._interrupted = True
        # Log the signal but don't exit yet - let the current statement finish
        signal_name = signal.Signals(signum).name
        logger = get_logger()
        logger.warning("Received signal %s, finishing current statement...", signal_name)

    @property
    def is_interrupted(self) -> bool:
        """Check if a signal was received."""
        return self._interrupted
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
from dbwarden.lock import acquire_lock, check_lock, release_lock
from dbwarden.engine.discovery import (
    get_all_model_tables,
    filter_model_tables_by_name,
    validate_model_tables_exist,
)
from dbwarden.engine.offline import model_state_to_dict


def set_baseline_migration(
    migrations_dir: str, version: str, db_name: str | None = None,
    connection: Any | None = None,
) -> list[str] | None:
    """
    Mark all migrations up to and including the specified version as applied.

    Args:
        migrations_dir: Path to migrations directory.
        version: Version to set as baseline.
        db_name: Database name.
        connection: Optional long-lived migration connection. When provided,
            statements execute on this connection instead of opening a new one.

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
                connection=connection,
            )
            applied.append(v)

    return applied


def _run_versioned_migrations(
    filepaths_by_version: dict[str, str],
    db_name: str | None,
    actual_db_name: str,
    logger: Any,
    dry_run: bool,
    perf: bool,
    migration_conn: Any,
    fencing_token: int,
    heartbeat: Any,
    applied_checksums: set[str],
    defer_snapshots: bool,
    batch_snapshots: int | None,
    config: Any,
) -> tuple[int, str]:
    from dbwarden.engine.checksum import calculate_checksum
    from dbwarden.commands.perf import PhaseTimer

    versioned_count = 0
    latest_version = "0"

    for version, filepath in filepaths_by_version.items():
        filename = filepath.split("/")[-1]
        sql_statements = parse_upgrade_statements(filepath)
        checksum = calculate_checksum(sql_statements)

        if checksum in applied_checksums:
            logger.log_migration_skipped(version, filename, checksum)
            continue

        plan_entry = MigrationPlanEntry(
            version=version,
            description=Path(filepath).stem,
            file_path=Path(filepath),
            checksum=checksum,
            kind="versioned",
            statement_count=len(sql_statements),
        )

        _pre_hooks = _collect_hooks("pre_migration", db_name)
        if _pre_hooks:
            pre_ctx = PreMigrationContext(
                database=actual_db_name,
                command="migrate",
                dry_run=dry_run,
                dev=False,
                migration=plan_entry,
            )
            execute_veto_hook("pre_migration", _pre_hooks, pre_ctx, command="migrate")

        for sql in sql_statements:
            logger.log_sql_statement(sql)
            logger.log_sql_trace(sql)

        if dry_run:
            warning(f"Would apply version {version}: {filename}")
            for statement in sql_statements:
                render_sql(statement)
            continue

        if heartbeat is not None and heartbeat.is_fatal:
            raise LockError(
                "Heartbeat fatal: lease may have been lost on fallback engine. "
                "Aborting migration to prevent concurrent mutation."
            )

        start_time = time.time()
        logger.log_migration_start(version, filename)

        _progress_hooks = _collect_hooks("migration_progress", db_name)
        _stmt_start_time = start_time

        def _on_progress(
            stmt_idx: int, total: int,
            _pe: MigrationPlanEntry = plan_entry,
            _ph: list = _progress_hooks,
            _t: float = _stmt_start_time,
        ) -> None:
            if _ph:
                progress_ctx = MigrationProgressContext(
                    database=actual_db_name,
                    command="migrate",
                    dry_run=dry_run,
                    dev=False,
                    migration=_pe,
                    statement_index=stmt_idx,
                    total_statements=total,
                    progress_pct=(stmt_idx / total * 100) if total else 100.0,
                    elapsed_ms=(time.time() - _t) * 1000,
                )
                execute_observer_hook("migration_progress", _ph, progress_ctx)

        run_migration(
            sql_statements=sql_statements,
            version=version,
            migration_operation="upgrade",
            filename=filename,
            db_name=db_name,
            perf=perf,
            connection=migration_conn,
            namespace="default",
            fencing_token=fencing_token,
            progress_callback=_on_progress,
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

        _post_hooks = _collect_hooks("post_migration", db_name)
        if _post_hooks:
            post_ctx = PostMigrationContext(
                database=actual_db_name,
                command="migrate",
                dry_run=dry_run,
                dev=False,
                migration=plan_entry,
                duration_ms=duration * 1000,
                statements_executed=len(sql_statements),
            )
            execute_observer_hook("post_migration", _post_hooks, post_ctx)

        if config.database_type == "clickhouse" and fencing_token > 0:
            try:
                from dbwarden.lock import get_lock_status
                current_status = get_lock_status(db_name)
                if current_status:
                    current_token = current_status.get("fencing_token", 0)
                    if current_token > fencing_token:
                        warning(
                            f"POSSIBLE_CONCURRENT_EXECUTION: fencing token advanced from "
                            f"{fencing_token} to {current_token} during migration run. "
                            f"Another worker may have executed DDL concurrently."
                        )
            except Exception as exc:
                logger.debug("Could not check fencing token after migration: %s", exc)

    return versioned_count, latest_version


def _fire_dry_run_hooks(
    runs_always_filepaths: list[str],
    runs_on_change_filepaths: list[str],
    db_name: str | None,
    actual_db_name: str,
) -> None:
    for filepath in runs_always_filepaths:
        sql_statements = parse_upgrade_statements(filepath)
        plan_entry = MigrationPlanEntry(
            version=None,
            description=Path(filepath).stem,
            file_path=Path(filepath),
            checksum="",
            kind="runs_always",
            statement_count=len(sql_statements),
        )
        _pre_hooks = _collect_hooks("pre_migration", db_name)
        if _pre_hooks:
            pre_ctx = PreMigrationContext(
                database=actual_db_name,
                command="migrate",
                dry_run=True,
                dev=False,
                migration=plan_entry,
            )
            execute_veto_hook("pre_migration", _pre_hooks, pre_ctx, command="migrate")
    for filepath in runs_on_change_filepaths:
        sql_statements = parse_upgrade_statements(filepath)
        plan_entry = MigrationPlanEntry(
            version=None,
            description=Path(filepath).stem,
            file_path=Path(filepath),
            checksum="",
            kind="runs_on_change",
            statement_count=len(sql_statements),
        )
        _pre_hooks = _collect_hooks("pre_migration", db_name)
        if _pre_hooks:
            pre_ctx = PreMigrationContext(
                database=actual_db_name,
                command="migrate",
                dry_run=True,
                dev=False,
                migration=plan_entry,
            )
            execute_veto_hook("pre_migration", _pre_hooks, pre_ctx, command="migrate")


def _run_always_migrations(
    runs_always_filepaths: list[str],
    db_name: str | None,
    actual_db_name: str,
    logger: Any,
    dry_run: bool,
    perf: bool,
    migration_conn: Any,
    fencing_token: int,
    existing_runs_always: set[str],
) -> None:
    for filepath in runs_always_filepaths:
        filename = filepath.split("/")[-1]
        sql_statements = parse_upgrade_statements(filepath)

        plan_entry = MigrationPlanEntry(
            version=None,
            description=Path(filepath).stem,
            file_path=Path(filepath),
            checksum="",
            kind="runs_always",
            statement_count=len(sql_statements),
        )

        _pre_hooks = _collect_hooks("pre_migration", db_name)
        if _pre_hooks:
            pre_ctx = PreMigrationContext(
                database=actual_db_name,
                command="migrate",
                dry_run=dry_run,
                dev=False,
                migration=plan_entry,
            )
            execute_veto_hook("pre_migration", _pre_hooks, pre_ctx, command="migrate")

        start_time = time.time()
        logger.log_migration_start("RA", filename)

        if filename in existing_runs_always:
            run_repeatable_migration(
                sql_statements=sql_statements,
                filename=filename,
                migration_type="runs_always",
                db_name=db_name,
                perf=perf,
                connection=migration_conn,
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
                connection=migration_conn,
                namespace="default",
                fencing_token=fencing_token,
            )

        duration = time.time() - start_time
        logger.log_migration_end("RA", filename, duration)

        _post_hooks = _collect_hooks("post_migration", db_name)
        if _post_hooks:
            post_ctx = PostMigrationContext(
                database=actual_db_name,
                command="migrate",
                dry_run=dry_run,
                dev=False,
                migration=plan_entry,
                duration_ms=duration * 1000,
                statements_executed=len(sql_statements),
            )
            execute_observer_hook("post_migration", _post_hooks, post_ctx)


def _run_on_change_migrations(
    runs_on_change_filepaths: list[str],
    db_name: str | None,
    actual_db_name: str,
    logger: Any,
    dry_run: bool,
    perf: bool,
    migration_conn: Any,
    fencing_token: int,
) -> None:
    for filepath in runs_on_change_filepaths:
        filename = filepath.split("/")[-1]
        sql_statements = parse_upgrade_statements(filepath)

        plan_entry = MigrationPlanEntry(
            version=None,
            description=Path(filepath).stem,
            file_path=Path(filepath),
            checksum="",
            kind="runs_on_change",
            statement_count=len(sql_statements),
        )

        _pre_hooks = _collect_hooks("pre_migration", db_name)
        if _pre_hooks:
            pre_ctx = PreMigrationContext(
                database=actual_db_name,
                command="migrate",
                dry_run=dry_run,
                dev=False,
                migration=plan_entry,
            )
            execute_veto_hook("pre_migration", _pre_hooks, pre_ctx, command="migrate")

        start_time = time.time()
        logger.log_migration_start("ROC", filename)

        run_repeatable_migration(
            sql_statements=sql_statements,
            filename=filename,
            migration_type="runs_on_change",
            db_name=db_name,
            perf=perf,
            connection=migration_conn,
        )

        duration = time.time() - start_time
        logger.log_migration_end("ROC", filename, duration)

        _post_hooks = _collect_hooks("post_migration", db_name)
        if _post_hooks:
            post_ctx = PostMigrationContext(
                database=actual_db_name,
                command="migrate",
                dry_run=dry_run,
                dev=False,
                migration=plan_entry,
                duration_ms=duration * 1000,
                statements_executed=len(sql_statements),
            )
            execute_observer_hook("post_migration", _post_hooks, post_ctx)


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
    force: bool = False,
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
    from dbwarden.plugin import validate_migration_hooks

    config = get_database(db_name)
    sqlalchemy_url = config.sqlalchemy_url
    actual_db_name = db_name or config.sqlalchemy_url.split("/")[-1].split("?")[0]

    # Section 11, step 6: Validate merged lifecycle hook registry before command runs
    validate_migration_hooks(db_name)

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
    _lock_strategy = None
    _heartbeat = None
    _migration_conn = None
    _fencing_token = 0
    _signal_handler = None

    # Install signal handlers for graceful shutdown (Sec 8.3.5)
    # SIGTERM/SIGINT: finish current statement, record progress, release lock, exit 75
    _signal_handler = MigrationSignalHandler()
    _signal_handler.install()

    try:
        _migration_start_time = time.time()
        migrations_dir = get_migrations_directory(db_name)

        # Section 7.1: Steps 1-2 run BEFORE lock acquisition (static checks)
        # Step 1: Validate migration files, checksums, headers, parseability
        # Step 2: Detect merge conflicts, collisions, superseded, MERGE_PENDING
        # Section 13.3: Dry-run runs core static artifact and merge checks.
        from dbwarden.merge.detection import check_dirty_environment
        if check_dirty_environment(db_name):
            if dry_run:
                warning(
                    f"Environment '{db_name or 'default'}' has unreconciled merge changes. "
                    "Run 'dbwarden reconcile' first."
                )
            else:
                error(
                    f"Environment '{db_name or 'default'}' has unreconciled merge changes. "
                    "Run 'dbwarden reconcile' first, or use --dry-run to preview."
                )
                return

        if not dry_run:
            # Step 3: Acquire the target database lock
            with PhaseTimer(logger, "Lock acquisition", perf=perf):
                create_migrations_table_if_not_exists(db_name)
                create_lock_table_if_not_exists(db_name)

                lock_result = acquire_lock(
                    db_name,
                    migration_version=None,
                    migration_checksum=None,
                )
                if not lock_result.acquired:
                    raise LockError(
                        f"Could not acquire migration lock. "
                        f"{lock_result.holder_description}"
                    )
                lock_acquired = True
                lock_owner = lock_result.owner_id
                _lock_strategy = lock_result.strategy
                _fencing_token = lock_result.fencing_token

                # Start heartbeat after successful lock acquisition.
                # SQLite uses BEGIN IMMEDIATE for the entire migration, so
                # a separate heartbeat connection cannot write the status row.
                # Staleness on SQLite is inferred from acquired_at + process liveness.
                if config.database_type != "sqlite":
                    from dbwarden.lock.heartbeat import HeartbeatTask
                    _heartbeat = HeartbeatTask(
                        db_name=db_name,
                        execution_id=lock_result.execution_id,
                        owner_id=lock_result.owner_id,
                        fencing_token=lock_result.fencing_token,
                    )
                    _heartbeat.start()

                # Open long-lived migration connection for DDL execution.
                # For SQLite, reuse the lock connection which already holds
                # BEGIN IMMEDIATE; a new connection would block on the write lock.
                if config.database_type == "sqlite" and lock_result.connection is not None:
                    _migration_conn = lock_result.connection
                else:
                    from dbwarden.connection.connection import hold_migration_connection
                    _migration_conn = hold_migration_connection(db_name)

            # Section 7.1: Backup after lock acquisition, before DDL
            if with_backup and not sandbox:
                backup_directory = backup_dir or os.path.join(os.getcwd(), "backups")
                backup_path = create_backup(sqlalchemy_url, backup_directory)
                logger.log_backup_created(backup_path)

        applied_versions = set()
        applied_checksums = set()
        if not dry_run:
            applied_versions = set(get_migrated_versions(db_name))
            applied_checksums = get_applied_checksums(db_name)

        if baseline:
            if not to_version:
                raise ValueError("--baseline requires --to-version to be specified.")

            applied = set_baseline_migration(migrations_dir, to_version, db_name, connection=_migration_conn)
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

        # Phase 8.1: Preflight checks on exact pending batch (Section 13.3)
        # Non-mutating policy checks run in dry-run mode; blocking only prevents DDL.
        # Section 7.1 Step 1: File validation always runs; policy gates are optional.
        if filepaths_by_version:
            from dbwarden.config import get_project_config

            project_config = get_project_config()

            # Step 1: Always run file validation (existence, readability, parseability)
            # Step 5: Policy gates only run when project_config exists
            if project_config:
                preflight = run_preflight(
                    filepaths_by_version,
                    missing_plan=project_config.get("missing_plan", "warn"),
                    impact_paths=project_config.get("impact_paths"),
                    migrations_dir=migrations_dir,
                    applied_versions=applied_versions,
                )
            else:
                preflight = run_preflight(
                    filepaths_by_version,
                    missing_plan="off",
                    impact_paths=None,
                    migrations_dir=migrations_dir,
                    applied_versions=applied_versions,
                )

            for w in preflight.warnings:
                warning(w)
            if preflight.errors:
                for e in preflight.errors:
                    error(e)
            if preflight.abort:
                if dry_run:
                    warning("Preflight checks would abort this migration.")
                else:
                    error("Migration aborted by preflight checks.")
                    return

            # Section 7.3: --force acknowledgement for safety warnings
            # WARNING blocks when pre_migrate_safety="block" unless --force
            if (
                project_config
                and not force
                and project_config.get("pre_migrate_safety") == "block"
                and preflight.warnings
            ):
                if dry_run:
                    warning(
                        "Safety warnings detected (would abort without --force)."
                    )
                else:
                    error(
                        "Migration aborted: safety warnings detected. "
                        "Use --force to acknowledge and proceed."
                    )
                    return

            if project_config and preflight.impact:
                for imp in preflight.impact:
                    refs = imp.get("references", [])
                    if refs:
                        warning(
                            f"{imp['table']}: {len(refs)} references found"
                        )

        from dbwarden.engine.checksum import calculate_checksum

        # Build pending migrations list for hooks
        pending_entries: list[MigrationPlanEntry] = []
        for version, filepath in filepaths_by_version.items():
            sql_stmts = parse_upgrade_statements(filepath)
            pending_entries.append(
                MigrationPlanEntry(
                    version=version,
                    description=Path(filepath).stem,
                    file_path=Path(filepath),
                    checksum=calculate_checksum(sql_stmts),
                    kind="versioned",
                    statement_count=len(sql_stmts),
                )
            )

        # Execute pre_migration_run hooks
        _run_hooks = _collect_hooks("pre_migration_run", db_name)
        if _run_hooks:
            pre_run_ctx = PreMigrationRunContext(
                database=actual_db_name,
                command="migrate",
                dry_run=dry_run,
                dev=False,
                pending_migrations=tuple(pending_entries),
            )
            execute_veto_hook("pre_migration_run", _run_hooks, pre_run_ctx, command="migrate")

        versioned_count, latest_version = _run_versioned_migrations(
            filepaths_by_version=filepaths_by_version,
            db_name=db_name,
            actual_db_name=actual_db_name,
            logger=logger,
            dry_run=dry_run,
            perf=perf,
            migration_conn=_migration_conn,
            fencing_token=_fencing_token,
            heartbeat=_heartbeat,
            applied_checksums=applied_checksums,
            defer_snapshots=defer_snapshots,
            batch_snapshots=None,
            config=config,
        )

        if dry_run:
            info(
                "Dry-run summary: "
                f"{len(filepaths_by_version)} versioned, "
                f"{len(runs_always_filepaths)} runs-always, "
                f"{len(runs_on_change_filepaths)} runs-on-change "
                "migrations would be applied."
            )
            _fire_dry_run_hooks(
                runs_always_filepaths, runs_on_change_filepaths,
                db_name, actual_db_name,
            )
            return

        existing_runs_always = get_existing_runs_always_filenames(db_name)

        _run_always_migrations(
            runs_always_filepaths=runs_always_filepaths,
            db_name=db_name,
            actual_db_name=actual_db_name,
            logger=logger,
            dry_run=dry_run,
            perf=perf,
            migration_conn=_migration_conn,
            fencing_token=_fencing_token,
            existing_runs_always=existing_runs_always,
        )

        _run_on_change_migrations(
            runs_on_change_filepaths=runs_on_change_filepaths,
            db_name=db_name,
            actual_db_name=actual_db_name,
            logger=logger,
            dry_run=dry_run,
            perf=perf,
            migration_conn=_migration_conn,
            fencing_token=_fencing_token,
        )

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

        # Execute post_migration_run hooks (Section 13.3: not in dry-run)
        if not dry_run:
            _post_run_hooks = _collect_hooks("post_migration_run", db_name)
            if _post_run_hooks:
                post_run_ctx = PostMigrationRunContext(
                    database=actual_db_name,
                    command="migrate",
                    dry_run=dry_run,
                    dev=False,
                    applied_migrations=tuple(versioned_count > 0 and [latest_version] or []),
                    total_duration_ms=(time.time() - _migration_start_time) * 1000 if '_migration_start_time' in dir() else 0,
                )
                execute_observer_hook("post_migration_run", _post_run_hooks, post_run_ctx)

    except Exception as exc:
        # Execute on_migration_failure hooks
        _failure_hooks = _collect_hooks("on_migration_failure", db_name)
        if _failure_hooks:
            failure_ctx = MigrationFailureContext(
                database=actual_db_name,
                command="migrate",
                dry_run=dry_run,
                dev=False,
                migration=None,
                error=exc,
                statements_executed_before_failure=0,
            )
            exc = execute_failure_hook("on_migration_failure", _failure_hooks, failure_ctx, exc)

        # Connection loss handling (Sec 8.3.2): Map connection-loss errors
        # to immediate abort. A reconnected worker holds no lock and could
        # mutate unsafely, so we must NOT reconnect.
        import sqlalchemy.exc as sa_exc
        if isinstance(exc, (sa_exc.DBAPIError, sa_exc.OperationalError, sa_exc.InterfaceError)):
            logger.error(
                "Connection lost during migration. Aborting immediately. "
                "Do NOT reconnect: a fresh connection would hold no lock."
            )
            raise LockError(
                f"Connection lost during migration: {exc}. "
                "Aborting for safety. A reconnected worker would hold no lock."
            ) from exc
        if metrics_enabled():
            increment_migration_errors(actual_db_name)
        raise
    finally:
        # Restore signal handlers
        if _signal_handler is not None:
            _signal_handler.uninstall()

        # Stop heartbeat before releasing the lock
        if _heartbeat is not None:
            _heartbeat.stop()
        # Close migration connection before releasing the lock
        # (on native-lock engines, closing releases the session-scoped lock)
        if _migration_conn is not None:
            try:
                _migration_conn.close()
            except Exception:
                pass
        if lock_acquired:
            if not release_lock(db_name, strategy=_lock_strategy):
                logger.error("Migration lock was not released by owner %s", lock_owner)

        # If signal was received, exit with code 75 (EX_TEMPFAIL)
        if _signal_handler is not None and _signal_handler.is_interrupted:
            logger.warning("Migration interrupted by signal, exiting with code 75")
            sys.exit(75)
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
    force: bool = False,
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
        force: Acknowledge warning-level safety issues (Section 7.3).
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
                    force=force,
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
            force=force,
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
    from dbwarden.merge.marker import is_superseded

    if migrations_dir is None:
        migrations_dir = get_migrations_directory(db_name)

    ordered = resolve_migration_order(
        directory=migrations_dir,
        applied_versions=applied_versions or set(),
    )

    # Skip superseded files (Phase 5: integration with merge handling)
    filepaths = {}
    for version, filepath, _deps, _seed in ordered:
        if not is_superseded(filepath):
            filepaths[version] = filepath

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
