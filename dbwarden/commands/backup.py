import os
import secrets
import sqlite3
import stat
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url


def create_backup(sqlalchemy_url: str, backup_dir: str) -> str:
    """
    Create a backup of the database.

    Args:
        sqlalchemy_url: Database connection URL.
        backup_dir: Directory to store backups.

    Returns:
        str: Path to the backup file.

    Raises:
        ValueError: If backup_dir is world-writable.
    """
    # Check backup_dir is not world-writable
    backup_dir_path = Path(backup_dir)
    try:
        mode = backup_dir_path.stat().st_mode
        if mode & stat.S_IWOTH:
            raise ValueError(
                f"Backup directory '{backup_dir}' is world-writable. "
                "Use a secure directory with restricted permissions."
            )
    except FileNotFoundError:
        # Directory doesn't exist yet - will be created with safe permissions
        pass

    os.makedirs(backup_dir, mode=0o700, exist_ok=True)

    if backup_dir_path.is_symlink():
        raise ValueError(f"Backup directory '{backup_dir}' must not be a symlink.")

    # Ensure directory has safe permissions after creation
    os.chmod(backup_dir, 0o700)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique = f"{timestamp}_{secrets.randbelow(1000):03d}"
    backup_path = os.path.join(backup_dir, f"backup_{unique}.db")

    # Handle collision with incrementing suffix
    base = backup_path
    counter = 1
    while os.path.exists(backup_path):
        backup_path = base.replace(".db", f"_{counter}.db")
        counter += 1
        if counter > 100:
            raise RuntimeError("Too many backup collisions")

    parsed = make_url(sqlalchemy_url)
    if parsed.get_backend_name() != "sqlite":
        raise ValueError(
            f"Backups are currently supported only for SQLite, got {parsed.get_backend_name()!r}."
        )
    if not parsed.database or parsed.database == ":memory:":
        raise ValueError("Cannot create a file backup for an in-memory SQLite database.")

    source_path = Path(parsed.database).expanduser()
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")

    temp_path: str | None = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix=".backup-", suffix=".db", dir=backup_dir)
        os.close(fd)
        os.chmod(temp_path, 0o600)
        with sqlite3.connect(source_path) as source, sqlite3.connect(temp_path) as target:
            source.backup(target)
        with open(temp_path, "rb") as backup_file:
            os.fsync(backup_file.fileno())
        os.replace(temp_path, backup_path)
        temp_path = None
        os.chmod(backup_path, 0o600)
        directory_fd = os.open(backup_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return backup_path
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
