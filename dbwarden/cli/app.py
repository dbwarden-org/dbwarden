import typer

from dbwarden.config import set_dev_mode, set_strict_translation

app = typer.Typer(
    help="""dbwarden - Professional database migration system for SQLAlchemy models

All commands support the --verbose / -v flag for detailed output.""",
    add_completion=False,
)

database_app = typer.Typer(help="List configured databases")
app.add_typer(database_app, name="database")
settings_app = typer.Typer(help="View dbwarden settings")
app.add_typer(settings_app, name="settings")
seed_app = typer.Typer(help="Manage seed data")
app.add_typer(seed_app, name="seed")


@app.callback()
def app_callback(
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
):
    """Global CLI options."""
    set_dev_mode(dev)
    set_strict_translation(strict_translation)
