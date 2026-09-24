import os
import sqlite3
import stat
from contextlib import closing

import pytest

from dbwarden.commands.backup import create_backup


def test_sqlite_backup_is_consistent_and_private(tmp_path):
    database_path = tmp_path / "source.db"
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO users (name) VALUES ('Ada')")

    backup_path = create_backup(f"sqlite:///{database_path}", str(tmp_path / "backups"))
    mode = stat.S_IMODE(os.stat(backup_path).st_mode)
    if os.name != "nt":
        assert mode == 0o600
    else:
        assert mode & stat.S_IWRITE

    with closing(sqlite3.connect(backup_path)) as connection:
        assert connection.execute("SELECT name FROM users").fetchone()[0] == "Ada"
    os.unlink(backup_path)
    os.unlink(database_path)


def test_backup_rejects_unsupported_backend(tmp_path):
    with pytest.raises(ValueError, match="supported only for SQLite"):
        create_backup("postgresql://user:pass@localhost/db", str(tmp_path / "backups"))


def test_backup_rejects_symlink_directory(tmp_path, symlink_factory):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    symlink_factory(link_dir, real_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink"):
        create_backup("sqlite:///:memory:", str(link_dir))
