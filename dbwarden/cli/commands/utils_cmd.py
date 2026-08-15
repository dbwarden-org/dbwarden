import typer

from dbwarden.cli.app import app, database_app, settings_app
from dbwarden.commands import (
    handle_config,
    handle_database_list,
    handle_settings_show_command,
    handle_version,
)


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
def config():
    """Display current Python configuration."""
    handle_config()


@app.command()
def version():
    """Display dbwarden version and compatibility information."""
    handle_version()
