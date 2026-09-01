from dbwarden.commands.check import check_cmd
from dbwarden.commands.check_impact import check_impact_cmd
from dbwarden.commands.check_db import check_db_cmd
from dbwarden.commands.downgrade import downgrade_cmd
from dbwarden.commands.export_models import export_models_cmd
from dbwarden.commands.extra import diff_cmd, lock_status_cmd, unlock_cmd
from dbwarden.commands.generate_models import generate_models_cmd
from dbwarden.commands.history import history_cmd
from dbwarden.commands.init import init_cmd
from dbwarden.commands.make_migrations import make_migrations_cmd, new_migration_cmd
from dbwarden.commands.make_rollback import make_rollback_cmd
from dbwarden.commands.migrate import migrate_cmd
from dbwarden.commands.plugin_cmd import (
    plugin_add_cmd,
    plugin_info_cmd,
    plugin_list_cmd,
    plugin_remove_cmd,
    plugin_trust_cmd,
    plugin_untrust_cmd,
)
from dbwarden.commands.recover_model_state import recover_model_state_cmd
from dbwarden.commands.rollback import rollback_cmd
from dbwarden.commands.snapshot import snapshot_cmd
from dbwarden.commands.export_seeds import export_seeds_cmd
from dbwarden.commands.seeds import (
    seed_apply_cmd,
    seed_create_cmd,
    seed_list_cmd,
    seed_rollback_cmd,
)
from dbwarden.commands.settings import handle_settings_show
from dbwarden.commands.status import status_cmd
from dbwarden.commands.utils import config_cmd, version_cmd
from dbwarden.output import json_mode


def handle_database_list() -> None:
    """List all configured databases."""
    handle_settings_show(database=None, all_databases=True)


def handle_init(database: str | None = None) -> None:
    """Handle init command."""
    init_cmd(database=database)


def handle_make_migrations(
    description: str | None,
    verbose: bool,
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
) -> None:
    """Handle make-migrations command."""
    make_migrations_cmd(
        description=description,
        verbose=verbose,
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
        perf=perf,
    )


def handle_new(
    description: str, version: str | None, database: str | None = None, migration_type: str = "versioned"
) -> None:
    """Handle new command."""
    new_migration_cmd(
        description=description, version=version, database=database, migration_type=migration_type,
    )


def handle_migrate(
    count: int | None,
    to_version: str | None,
    verbose: bool,
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
    """Handle migrate command."""
    migrate_cmd(
        count=count,
        to_version=to_version,
        verbose=verbose,
        database=database,
        all_databases=all_databases,
        baseline=baseline,
        with_backup=with_backup,
        backup_dir=backup_dir,
        dry_run=dry_run,
        sandbox=sandbox,
        apply_seeds=apply_seeds,
        perf=perf,
        defer_snapshots=defer_snapshots,
    )


def handle_rollback(
    count: int | None,
    to_version: str | None,
    verbose: bool,
    database: str | None = None,
    perf: bool = False,
) -> None:
    """Handle rollback command."""
    rollback_cmd(count=count, to_version=to_version, verbose=verbose, database=database, perf=perf)


def handle_history(database: str | None = None) -> None:
    """Handle history command."""
    history_cmd(database=database)


def handle_status(database: str | None = None, all_databases: bool = False, all_environments: bool = False) -> None:
    """Handle status command."""
    status_cmd(database=database, all_databases=all_databases, all_environments=all_environments)


def handle_check_db(output_format: str, database: str | None = None) -> None:
    """Handle check-db command."""
    if json_mode() and output_format != "json":
        output_format = "json"
    check_db_cmd(output_format=output_format, database=database)


def handle_check(
    output_format: str,
    database: str | None = None,
    force: bool = False,
) -> None:
    """Handle safety check command."""
    if json_mode() and output_format != "json":
        output_format = "json"
    check_cmd(output_format=output_format, database=database, force=force)


def handle_check_impact(
    migration: str,
    out: str = "text",
    scan_path: str = ".",
    deep: bool = False,
    verbose: bool = False,
    database: str | None = None,
) -> None:
    """Handle check-impact command."""
    if json_mode() and out != "json":
        out = "json"
    check_impact_cmd(
        migration=migration, out=out, scan_path=scan_path,
        deep=deep, verbose=verbose, database=database,
    )


def handle_diff(
    output_format: str = "table",
    verbose: bool = False,
    database: str | None = None,
    offline: bool = False,
) -> None:
    """Handle diff command."""
    if json_mode() and output_format != "json":
        output_format = "json"
    diff_cmd(output_format=output_format, verbose=verbose, database=database, offline=offline)


def handle_config() -> None:
    """Handle config command."""
    config_cmd()


def handle_version() -> None:
    """Handle version command."""
    version_cmd()


def handle_lock_status(database: str | None = None) -> None:
    """Handle lock-status command."""
    lock_status_cmd(database=database)


def handle_unlock(database: str | None = None, force: bool = False) -> None:
    """Handle unlock command."""
    unlock_cmd(database=database, force=force)


def handle_settings_show_command(
    database: str | None = None,
    all_databases: bool = False,
) -> None:
    handle_settings_show(database=database, all_databases=all_databases)


def handle_downgrade(
    to_version: str,
    verbose: bool = False,
    database: str | None = None,
    perf: bool = False,
) -> None:
    downgrade_cmd(to_version=to_version, verbose=verbose, database=database, perf=perf)


def handle_make_rollback(
    migration_file: str,
) -> None:
    make_rollback_cmd(migration_file=migration_file)


def handle_snapshot(
    table_name: str,
    database: str | None = None,
) -> None:
    snapshot_cmd(table_name=table_name, database=database)


def handle_generate_models(
    output: str = "models",
    tables: str | None = None,
    exclude_tables: str | None = None,
    clickhouse_engines: bool = False,
    relationships: bool = False,
    dialect: str | None = None,
    single_file: bool = False,
    base: str | None = None,
    database: str | None = None,
) -> None:
    if not clickhouse_engines:
        from dbwarden.config import get_database, ConfigurationError
        try:
            config = get_database(database)
            if config.database_type == "clickhouse":
                clickhouse_engines = True
        except ConfigurationError:
            pass
    generate_models_cmd(
        output=output,
        tables=tables,
        exclude_tables=exclude_tables,
        clickhouse_engines=clickhouse_engines,
        relationships=relationships,
        dialect=dialect,
        single_file=single_file,
        base=base,
        database=database,
    )


def handle_export_models(
    output: str | None = None,
    database: str | None = None,
) -> None:
    """Handle export-models command."""
    export_models_cmd(output=output, database=database)


def handle_recover_model_state(
    database: str | None = None,
) -> None:
    """Handle recover-model-state command."""
    recover_model_state_cmd(database=database)


def handle_seed_create(
    description: str,
    seed_type: str = "sql",
    database: str | None = None,
    verbose: bool = False,
) -> None:
    seed_create_cmd(
        description=description,
        seed_type=seed_type,
        database=database,
        verbose=verbose,
    )


def handle_seed_apply(
    version: str | None = None,
    dry_run: bool = False,
    database: str | None = None,
    all_databases: bool = False,
    verbose: bool = False,
) -> None:
    seed_apply_cmd(
        version=version,
        dry_run=dry_run,
        database=database,
        all_databases=all_databases,
        verbose=verbose,
    )


def handle_seed_list(
    database: str | None = None,
    all_databases: bool = False,
    verbose: bool = False,
    prune: bool = False,
) -> None:
    seed_list_cmd(
        database=database,
        all_databases=all_databases,
        verbose=verbose,
        prune=prune,
    )


def handle_seed_rollback(
    count: int | None = None,
    to_version: str | None = None,
    database: str | None = None,
    all_databases: bool = False,
    verbose: bool = False,
) -> None:
    seed_rollback_cmd(
        count=count,
        to_version=to_version,
        database=database,
        all_databases=all_databases,
        verbose=verbose,
    )


def handle_seed_export(
    database: str | None = None,
    all_databases: bool = False,
    output_dir: str = "seeds",
) -> None:
    """Handle seed export command."""
    export_seeds_cmd(
        database=database,
        all_databases=all_databases,
        output_dir=output_dir,
    )


def handle_plugin_list(output_format: str = "table") -> None:
    if json_mode() and output_format == "table":
        output_format = "json"
    plugin_list_cmd(output_format=output_format)


def handle_plugin_info(dist_name: str, output_format: str = "table") -> None:
    if json_mode() and output_format == "table":
        output_format = "json"
    plugin_info_cmd(dist_name, output_format=output_format)


def handle_plugin_trust(dist_name: str) -> None:
    plugin_trust_cmd(dist_name)


def handle_plugin_untrust(dist_name: str) -> None:
    plugin_untrust_cmd(dist_name)


def handle_plugin_add(
    dist_name: str,
    use_uv: bool = False,
    version: str | None = None,
    dry_run: bool = False,
) -> None:
    plugin_add_cmd(dist_name, use_uv=use_uv, version=version, dry_run=dry_run)


def handle_plugin_remove(
    dist_name: str,
    use_uv: bool = False,
    dry_run: bool = False,
) -> None:
    plugin_remove_cmd(dist_name, use_uv=use_uv, dry_run=dry_run)
