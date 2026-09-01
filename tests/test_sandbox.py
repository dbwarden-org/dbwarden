import os
import re
import sys
import tempfile
from io import StringIO
from pathlib import Path

import pytest

from dbwarden.config import set_dev_mode
from dbwarden.engine.sandbox import SQLiteSandboxProvider


def _strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)


class TestSQLiteSandboxProvider:
    def test_start_returns_in_memory_url(self):
        provider = SQLiteSandboxProvider()
        url = provider.start()
        assert url == "sqlite:///:memory:"

    def test_stop_does_not_raise(self):
        provider = SQLiteSandboxProvider()
        provider.start()
        provider.stop()

    def test_get_database_type(self):
        provider = SQLiteSandboxProvider()
        assert provider.get_database_type() == "sqlite"

    def test_start_and_stop_can_be_called_multiple_times(self):
        provider = SQLiteSandboxProvider()
        url1 = provider.start()
        provider.stop()
        url2 = provider.start()
        provider.stop()
        assert url1 == url2 == "sqlite:///:memory:"


def _write_migration(directory: str, name: str, content: str) -> None:
    with open(os.path.join(directory, name), "w", encoding="utf-8") as f:
        f.write(content)


class TestDryRun:
    def test_dry_run_prints_pending_and_skips_execution(self):
        set_dev_mode(False)
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                db_path = os.path.join(tmpdir, "app.db")
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n\n"
                    "database_config(database_name='primary', default=True, "
                    "database_type='sqlite', database_url_sync='sqlite:///" + db_path + "')\n",
                    encoding="utf-8",
                )
                migrations_dir = Path("migrations/primary")
                migrations_dir.mkdir(parents=True)
                _write_migration(
                    str(migrations_dir),
                    "primary__0001_create_test.sql",
                    "-- upgrade\n\n"
                    "CREATE TABLE test (id INTEGER PRIMARY KEY)\n"
                    "INSERT INTO test VALUES (1)\n\n"
                    "-- rollback\n\n"
                    "DROP TABLE test\n",
                )

                from dbwarden.commands.migrate import migrate_single

                old_stdout = sys.stdout
                sys.stdout = StringIO()
                try:
                    migrate_single(db_name="primary", dry_run=True)
                    output = sys.stdout.getvalue()
                finally:
                    sys.stdout = old_stdout

                assert "DRY RUN" in output
                assert "0001" in output
                assert "CREATE TABLE test" in _strip_ansi(output)

                assert not os.path.exists(db_path)
            finally:
                os.chdir(old_cwd)

    def test_dry_run_no_pending_migrations(self):
        set_dev_mode(False)
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                db_path = os.path.join(tmpdir, "app.db")
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n\n"
                    "database_config(database_name='primary', default=True, "
                    "database_type='sqlite', database_url_sync='sqlite:///" + db_path + "')\n",
                    encoding="utf-8",
                )
                migrations_dir = Path("migrations/primary")
                migrations_dir.mkdir(parents=True)

                from dbwarden.commands.migrate import migrate_single

                old_stdout = sys.stdout
                sys.stdout = StringIO()
                try:
                    migrate_single(db_name="primary", dry_run=True)
                    output = sys.stdout.getvalue()
                finally:
                    sys.stdout = old_stdout

                assert "up to date" in output.lower()
            finally:
                os.chdir(old_cwd)


class TestSandbox:
    def test_sandbox_applies_migrations_and_cleans_up(self):
        set_dev_mode(False)
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                db_path = os.path.join(tmpdir, "app.db")
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n\n"
                    "database_config(database_name='primary', default=True, "
                    "database_type='sqlite', database_url_sync='sqlite:///" + db_path + "')\n",
                    encoding="utf-8",
                )
                migrations_dir = Path("migrations/primary")
                migrations_dir.mkdir(parents=True)
                _write_migration(
                    str(migrations_dir),
                    "primary__0001_create_test.sql",
                    "-- upgrade\n\n"
                    "CREATE TABLE test (id INTEGER PRIMARY KEY)\n\n"
                    "-- rollback\n\n"
                    "DROP TABLE test\n",
                )

                from dbwarden.commands.migrate import migrate_single

                old_stdout = sys.stdout
                sys.stdout = StringIO()
                try:
                    migrate_single(db_name="primary", sandbox=True)
                    output = sys.stdout.getvalue()
                finally:
                    sys.stdout = old_stdout

                assert "Sandbox started" in output
                assert "Sandbox stopped" in output
                assert "completed successfully" in output.lower()

                assert not os.path.exists(db_path)
            finally:
                os.chdir(old_cwd)

    def test_sandbox_reports_no_pending(self):
        set_dev_mode(False)
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                db_path = os.path.join(tmpdir, "app.db")
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n\n"
                    "database_config(database_name='primary', default=True, "
                    "database_type='sqlite', database_url_sync='sqlite:///" + db_path + "')\n",
                    encoding="utf-8",
                )
                migrations_dir = Path("migrations/primary")
                migrations_dir.mkdir(parents=True)

                from dbwarden.commands.migrate import migrate_single

                old_stdout = sys.stdout
                sys.stdout = StringIO()
                try:
                    migrate_single(db_name="primary", sandbox=True)
                    output = sys.stdout.getvalue()
                finally:
                    sys.stdout = old_stdout

                assert "Sandbox started" in output
                assert "Sandbox stopped" in output
                assert "up to date" in output.lower()
            finally:
                os.chdir(old_cwd)

    def test_sandbox_isolates_from_real_db(self):
        set_dev_mode(False)
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                db_path = os.path.join(tmpdir, "app.db")
                Path("dbwarden.py").write_text(
                    "from dbwarden import database_config\n\n"
                    "database_config(database_name='primary', default=True, "
                    "database_type='sqlite', database_url_sync='sqlite:///" + db_path + "')\n",
                    encoding="utf-8",
                )
                migrations_dir = Path("migrations/primary")
                migrations_dir.mkdir(parents=True)
                _write_migration(
                    str(migrations_dir),
                    "primary__0001_create_test.sql",
                    "-- upgrade\n\n"
                    "CREATE TABLE test (id INTEGER PRIMARY KEY)\n\n"
                    "-- rollback\n\n"
                    "DROP TABLE test\n",
                )

                from dbwarden.commands.migrate import migrate_single
                from dbwarden.connection.connection import _engine_cache

                migrate_single(db_name="primary", sandbox=True)

                assert not os.path.exists(db_path)

                from sqlalchemy import create_engine, text

                real_engine = create_engine(f"sqlite:///{db_path}")
                from sqlalchemy import inspect as sa_inspect

                inspector = sa_inspect(real_engine)
                tables = inspector.get_table_names()
                assert "test" not in tables
                real_engine.dispose()
            finally:
                os.chdir(old_cwd)
