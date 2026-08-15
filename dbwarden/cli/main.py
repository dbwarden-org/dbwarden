import typer

from dbwarden.cli.validators import validate_directory
from dbwarden.config import set_dev_mode, set_disable_skip, set_strict_translation
from dbwarden.commands import (
    handle_check_db,
    handle_check,
    handle_check_impact,
    handle_config,
    handle_database_list,
    handle_diff,
    handle_downgrade,
    handle_export_models,
    handle_generate_models,
    handle_history,
    handle_init,
    handle_lock_status,
    handle_make_migrations,
    handle_make_rollback,
    handle_migrate,
    handle_new,
    handle_plugin_add,
    handle_plugin_info,
    handle_plugin_list,
    handle_plugin_remove,
    handle_plugin_trust,
    handle_plugin_untrust,
    handle_recover_model_state,
    handle_rollback,
    handle_seed_apply,
    handle_seed_create,
    handle_seed_export,
    handle_seed_list,
    handle_seed_rollback,
    handle_snapshot,
    handle_status,
    handle_unlock,
    handle_version,
    handle_settings_show_command,
)
from dbwarden.logging import enable_json_logging, get_logger, resolve_debug_level
from dbwarden.output import set_output_mode
from dbwarden.plugin import load_plugins

app = typer.Typer(
    help="""dbwarden - Professional database migration system for SQLAlchemy models

All commands support the --verbose / -v flag for detailed output and the
--debug / --debug-level flags for DEBUG-level diagnostics.""",
    add_completion=False,
)

database_app = typer.Typer(help="List configured databases")
app.add_typer(database_app, name="database")
settings_app = typer.Typer(help="View dbwarden settings")
app.add_typer(settings_app, name="settings")
seed_app = typer.Typer(help="Manage seed data")
app.add_typer(seed_app, name="seed")
plugin_app = typer.Typer(help="Manage dbwarden plugins")
app.add_typer(plugin_app, name="plugin")


@app.callback()
def app_callback(
    ctx: typer.Context,
    dev: bool = typer.Option(
        False,
        "--dev",
        help="Use development database settings (dev_database_url/dev_database_type)",
    ),
    strict_translation: bool = typer.Option(
        False,
        "--strict-translation",
        help="Fail when a type/default cannot be translated for target backend",
    ),
    disable_skip: bool = typer.Option(
        False,
        "--disable-skip",
        help="Do not skip databases configured with skip_if_missing.",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Enable DEBUG-level logging",
    ),
    debug_level: str | None = typer.Option(
        None,
        "--debug-level",
        metavar="LEVEL",
        help="Exact log level: trace, debug, info, warning, error, critical, or 5/10/20/30/40/50",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Render command output (and logs) as structured JSON",
    ),
):
    """Global CLI options."""
    set_dev_mode(dev)
    set_disable_skip(disable_skip)
    set_strict_translation(strict_translation)
    if json_output:
        set_output_mode("json")
        enable_json_logging()
    _apply_cli_logging(debug=debug, debug_level=debug_level)
    if ctx.invoked_subcommand != "plugin":
        load_plugins(interactive=True)


def _apply_cli_logging(*, debug: bool, debug_level: str | None) -> None:
    """Resolve and apply --debug / --debug-level to the global logger.

    ``--debug-level`` takes precedence over the bare ``--debug`` switch. The
    severity level is orthogonal to ``--verbose``: verbosity is applied by each
    command through ``get_logger(verbose=...)``.
    """
    level: int | None = None
    if debug_level is not None:
        try:
            level = resolve_debug_level(debug_level)
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_hint="--debug-level") from exc
    elif debug:
        level = resolve_debug_level("debug")
    if level is not None:
        get_logger(debug_level=level)


@plugin_app.command("list")
def plugin_list(
    output_format: str = typer.Option(
        "table", "--format", "-f", help="Output format: table (default) or json"
    ),
) -> None:
    """List installed dbwarden plugins."""
    handle_plugin_list(output_format=output_format)


@plugin_app.command("info")
def plugin_info(
    dist_name: str = typer.Argument(..., help="Plugin distribution name"),
    output_format: str = typer.Option(
        "table", "--format", "-f", help="Output format: table (default) or json"
    ),
) -> None:
    """Show plugin metadata."""
    handle_plugin_info(dist_name, output_format=output_format)


@plugin_app.command("trust")
def plugin_trust(
    dist_name: str = typer.Argument(..., help="Community plugin distribution name"),
) -> None:
    """Allow a community plugin to load."""
    handle_plugin_trust(dist_name)


@plugin_app.command("untrust")
def plugin_untrust(
    dist_name: str = typer.Argument(..., help="Community plugin distribution name"),
) -> None:
    """Remove trust for a community plugin."""
    handle_plugin_untrust(dist_name)


@plugin_app.command("add")
def plugin_add(
    dist_name: str = typer.Argument(..., help="Plugin distribution name"),
    use_uv: bool = typer.Option(
        False, "--uv", help="Install with `uv add` instead of pip"
    ),
    plugin_version: str | None = typer.Option(
        None, "--version", help="Pin an exact version to install (e.g. 1.2.0)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the install plan without installing"
    ),
) -> None:
    """Install a dbwarden plugin."""
    handle_plugin_add(dist_name, use_uv=use_uv, version=plugin_version, dry_run=dry_run)


@plugin_app.command("remove")
def plugin_remove(
    dist_name: str = typer.Argument(..., help="Plugin distribution name"),
    use_uv: bool = typer.Option(
        False, "--uv", help="Uninstall with `uv remove` instead of pip"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the uninstall plan without uninstalling"
    ),
) -> None:
    """Uninstall a dbwarden plugin."""
    handle_plugin_remove(dist_name, use_uv=use_uv, dry_run=dry_run)


@app.command()
def init(
    database: str | None = typer.Option(
        None, "--database", "-d", help="Database name to create migration directory for"
    ),
):
    """Initialize the migrations directory."""
    handle_init(database=database)


@database_app.command("list")
def database_list():
    """List all configured databases."""
    handle_database_list()


@settings_app.command("show")
def settings_show(
    database: str | None = typer.Argument(None, help="Database name"),
    all_databases: bool = typer.Option(False, "--all", help="Show all databases"),
):
    """Show current settings configuration."""
    handle_settings_show_command(database=database, all_databases=all_databases)


@app.command()
def generate_models(
    output: str = typer.Option(
        "models", "--output", "-o", help="Output directory for generated model files"
    ),
    tables: str | None = typer.Option(
        None, "--tables", help="Comma-separated list of tables to include"
    ),
    exclude_tables: str | None = typer.Option(
        None, "--exclude-tables", help="Comma-separated list of tables to exclude"
    ),
    clickhouse_engines: bool = typer.Option(
        False, "--clickhouse-engines", help="Include ClickHouse engine metadata"
    ),
    relationships: bool = typer.Option(
        False, "--relationships", help="Generate relationship attributes"
    ),
    dialect: str | None = typer.Option(
        None, "--dialect", help="SQL dialect for type mapping (auto-detected by default)"
    ),
    single_file: bool = typer.Option(
        False, "--single-file", help="Generate a single models.py file"
    ),
    base: str | None = typer.Option(
        None, "--base",
        help="Custom Base class import path (e.g. 'app.core.database:Base' or 'app.database:DeclarativeBase')",
    ),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
):
    """Reverse-engineer SQLAlchemy model code from a live database."""
    validate_directory()
    handle_generate_models(
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


@app.command()
def export_models(
    output: str | None = typer.Option(
        None, "--output", "-o", help="Output path for the model state JSON file",
    ),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
):
    """Export current model definitions to a JSON state file for offline diffs."""
    validate_directory()
    handle_export_models(output=output, database=database)


@app.command()
def recover_model_state(
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
):
    """Recover a deleted model state file by replaying migrations in a sandbox."""
    validate_directory()
    handle_recover_model_state(database=database)


@app.command()
def make_migrations(
    description: str = typer.Argument(None, help="Description for the migration"),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose logging"
    ),
    plan: bool = typer.Option(
        False,
        "--plan",
        help="Output migration plan JSON without writing files",
    ),
    sql_output: bool = typer.Option(
        False,
        "--sql",
        help="Output raw migration SQL to stdout without writing files",
    ),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
    rename: list[str] = typer.Option(
        [],
        "--rename",
        help="Explicitly rename column (format: table.old_name:new_name). Can be repeated.",
    ),
    safe_type_change: bool = typer.Option(
        False,
        "--safe-type-change",
        help="Use multi-step safe type change (add temp column, backfill, swap, drop old)",
    ),
    rename_table: list[str] = typer.Option(
        [],
        "--rename-table",
        help="Declare a table rename as old_table:new_table. Repeatable.",
    ),
    concurrent: bool = typer.Option(
        True,
        "--concurrent/--no-concurrent",
        help="Use CREATE INDEX CONCURRENTLY on PostgreSQL (disable inside transactions)",
    ),
    offline: bool = typer.Option(
        False,
        "--offline",
        help="Use model state file instead of live database (requires export-models first)",
    ),
    clickhouse_engine_recreate: bool = typer.Option(
        False,
        "--clickhouse-engine-recreate",
        help="Allow automatic ClickHouse table rebuild when engine changes require recreation",
    ),
    drop_preserved_clickhouse_table: bool | None = typer.Option(
        None,
        "--drop-preserved-clickhouse-table/--keep-preserved-clickhouse-table",
        help="Drop the preserved old ClickHouse table after swap; if omitted, prompt in TTY and preserve by default",
    ),
    postgres_auto_using: bool = typer.Option(
        False,
        "--postgres-auto-using",
        help="Emit active USING clause on PostgreSQL ALTER COLUMN TYPE (default: commented-out)",
    ),
    migration_type: str = typer.Option(
        "versioned", "--type", "-t",
        help="Output prefix: versioned (default), runs_always/ra, runs_on_change/roc",
    ),
    perf: bool = typer.Option(
        False, "--perf", help="Log SQL-generation phase timing"
    ),
):
    """Auto-generate SQL migration from SQLAlchemy models."""
    validate_directory()
    handle_make_migrations(
        description=description,
        verbose=verbose,
        database=database,
        output_plan=plan,
        output_sql=sql_output,
        rename_flags=rename,
        safe_type_change=safe_type_change,
        rename_table_flags=rename_table,
        concurrent=concurrent,
        offline=offline,
        clickhouse_engine_recreate=clickhouse_engine_recreate,
        drop_preserved_clickhouse_table=drop_preserved_clickhouse_table,
        postgres_auto_using=postgres_auto_using,
        migration_type=migration_type,
        perf=perf,
    )


@app.command()
def new(
    description: str = typer.Argument(..., help="Description of the migration"),
    version: str | None = typer.Option(
        None, "--version", help="Version of the migration"
    ),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
    migration_type: str = typer.Option(
        "versioned", "--type", "-t",
        help="Migration type: versioned (default), runs_always/ra, runs_on_change/roc",
    ),
):
    """Create a new manual migration file."""
    validate_directory()
    handle_new(
        description=description, version=version, database=database,
        migration_type=migration_type,
    )


@app.command()
def migrate(
    count: int | None = typer.Option(
        None, "--count", "-c", help="Number of migrations to apply"
    ),
    to_version: str | None = typer.Option(
        None, "--to-version", "-t", help="Migrate to a specific version"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose logging"
    ),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
    all_databases: bool = typer.Option(
        False, "--all", "-a", help="Run migrations on all databases sequentially"
    ),
    baseline: bool = typer.Option(
        False, "--baseline", help="Mark migrations as applied without executing"
    ),
    with_backup: bool = typer.Option(
        False, "--with-backup", "-b", help="Create a backup before migrating"
    ),
    backup_dir: str | None = typer.Option(
        None, "--backup-dir", help="Directory for backup files"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be applied without executing"
    ),
    sandbox: bool = typer.Option(
        False, "--sandbox", help="Apply migrations in a temporary sandbox database"
    ),
    apply_seeds: bool = typer.Option(
        False, "--apply-seeds", help="Apply pending seeds after migrations (overrides config)"
    ),
    perf: bool = typer.Option(
        False, "--perf", help="Log per-SQL-statement timing breakdowns"
    ),
    defer_snapshots: bool = typer.Option(
        False,
        "--defer-snapshots",
        help="Write one final schema snapshot instead of one after every migration",
    ),
):
    """Apply pending migrations to the database."""
    validate_directory()
    handle_migrate(
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


@app.command()
def rollback(
    count: int | None = typer.Option(
        None, "--count", "-c", help="Number of migrations to rollback"
    ),
    to_version: str | None = typer.Option(
        None, "--to-version", "-t", help="Rollback to a specific version"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose logging"
    ),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
    perf: bool = typer.Option(
        False, "--perf", help="Log per-SQL-statement timing breakdowns"
    ),
):
    """Rollback the last applied migration."""
    validate_directory()
    handle_rollback(
        count=count, to_version=to_version, verbose=verbose, database=database, perf=perf
    )


@app.command()
def downgrade(
    to_version: str = typer.Option(
        ..., "--to", "-t", help="Target version to downgrade to"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose logging"
    ),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
    perf: bool = typer.Option(
        False, "--perf", help="Log per-SQL-statement timing breakdowns"
    ),
):
    """Downgrade to a specific migration version by reverting applied migrations."""
    validate_directory()
    handle_downgrade(to_version=to_version, verbose=verbose, database=database, perf=perf)


@app.command()
def make_rollback(
    migration_file: str = typer.Argument(
        ..., help="Path to migration SQL file"
    ),
):
    """Generate a rollback SQL file for a given migration file."""
    handle_make_rollback(migration_file=migration_file)


@app.command()
def snapshot(
    table_name: str = typer.Argument(..., help="Table name to snapshot"),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
):
    """Snapshot the DDL schema of a specific table."""
    validate_directory()
    handle_snapshot(table_name=table_name, database=database)


@app.command()
def history(
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
):
    """Show the full migration history."""
    validate_directory()
    handle_history(database=database)


@app.command()
def status(
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
    all_databases: bool = typer.Option(
        False, "--all", "-a", help="Show status for all databases"
    ),
):
    """Show migration status (applied and pending)."""
    validate_directory()
    handle_status(database=database, all_databases=all_databases)


@app.command()
def check(
    output: str = typer.Option(
        "txt", "--out", "-o", help="Output format (json, txt)"
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Allow warning-level changes to pass",
    ),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
):
    """Run the schema safety analyzer."""
    validate_directory()
    handle_check(output_format=output, database=database, force=force)


@app.command()
def check_db(
    output: str = typer.Option(
        "txt", "--out", "-o", help="Output format (json, yaml, sql, txt)"
    ),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
):
    """Inspect the live database schema."""
    validate_directory()
    handle_check_db(output_format=output, database=database)


@app.command()
def check_impact(
    migration: str = typer.Argument(
        ..., help="Migration version or plan file path"
    ),
    out: str = typer.Option(
        "text", "--out", "-o", help="Output format: text (default) or json"
    ),
    scan_path: str = typer.Option(
        ".", "--scan-path", help="Directory to scan for affected code"
    ),
    deep: bool = typer.Option(
        False, "--deep", help="Enable deep introspection (imports models live)"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show INFO-level operations in scan"
    ),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
):
    """Analyze impact of a migration on your codebase."""
    validate_directory()
    handle_check_impact(
        migration=migration, out=out, scan_path=scan_path,
        deep=deep, verbose=verbose, database=database,
    )


@app.command()
def diff(
    output: str = typer.Option(
        "table", "--out", "-o", help="Output format: table (default), json, sql"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose logging"
    ),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
    offline: bool = typer.Option(
        False,
        "--offline",
        help="Use model state file instead of live database snapshot",
    ),
):
    """Show structural differences between models and database (read-only, no files written)."""
    validate_directory()
    handle_diff(
        output_format=output, verbose=verbose, database=database, offline=offline
    )


@app.command()
def config():
    """Display current Python configuration."""
    handle_config()


@app.command()
def version():
    """Display dbwarden version and compatibility information."""
    handle_version()


@app.command()
def lock_status(
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
):
    """Check if migration is currently locked."""
    validate_directory()
    handle_lock_status(database=database)


@app.command()
def unlock(
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
):
    """Release the migration lock."""
    validate_directory()
    handle_unlock(database=database)


@seed_app.command("create")
def seed_create(
    description: str = typer.Argument(..., help="Description for the seed"),
    seed_type: str = typer.Option(
        "sql", "--type", "-t", help="Seed type: sql or python"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose logging"
    ),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
):
    """Create a new seed file."""
    validate_directory()
    handle_seed_create(
        description=description,
        seed_type=seed_type,
        database=database,
        verbose=verbose,
    )


@seed_app.command("apply")
def seed_apply(
    version: str | None = typer.Option(
        None, "--version", "-v", help="Apply a specific seed version"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be applied without executing"
    ),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
    all_databases: bool = typer.Option(
        False, "--all", "-a", help="Apply seeds on all databases"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose logging"
    ),
):
    """Apply pending seeds."""
    validate_directory()
    handle_seed_apply(
        version=version,
        dry_run=dry_run,
        database=database,
        all_databases=all_databases,
        verbose=verbose,
    )


@seed_app.command("list")
def seed_list(
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
    all_databases: bool = typer.Option(
        False, "--all", "-a", help="Show seeds for all databases"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose logging"
    ),
    prune: bool = typer.Option(
        False, "--prune", help="Remove tracking records for seed files that no longer exist"
    ),
):
    """List seed files and their applied status."""
    validate_directory()
    handle_seed_list(
        database=database,
        all_databases=all_databases,
        verbose=verbose,
        prune=prune,
    )


@seed_app.command("export")
def seed_export(
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
    all_databases: bool = typer.Option(
        False, "--all", "-a", help="Export seeds for all configured databases"
    ),
    output_dir: str = typer.Option(
        "seeds", "--output-dir", "-o", help="Output directory for ROC seed files (default: seeds/)"
    ),
):
    """Export code seeds to ROC SQL files for stateless application."""
    handle_seed_export(
        database=database,
        all_databases=all_databases,
        output_dir=output_dir,
    )


@seed_app.command("rollback")
def seed_rollback(
    count: int | None = typer.Option(
        None, "--count", "-c", help="Number of seeds to rollback"
    ),
    to_version: str | None = typer.Option(
        None, "--to-version", "-t", help="Rollback to a specific version"
    ),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name"
    ),
    all_databases: bool = typer.Option(
        False, "--all", "-a", help="Rollback seeds in all databases"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose logging"
    ),
):
    """Rollback seed tracking records (allows re-application)."""
    validate_directory()
    handle_seed_rollback(
        count=count,
        to_version=to_version,
        database=database,
        all_databases=all_databases,
        verbose=verbose,
    )


def main() -> None:
    """Main entry point for dbwarden CLI."""
    from dbwarden.database import reset_connection_logging
    from dbwarden.exceptions import DBWardenError
    from dbwarden.output import emit_error_json, error, json_mode

    reset_connection_logging()
    try:
        app()
    except typer.Exit:
        raise
    except DBWardenError as exc:
        if json_mode():
            emit_error_json(_error_code(exc), str(exc))
        else:
            error(str(exc))
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        if json_mode():
            emit_error_json("internal_error", str(exc))
        else:
            error(str(exc))
        raise typer.Exit(code=1) from exc


def _error_code(exc: Exception) -> str:
    """Return a stable automation-facing code for a domain exception."""
    name = exc.__class__.__name__
    mapping = {
        "ConfigurationError": "configuration_error",
        "DBWardenConfigError": "configuration_error",
        "DBDisconnectedError": "connection_error",
        "DatabaseError": "database_error",
        "LockError": "lock_error",
        "DirectoryNotFoundError": "directory_error",
        "PluginError": "plugin_error",
    }
    return mapping.get(name, "dbwarden_error")


if __name__ == "__main__":
    main()
