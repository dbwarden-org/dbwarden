import typer

from dbwarden.cli.app import app
from dbwarden.cli.validators import validate_directory
from dbwarden.commands import (
    handle_downgrade,
    handle_history,
    handle_lock_status,
    handle_make_migrations,
    handle_make_rollback,
    handle_migrate,
    handle_new,
    handle_rollback,
    handle_status,
    handle_unlock,
)


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
):
    """Rollback the last applied migration."""
    validate_directory()
    handle_rollback(
        count=count, to_version=to_version, verbose=verbose, database=database
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
):
    """Downgrade to a specific migration version by reverting applied migrations."""
    validate_directory()
    handle_downgrade(to_version=to_version, verbose=verbose, database=database)


@app.command()
def make_rollback(
    migration_file: str = typer.Argument(
        ..., help="Path to migration SQL file"
    ),
):
    """Generate a rollback SQL file for a given migration file."""
    handle_make_rollback(migration_file=migration_file)


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
    force: bool = typer.Option(
        False, "--force", "-f", help="Skip confirmation and force-release the lock"
    ),
):
    """Release the migration lock."""
    validate_directory()
    handle_unlock(database=database, force=force)
