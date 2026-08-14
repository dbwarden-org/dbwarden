from pathlib import Path
import shutil

from dbwarden.constants import MIGRATIONS_DIR
from dbwarden.logging import get_logger
from dbwarden.output import success, success_panel
from dbwarden.files import atomic_write_text


def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically while retaining a backup of the old file."""
    path = path.resolve()
    if path.exists():
        backup_path = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup_path)
    atomic_write_text(path, content)


def _ensure_settings_file(settings_path: Path, db_name: str) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    content = ""
    if settings_path.exists():
        content = settings_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    import_line = "from dbwarden import DbwardenDatabase"
    has_declarative_import = any(line.strip() == import_line for line in lines)
    has_function_config = "database_config(" in content
    has_declarative_config = "DbwardenDatabase" in content and "class " in content
    has_scaffold = has_function_config or has_declarative_config

    updated = content
    if not has_scaffold and not has_declarative_import:
        updated = (
            f"{import_line}\n\n{updated}" if updated.strip() else f"{import_line}\n"
        )

    if not has_scaffold:
        scaffold = (
            "\n\nclass Primary(DbwardenDatabase):\n"
            "    \"\"\"Default database configuration.\"\"\"\n"
            f'    database_name = "{db_name}"\n'
            "    default = True\n"
            '    database_type = "sqlite"\n'
            '    database_url_sync = "sqlite:///./app.db"\n'
            f'    migrations_dir = "migrations/{db_name}"\n'
        )
        updated = f"{updated.rstrip()}{scaffold}"

    if updated != content:
        _atomic_write(settings_path, updated)


def init_cmd(database: str | None = None) -> None:
    """
    Initialize DBWarden in current directory.

    Creates the migrations directory and a Python settings config scaffold.

    Args:
        database: Optional database name for scaffold defaults.
    """
    logger = get_logger()
    current_dir = Path.cwd()

    migrations_dir = current_dir / MIGRATIONS_DIR
    migrations_dir.mkdir(parents=True, exist_ok=True)

    db_name = database or "primary"
    db_migrations_dir = migrations_dir / db_name
    db_migrations_dir.mkdir(parents=True, exist_ok=True)

    settings_path = current_dir / "dbwarden.py"
    _ensure_settings_file(settings_path, db_name)

    logger.info(f"Created/updated configuration file: {settings_path}")
    success(f"Created/updated configuration file: {settings_path}")
    logger.info(
        f"Initialized DBWarden migrations directory: {db_migrations_dir.absolute()}"
    )
    success(f"DBWarden migrations directory created: {db_migrations_dir.absolute()}")

    success_panel(
        "Next steps",
        "1. Edit dbwarden.py with your database configuration\n"
        "2. Run 'dbwarden make-migrations -d <name>' to generate migrations",
    )
