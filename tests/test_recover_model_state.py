from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import tempfile


def _mock_config():
    mock = MagicMock()
    mock.database_type = "sqlite"
    mock.sqlalchemy_url = "sqlite:///:memory:"
    mock.model_paths = ["test.models"]
    mock.model_tables = None
    mock.migration_table = "_dbwarden_migrations"
    mock.seed_table = "_dbwarden_seeds"
    return mock


class TestRecoverModelState:
    @patch("dbwarden.commands.recover_model_state.get_migration_records")
    @patch("dbwarden.commands.recover_model_state.get_database")
    @patch("dbwarden.commands.recover_model_state.get_multi_db_config")
    def test_no_applied_migrations(
        self, mock_multi_db, mock_get_db, mock_get_migrations
    ):
        mock_get_migrations.return_value = []
        mock_get_db.return_value = _mock_config()
        mock_multi_db.return_value.default = "default"

        from dbwarden.commands.recover_model_state import recover_model_state_cmd

        from dbwarden.output import console
        from io import StringIO
        import sys

        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            recover_model_state_cmd(database="default")
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        assert "No applied migrations found" in output

    @patch("dbwarden.commands.recover_model_state.get_migration_records")
    @patch("dbwarden.commands.recover_model_state.get_migrations_directory")
    @patch("dbwarden.commands.recover_model_state.get_database")
    @patch("dbwarden.commands.recover_model_state.get_multi_db_config")
    def test_migration_file_not_found(
        self, mock_multi_db, mock_get_db, mock_get_dir, mock_get_migrations
    ):
        from dbwarden.models import MigrationRecord
        from datetime import datetime

        mock_get_migrations.return_value = [
            MigrationRecord(
                version="0001",
                description="test",
                filename="nonexistent.sql",
                migration_type="versioned",
                applied_at=datetime.utcnow(),
                checksum="abc",
                order_executed=1,
            ),
        ]
        mock_get_db.return_value = _mock_config()
        mock_multi_db.return_value.default = "default"
        mock_get_dir.return_value = "/tmp/migrations"

        from dbwarden.commands.recover_model_state import recover_model_state_cmd

        from dbwarden.output import console
        from io import StringIO
        import sys

        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            recover_model_state_cmd(database="default")
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        assert "No migration files found on disk" in output

    def test_sandbox_schema_to_model_state(self):
        """Test that _sandbox_schema_to_model_state works with a real SQLite DB."""
        import tempfile, os

        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        url = f"sqlite:///{db_path}"

        from sqlalchemy import create_engine, text
        engine = create_engine(url)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE)"))
            conn.execute(text("CREATE TABLE posts (id INTEGER PRIMARY KEY, title TEXT, user_id INTEGER REFERENCES users(id))"))
            conn.execute(text("CREATE INDEX idx_posts_user ON posts(user_id)"))
        engine.dispose()

        from dbwarden.commands.recover_model_state import (
            _build_model_table_from_inspector,
            model_state_to_dict,
        )
        from sqlalchemy import inspect as sa_inspect

        engine2 = create_engine(url)
        with engine2.connect() as conn:
            inspector = sa_inspect(conn)
            table_names = [t for t in inspector.get_table_names() if not t.startswith("_")]
            tables = [_build_model_table_from_inspector(inspector, t) for t in table_names]
            state = model_state_to_dict(tables, dbwarden_version="0.0.0")
        engine2.dispose()

        assert "tables" in state
        assert "users" in state["tables"]
        assert "posts" in state["tables"]

        users = state["tables"]["users"]
        assert "columns" in users
        assert "id" in users["columns"]
        assert users["columns"]["id"]["primary_key"] is True
        assert users["columns"]["name"]["nullable"] is False

        assert "dfg" not in state["tables"]

        os.unlink(db_path)

    def test_recover_model_state_integration(self):
        """Integration test: set up real SQLite DB, write migration SQL files,
        apply a migration to the real DB, then verify recover-model-state
        reconstructs the state correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = Path.cwd()
            import os
            os.chdir(tmpdir)
            try:
                db_path = Path(tmpdir) / "app.db"
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n\n"
                    "database_config(database_name='default', default=True, "
                    f"database_type='sqlite', database_url_sync={'sqlite:///' + str(db_path)!r})\n",
                    encoding="utf-8",
                )

                migrations_dir = Path(tmpdir) / "migrations" / "default"
                migrations_dir.mkdir(parents=True)
                migration_file = migrations_dir / "default__0001_create_users.sql"
                migration_file.write_text(
                    "-- upgrade\n\n"
                    "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)\n\n"
                    "-- rollback\n\n"
                    "DROP TABLE users\n",
                    encoding="utf-8",
                )

                from dbwarden.repositories import (
                    create_migrations_table_if_not_exists,
                    run_migration,
                )

                create_migrations_table_if_not_exists("default")

                run_migration(
                    sql_statements=["CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"],
                    version="0001",
                    migration_operation="upgrade",
                    filename="default__0001_create_users.sql",
                    db_name="default",
                )

                from dbwarden.commands.recover_model_state import recover_model_state_cmd
                from io import StringIO
                import sys

                model_state_path = Path(".dbwarden") / "model_state.default.json"
                assert not model_state_path.exists()

                old_stdout = sys.stdout
                sys.stdout = StringIO()
                try:
                    recover_model_state_cmd(database="default")
                    output = sys.stdout.getvalue()
                finally:
                    sys.stdout = old_stdout

                assert "Model state recovered" in output
                assert model_state_path.exists()

                import json
                state = json.loads(model_state_path.read_text())
                assert "tables" in state
                assert "users" in state["tables"]
                assert state["tables"]["users"]["columns"]["id"]["primary_key"] is True
            finally:
                from dbwarden.connection.connection import dispose_engine
                dispose_engine(f"sqlite:///{db_path}", "sqlite")
                os.chdir(str(old_cwd))
