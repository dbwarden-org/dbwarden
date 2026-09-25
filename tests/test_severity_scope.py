import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import typer

from dbwarden.commands.migrate import migrate_cmd
from dbwarden.engine.safety.plans import bind_plan
from dbwarden.engine.safety.scope import (
    DeferredStop,
    filter_repeatables,
    report_stop,
    severity_prefix,
)


def migration(tmp_path, version, kind):
    path = tmp_path / f"primary__{version}_test.sql"
    content = "-- upgrade\nSELECT 1;\n-- rollback\nSELECT 1;\n"
    path.write_text(content, encoding="utf-8")
    path.with_suffix(".plan.json").write_text(json.dumps(bind_plan({}, content, [{"type": kind}], "sqlite")), encoding="utf-8")
    return str(path)


def test_strict_prefix_does_not_examine_later_files(tmp_path):
    files = {"0001": migration(tmp_path, "0001", "create_table"), "0002": migration(tmp_path, "0002", "set_not_null"), "0003": "missing.sql"}
    allowed, stop = severity_prefix(files, "INFO")
    assert list(allowed) == ["0001"]
    assert (stop.version, stop.remaining, stop.severity) == ("0002", 2, "WARN")
    assert len(severity_prefix(files, "CRITICAL")[0]) == 3


def test_repeatable_skip_is_per_run(tmp_path):
    path = migration(tmp_path, "RA", "set_not_null")
    assert filter_repeatables([path], "INFO", "primary") == []
    assert filter_repeatables([path], "WARN", "primary") == [path]


def test_report_stop_tolerates_deleted_deferred_file(tmp_path, capsys):
    """A deferred file deleted between the severity scan and the stop report
    (concurrent edit) must not crash the report."""
    path = migration(tmp_path, "0001", "set_not_null")
    _allowed, stop = severity_prefix({"0001": path}, "INFO")
    assert stop is not None
    from pathlib import Path

    Path(path).unlink()
    report_stop(stop, "primary")  # completes without raising
    assert "Stopped" in capsys.readouterr().out


def test_all_continues_after_deferred_stop_and_errors_win():
    calls = []
    def run(**kwargs):
        calls.append(kwargs["db_name"])
        if kwargs["db_name"] == "a":
            return DeferredStop("0002", "x", "WARN", "INFO", 1)
    with patch("dbwarden.commands.migrate.migrate_single", side_effect=run), patch("dbwarden.config.get_multi_db_config", return_value=SimpleNamespace(databases={"a": object(), "b": object()})):
        with pytest.raises(typer.Exit) as exc:
            migrate_cmd(all_databases=True, max_severity="INFO")
        assert exc.value.exit_code == 3
    assert calls == ["a", "b"]
    with patch("dbwarden.commands.migrate.migrate_single", side_effect=[DeferredStop("0002", "x", "WARN", "INFO", 1), RuntimeError("database failed")]), patch("dbwarden.config.get_multi_db_config", return_value=SimpleNamespace(databases={"a": object(), "b": object()})):
        with pytest.raises(RuntimeError, match="database failed"):
            migrate_cmd(all_databases=True, max_severity="INFO")
