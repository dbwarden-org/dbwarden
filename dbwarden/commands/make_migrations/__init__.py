import re
from contextlib import nullcontext
from pathlib import Path

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
    _build_domain_sql,
    _build_sequence_sql,
    _drop_domain_sql,
    _drop_sequence_sql,
    _run_offline_migrations,
    generate_migration_sql,
    get_current_model_state_path,
    get_model_state_path,
    get_pending_migration_statements,
)
from dbwarden.commands.make_migrations.prompts import (
    _detect_table_rename_candidates,
    _prompt_rename_confirmations,
    _prompt_table_rename_confirmations,
)
from dbwarden.config import get_database, get_multi_db_config
from dbwarden.constants import RUNS_ALWAYS_FILE_PREFIX, RUNS_ON_CHANGE_FILE_PREFIX
from dbwarden.engine.discovery import auto_discover_model_paths, get_all_model_tables
from dbwarden.engine.version import (
    generate_migration_filename,
    generate_repeatable_filename,
    get_migrations_directory,
    get_next_migration_number,
)
from dbwarden.files import atomic_write_text
from dbwarden.logging import get_logger
from dbwarden.output import success

__all__ = [
    "RenameIntent",
    "_build_domain_sql",
    "_build_sequence_sql",
    "_build_table_rename_ops",
    "_check_migration_scope",
    "_check_recreate_rename_conflict",
    "_detect_table_rename_candidates",
    "_drop_domain_sql",
    "_drop_sequence_sql",
    "_format_rename_warning",
    "_format_table_rename_warning",
    "_parse_rename_flags",
    "_parse_rename_table_flags",
    "_prompt_rename_confirmations",
    "_prompt_table_rename_confirmations",
    "_resolve_clickhouse_recreate_ops",
    "_resolve_migration_description",
    "_run_offline_migrations",
    "_validate_table_rename_intents",
    "auto_discover_model_paths",
    "build_migration_plan",
    "generate_migration_sql",
    "get_all_model_tables",
    "get_current_model_state_path",
    "get_database",
    "get_model_state_path",
    "get_pending_migration_statements",
    "make_migrations_cmd",
    "new_migration_cmd",
]


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
    perf: bool = False,
    split_at_severity: str | None = None,
    strict_pending: bool | None = None,
    dry_run: bool = False,
    parameters: dict | None = None,
    show_managed_values: bool = False,
) -> None:
    from dbwarden.commands.make_migrations.generation import run_generation
    from dbwarden.commands.perf import PhaseTimer

    logger = get_logger(verbose=verbose)
    timing = (
        PhaseTimer(logger, "Migration generation", perf=perf)
        if perf and not output_plan and not output_sql
        else nullcontext()
    )
    with timing:
        run_generation(
            description=description,
            database=database,
            output_plan=output_plan,
            output_sql=output_sql,
            rename_flags=rename_flags,
            safe_type_change=safe_type_change,
            rename_table_flags=rename_table_flags,
            concurrent=concurrent,
            offline=offline,
            migration_type=migration_type,
            clickhouse_engine_recreate=clickhouse_engine_recreate,
            drop_preserved_clickhouse_table=drop_preserved_clickhouse_table,
            postgres_auto_using=postgres_auto_using,
            split_at_severity=split_at_severity,
            strict_pending=strict_pending,
            dry_run=dry_run,
            parameters=parameters,
            show_managed_values=show_managed_values,
            verbose=verbose,
            perf=perf,
        )


def new_migration_cmd(
    description: str,
    version: str | None = None,
    database: str | None = None,
    migration_type: str = "versioned",
) -> None:
    logger = get_logger()
    db_name = database or get_multi_db_config().default
    migrations_dir = get_migrations_directory(database)
    safe_description = re.sub(r"[^a-zA-Z0-9]", "_", description).lower()
    safe_description = re.sub(r"_+", "_", safe_description).strip("_")
    if not safe_description:
        raise ValueError(
            "Migration description cannot be empty or contain only special characters."
        )
    if migration_type in ("runs_always", "ra", "runs_on_change", "roc"):
        if version is not None:
            logger.warning("--version is ignored for repeatable migrations (RA/ROC).")
        prefix = (
            RUNS_ALWAYS_FILE_PREFIX
            if migration_type in ("runs_always", "ra")
            else RUNS_ON_CHANGE_FILE_PREFIX
        )
        filename = generate_repeatable_filename(db_name, safe_description, prefix)
    else:
        filename = generate_migration_filename(
            db_name,
            safe_description,
            version or get_next_migration_number(migrations_dir),
        )
    filepath = Path(migrations_dir) / filename
    if not filepath.resolve().is_relative_to(Path(migrations_dir).resolve()):
        raise ValueError(
            f"Invalid migration path: {filename} resolves outside migrations directory. Path traversal not allowed."
        )
    atomic_write_text(
        filepath, f"-- upgrade\n\n-- {description}\n\n-- rollback\n\n-- {description}\n"
    )
    success(f"Created migration file: {filepath}")
