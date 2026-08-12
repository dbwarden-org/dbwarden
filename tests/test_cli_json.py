import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from dbwarden.cli.main import app
from dbwarden.output import set_output_mode


@pytest.fixture(autouse=True)
def reset_output_mode():
    set_output_mode("text")
    yield
    set_output_mode("text")


@pytest.fixture(autouse=True)
def restore_logging_state():
    from dbwarden.logging import reset_logger

    original = os.environ.get("DBWARDEN_LOG_JSON")
    yield
    if original is None:
        os.environ.pop("DBWARDEN_LOG_JSON", None)
    else:
        os.environ["DBWARDEN_LOG_JSON"] = original
    reset_logger()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A minimal project config with two databases."""
    set_output_mode("text")
    monkeypatch.chdir(tmp_path)
    Path("dbwarden.py").write_text(
        "from dbwarden import database_config\n\n"
        "database_config(database_name='primary', default=True, "
        "database_type='sqlite', database_url_sync='sqlite:///app.db', "
        "model_paths=['models'], overlap_models=True)\n"
        "database_config(database_name='archive', default=False, "
        "database_type='sqlite', database_url_sync='sqlite:///archive.db', "
        "model_paths=['models'], overlap_models=True)\n",
        encoding="utf-8",
    )
    return tmp_path


def _parse(output: str):
    """Return the last complete JSON document in stdout (logs are also JSON)."""
    decoder = json.JSONDecoder()
    idx = 0
    doc = None
    while idx < len(output):
        while idx < len(output) and output[idx].isspace():
            idx += 1
        if idx >= len(output):
            break
        try:
            doc, idx = decoder.raw_decode(output, idx)
        except json.JSONDecodeError:
            break
    return doc


def _all_docs(output: str):
    """Return every complete JSON document in stdout."""
    decoder = json.JSONDecoder()
    idx = 0
    docs = []
    while idx < len(output):
        while idx < len(output) and output[idx].isspace():
            idx += 1
        if idx >= len(output):
            break
        try:
            doc, idx = decoder.raw_decode(output, idx)
            docs.append(doc)
        except json.JSONDecodeError:
            break
    return docs


class TestJsonFlag:
    def test_json_version(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--json", "version"])
        assert result.exit_code == 0
        payload = _parse(result.output)
        assert "version" in payload

    def test_json_config(self, project):
        runner = CliRunner()
        result = runner.invoke(app, ["--json", "config"])
        assert result.exit_code == 0
        payload = _parse(result.output)
        assert payload["default"] == "primary"
        assert "primary" in payload["databases"]
        assert "archive" in payload["databases"]

    def test_json_database_list(self, project):
        runner = CliRunner()
        result = runner.invoke(app, ["--json", "database", "list"])
        assert result.exit_code == 0
        payload = _parse(result.output)
        assert "primary" in payload
        assert "archive" in payload

    def test_json_settings_show(self, project):
        runner = CliRunner()
        result = runner.invoke(app, ["--json", "settings", "show"])
        assert result.exit_code == 0
        payload = _parse(result.output)
        assert "primary" in payload
        assert payload["primary"]["default"] is True

    def test_json_lock_status(self, project):
        (Path("migrations") / "primary").mkdir(parents=True)
        from dbwarden import repositories

        with patch.object(repositories, "check_lock", return_value=True):
            runner = CliRunner()
            result = runner.invoke(app, ["--json", "lock-status"])
        assert result.exit_code == 0
        payload = _parse(result.output)
        assert payload["locked"] is True


class TestJsonStatus:
    def test_json_status_with_migrations_dir(self, project):
        migrations_dir = Path("migrations/primary")
        migrations_dir.mkdir(parents=True)
        Path("migrations/primary/primary__0001_a.sql").write_text(
            "-- upgrade\n\nCREATE TABLE t (id int)\n\n-- rollback\n\nDROP TABLE t\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(app, ["--json", "status"])
        assert result.exit_code == 0
        payload = _parse(result.output)
        assert payload["database"] == "default"
        assert payload["summary"]["total"] == 1
        assert payload["migrations"][0]["version"] == "0001"
        assert payload["migrations"][0]["status"] == "pending"

    def test_json_status_all_databases(self, project):
        for name in ("primary", "archive"):
            (Path("migrations") / name).mkdir(parents=True)

        runner = CliRunner()
        result = runner.invoke(app, ["--json", "status", "--all"])
        assert result.exit_code == 0
        payloads = _all_docs(result.output)
        assert any(p["database"] == "primary" for p in payloads)
        assert any(p["database"] == "archive" for p in payloads)


class TestJsonHonorsExistingFormatCommands:
    def test_json_check_forces_json_output(self, project):
        migrations_dir = Path("migrations/primary")
        migrations_dir.mkdir(parents=True)

        with patch(
            "dbwarden.commands.check.load_issues", return_value=[]
        ):
            runner = CliRunner()
            result = runner.invoke(app, ["--json", "check"])
        assert result.exit_code == 0
        payload = _parse(result.output)
        assert isinstance(payload, list)
