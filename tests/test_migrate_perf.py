import os
import re
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from dbwarden.config import set_dev_mode


def _strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)


def _write_migration(directory: str, name: str, content: str) -> None:
    with open(os.path.join(directory, name), "w", encoding="utf-8") as f:
        f.write(content)


@pytest.fixture
def sqlite_project(tmp_path, monkeypatch):
    """Chdir into a temp project with a real SQLite database and one migration."""
    set_dev_mode(False)
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "app.db"
    Path("dbwarden.py").write_text(
        "from dbwarden import database_config\n\n"
        "database_config(database_name='primary', default=True, "
        f"database_type='sqlite', database_url_sync='sqlite:///{db_path}')\n",
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
    return db_path


def _run_migrate(db_name="primary", **kwargs):
    from dbwarden.commands.migrate import migrate_single

    old_stdout = sys.stdout
    sys.stdout = StringIO()
    try:
        migrate_single(db_name=db_name, **kwargs)
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    return _strip_ansi(output)


class TestMigratePerf:
    def test_perf_flag_logs_phase_timings_and_statement_breakdown(self, sqlite_project, tmp_path):
        output = _run_migrate(db_name="primary", perf=True)

        assert "completed successfully" in output.lower()
        assert "Lock acquisition completed in" in output
        assert "Snapshot write completed in" in output
        assert "Model state write completed in" in output
        assert "SQL (" in output
        assert "CREATE TABLE test" in output
        assert tmp_path.joinpath("app.db").exists()

    def test_default_run_logs_phase_timings_without_statement_breakdown(
        self, sqlite_project
    ):
        output = _run_migrate(db_name="primary", perf=False)

        assert "completed successfully" in output.lower()
        assert "Lock acquisition completed in" in output
        assert "Snapshot write completed in" in output
        assert "Model state write completed in" in output
        assert "SQL (" not in output


class TestCliPerfFlag:
    def test_migrate_accepts_perf_flag(self):
        from dbwarden.cli.main import app

        runner = CliRunner()
        with patch("dbwarden.cli.main.handle_migrate") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(app, ["migrate", "--perf"])
            assert result.exit_code == 0
        _, kwargs = mock.call_args
        assert kwargs["perf"] is True

    def test_rollback_accepts_perf_flag(self):
        from dbwarden.cli.main import app

        runner = CliRunner()
        with patch("dbwarden.cli.main.handle_rollback") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(app, ["rollback", "--perf"])
            assert result.exit_code == 0
        _, kwargs = mock.call_args
        assert kwargs["perf"] is True

    def test_make_migrations_accepts_perf_flag(self):
        from dbwarden.cli.main import app

        runner = CliRunner()
        with patch("dbwarden.cli.main.handle_make_migrations") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(
                    app, ["make-migrations", "add column", "--perf"]
                )
            assert result.exit_code == 0
        _, kwargs = mock.call_args
        assert kwargs["perf"] is True
