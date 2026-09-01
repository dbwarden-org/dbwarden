import os
import tempfile
from pathlib import Path

from dbwarden.commands.downgrade import downgrade_cmd
from dbwarden.engine.version import get_migration_filepaths_by_version, get_migrations_directory


def _setup_migration_env(tmpdir: str) -> str:
    db_path = f"sqlite:///./{Path(tmpdir).name}.db"
    Path(tmpdir, "dbwarden.py").write_text(
        "from dbwarden import database_config\n\n"
        f"database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='{db_path}')\n",
        encoding="utf-8",
    )
    migrations_dir = Path(tmpdir, "migrations", "primary")
    migrations_dir.mkdir(parents=True, exist_ok=True)
    return db_path


def _create_migration(migrations_dir: str, version: str, desc: str, upgrade: str, rollback: str) -> str:
    filename = f"primary__{version}_{desc}.sql"
    content = f"-- upgrade\n\n{upgrade}\n\n-- rollback\n\n{rollback}\n"
    filepath = os.path.join(migrations_dir, filename)
    Path(filepath).write_text(content, encoding="utf-8")
    return filepath


def test_downgrade_noop_when_already_at_target():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            from dbwarden.config import set_dev_mode
            set_dev_mode(False)
            _setup_migration_env(tmpdir)
            downgrade_cmd(to_version="0001", database=None)
        finally:
            os.chdir(old_cwd)


def test_downgrade_parses_rollback_statements():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            from dbwarden.config import set_dev_mode
            from dbwarden.repositories import run_migration

            set_dev_mode(False)
            _setup_migration_env(tmpdir)

            migrations_dir = str(Path(tmpdir, "migrations", "primary"))

            filepath = _create_migration(
                migrations_dir, "0001", "create_users",
                "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);",
                "DROP TABLE IF EXISTS users;",
            )

            from dbwarden.engine.version import get_migrations_directory
            from dbwarden.repositories import create_migrations_table_if_not_exists

            create_migrations_table_if_not_exists(db_name=None)

            run_migration(
                sql_statements=["CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT);"],
                version="0001",
                migration_operation="upgrade",
                filename="primary__0001_create_users.sql",
                db_name=None,
            )

            filepaths = get_migration_filepaths_by_version(migrations_dir)
            assert "0001" in filepaths
        finally:
            os.chdir(old_cwd)


def test_make_rollback_reverses_create_table():
    from dbwarden.commands.make_rollback import _reverse_sql

    result = _reverse_sql("CREATE TABLE users (id INTEGER PRIMARY KEY);")
    assert "DROP TABLE IF EXISTS users" in result


def test_make_rollback_reverses_create_view():
    from dbwarden.commands.make_rollback import _reverse_sql

    result = _reverse_sql("CREATE MATERIALIZED VIEW my_view AS SELECT * FROM users;")
    assert "DROP VIEW IF EXISTS my_view" in result


def test_make_rollback_reverses_add_column():
    from dbwarden.commands.make_rollback import _reverse_sql

    result = _reverse_sql("ALTER TABLE users ADD COLUMN email TEXT;")
    assert "ALTER TABLE users DROP COLUMN email" in result


def test_make_rollback_reverses_create_index():
    from dbwarden.commands.make_rollback import _reverse_sql

    result = _reverse_sql("CREATE INDEX idx_name ON users (name);")
    assert "DROP INDEX IF EXISTS idx_name" in result


def test_make_rollback_generates_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            from dbwarden.commands.make_rollback import make_rollback_cmd

            migration_file = Path(tmpdir, "V0001__test.sql")
            migration_file.write_text(
                "-- upgrade\n\nCREATE TABLE users (id INTEGER PRIMARY KEY);\n\n-- rollback\n\nDROP TABLE users;\n",
                encoding="utf-8",
            )

            make_rollback_cmd(str(migration_file))

            rollback_file = Path(tmpdir, "V0001__test.rollback.sql")
            assert rollback_file.exists()
            content = rollback_file.read_text()
            assert "DROP TABLE IF EXISTS users" in content
        finally:
            os.chdir(old_cwd)


def test_make_rollback_refuses_placeholder_without_irreversible_annotation(tmp_path):
    from dbwarden.commands.make_rollback import make_rollback_cmd

    migration_file = tmp_path / "V0001__unknown.sql"
    migration_file.write_text("-- upgrade\n\nVACUUM;\n", encoding="utf-8")

    make_rollback_cmd(str(migration_file))

    assert not (tmp_path / "V0001__unknown.rollback.sql").exists()


def test_make_rollback_allows_placeholder_with_irreversible_annotation(tmp_path):
    from dbwarden.commands.make_rollback import make_rollback_cmd

    migration_file = tmp_path / "V0001__unknown.sql"
    migration_file.write_text(
        "-- dbwarden: irreversible\n-- upgrade\n\nVACUUM;\n",
        encoding="utf-8",
    )

    make_rollback_cmd(str(migration_file))

    rollback_file = tmp_path / "V0001__unknown.rollback.sql"
    assert rollback_file.exists()
    assert "No automatic rollback generated" in rollback_file.read_text(encoding="utf-8")


def test_make_rollback_no_upgrade_section():
    from dbwarden.commands.make_rollback import make_rollback_cmd

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False) as f:
        f.write("-- no sections here\n")
        f.flush()
        make_rollback_cmd(f.name)
        os.unlink(f.name)


def test_snapshot_generic_table():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            from dbwarden.config import set_dev_mode
            from dbwarden.connection.connection import get_db_connection
            from dbwarden.commands.snapshot import _snapshot_generic
            from sqlalchemy import text

            set_dev_mode(False)
            db_path = f"sqlite:///./{Path(tmpdir).name}.db"
            Path(tmpdir, "dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                f"database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='{db_path}')\n",
                encoding="utf-8",
            )

            with get_db_connection(None) as conn:
                conn.execute(text("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"))
                conn.execute(text("CREATE INDEX idx_name ON test_table (name)"))

            with get_db_connection(None) as conn:
                _snapshot_generic(conn, "test_table")
        finally:
            os.chdir(old_cwd)


def test_snapshot_table_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            from dbwarden.config import set_dev_mode
            from dbwarden.connection.connection import get_db_connection
            from dbwarden.commands.snapshot import _snapshot_generic

            set_dev_mode(False)
            db_path = f"sqlite:///./{Path(tmpdir).name}.db"
            Path(tmpdir, "dbwarden.py").write_text(
                "from dbwarden import database_config\n\n"
                f"database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync='{db_path}')\n",
                encoding="utf-8",
            )

            with get_db_connection(None) as conn:
                _snapshot_generic(conn, "nonexistent_table")
        finally:
            os.chdir(old_cwd)
