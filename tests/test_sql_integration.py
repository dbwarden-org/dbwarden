import pytest
import tempfile
import os
import sqlite3
from pathlib import Path

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    ForeignKey,
    Table,
    MetaData,
    text,
)
from sqlalchemy.orm import declarative_base

from dbwarden.connection.connection import get_db_connection
from dbwarden.config import set_dev_mode
from dbwarden.repositories import (
    create_migrations_table_if_not_exists,
    migrations_table_exists,
    run_migration,
    get_migration_records,
    fetch_latest_versioned_migration,
)


Base = declarative_base()


@pytest.fixture(autouse=True)
def reset_dev_mode():
    set_dev_mode(False)
    yield
    set_dev_mode(False)


class TestDatabaseOperations:
    """Integration tests for database operations."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        yield db_path

        if os.path.exists(db_path):
            os.unlink(db_path)

    @pytest.fixture
    def setup_env(self, temp_db):
        """Set up environment for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)

            with open("dbwarden.py", "w", encoding="utf-8") as f:
                f.write("from dbwarden import database_config\n\n")
                f.write(
                    "database_config(\n"
                    "    database_name='primary',\n"
                    "    default=True,\n"
                    "    database_type='sqlite',\n"
                    f"    database_url_sync='sqlite:///{temp_db}',\n"
                    ")\n"
                )

            yield {"db_path": temp_db}

            os.chdir(old_cwd)

    def test_create_migrations_table(self, setup_env):
        """Test creating the migrations tracking table."""
        create_migrations_table_if_not_exists()

        assert migrations_table_exists() == True

    def test_run_migration(self, setup_env):
        """Test running a migration."""
        create_migrations_table_if_not_exists()

        sql_statements = [
            "CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY, name VARCHAR(100))"
        ]

        run_migration(
            sql_statements=sql_statements,
            version="1",
            migration_operation="upgrade",
            filename="V1__test.sql",
            migration_type="versioned",
        )

        records = get_migration_records()
        assert len(records) == 1
        assert records[0].version == "1"

    def test_get_migration_records(self, setup_env):
        """Test retrieving migration records."""
        create_migrations_table_if_not_exists()

        sql_statements = ["CREATE TABLE users (id INTEGER PRIMARY KEY)"]

        run_migration(
            sql_statements=sql_statements,
            version="1",
            migration_operation="upgrade",
            filename="V1__users.sql",
        )

        run_migration(
            sql_statements=["CREATE TABLE posts (id INTEGER PRIMARY KEY)"],
            version="2",
            migration_operation="upgrade",
            filename="V2__posts.sql",
        )

        records = get_migration_records()
        assert len(records) == 2

    def test_rollback_migration(self, setup_env):
        """Test rolling back a migration."""
        create_migrations_table_if_not_exists()

        sql_statements = ["CREATE TABLE test_table (id INTEGER PRIMARY KEY)"]

        run_migration(
            sql_statements=sql_statements,
            version="1",
            migration_operation="upgrade",
            filename="V1__test.sql",
        )

        records = get_migration_records()
        assert len(records) == 1

        run_migration(
            sql_statements=["DROP TABLE test_table"],
            version="1",
            migration_operation="rollback",
            filename="V1__test.sql",
        )

        records = get_migration_records()
        assert len(records) == 0

    def test_autocommit_failure_does_not_record_migration(self, setup_env, monkeypatch):
        from dbwarden.repositories import migrations_repo

        create_migrations_table_if_not_exists()

        def fail_autocommit(*_args, **_kwargs):
            raise RuntimeError("autocommit failed")

        monkeypatch.setattr(migrations_repo, "_execute_autocommit", fail_autocommit)
        with pytest.raises(RuntimeError, match="autocommit failed"):
            run_migration(
                sql_statements=[
                    "CREATE TABLE transactional_part (id INTEGER PRIMARY KEY)",
                    "-- @dbwarden:autocommit\nCREATE TABLE autocommit_part (id INTEGER PRIMARY KEY)",
                ],
                version="1",
                migration_operation="upgrade",
                filename="V1__autocommit.sql",
            )

        assert get_migration_records() == []


class TestMigrationExecution:
    """Tests for migration execution with SQL."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        yield db_path

        if os.path.exists(db_path):
            os.unlink(db_path)

    def test_create_table_with_columns(self, temp_db):
        """Test creating a table with multiple columns."""
        engine = create_engine(f"sqlite:///{temp_db}")

        with engine.connect() as conn:
            conn.execute(
                text("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    age INTEGER
                )
            """)
            )

        engine.dispose()

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(users)")
        columns = cursor.fetchall()
        conn.close()

        column_names = [col[1] for col in columns]
        assert "id" in column_names
        assert "name" in column_names
        assert "email" in column_names
        assert "age" in column_names

    def test_create_table_with_foreign_key(self, temp_db):
        """Test creating tables with foreign key relationship."""
        engine = create_engine(f"sqlite:///{temp_db}")

        with engine.connect() as conn:
            conn.execute(
                text("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(100)
                )
            """)
            )
            conn.execute(
                text("""
                CREATE TABLE posts (
                    id INTEGER PRIMARY KEY,
                    title VARCHAR(255),
                    user_id INTEGER,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
            )

        engine.dispose()

    def test_alter_table_add_column(self, temp_db):
        """Test adding a column to existing table."""
        engine = create_engine(f"sqlite:///{temp_db}")

        with engine.connect() as conn:
            conn.execute(
                text("CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(100))")
            )
            conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(255)"))

        engine.dispose()

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(users)")
        columns = cursor.fetchall()
        conn.close()

        column_names = [col[1] for col in columns]
        assert "email" in column_names

    def test_create_index(self, temp_db):
        """Test creating an index."""
        engine = create_engine(f"sqlite:///{temp_db}")

        with engine.connect() as conn:
            conn.execute(
                text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255))")
            )
            conn.execute(text("CREATE UNIQUE INDEX ix_users_email ON users(email)"))

        engine.dispose()

    def test_multiple_migrations(self, temp_db):
        """Test running multiple migrations in sequence."""
        engine = create_engine(f"sqlite:///{temp_db}")

        migrations = [
            ("1", "CREATE TABLE users (id INTEGER PRIMARY KEY)"),
            ("2", "CREATE TABLE posts (id INTEGER PRIMARY KEY, user_id INTEGER)"),
            ("3", "CREATE TABLE comments (id INTEGER PRIMARY KEY, post_id INTEGER)"),
        ]

        with engine.connect() as conn:
            for version, sql in migrations:
                conn.execute(text(sql))

        engine.dispose()

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()

        assert "users" in tables
        assert "posts" in tables
        assert "comments" in tables
