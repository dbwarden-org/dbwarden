import json
import sqlite3

import pytest
import typer

from dbwarden.commands.migrate import migrate_cmd
from dbwarden.config import set_dev_mode
from dbwarden.engine.safety.plans import bind_plan


@pytest.fixture
def project(tmp_path, monkeypatch):
    set_dev_mode(False)
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "app.db"
    (tmp_path / "dbwarden.py").write_text(
        "from dbwarden import database_config\n"
        f"database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync={('sqlite:///' + db.as_posix())!r})\n",
        encoding="utf-8",
    )
    directory = tmp_path / "migrations" / "primary"
    directory.mkdir(parents=True)
    for number, name, sql, kind in [
        (1, "base", "CREATE TABLE items (id INTEGER PRIMARY KEY);", "create_table"),
        (2, "deferred", "ALTER TABLE items ADD COLUMN score INTEGER;", "set_not_null"),
        (3, "blocked", "CREATE TABLE later (id INTEGER);", "create_table"),
    ]:
        path = directory / f"primary__{number:04d}_{name}.sql"
        content = f"-- upgrade\n{sql}\n-- rollback\nDROP TABLE items;\n"
        path.write_text(content, encoding="utf-8")
        plan = bind_plan({}, content, [{"type": kind}], "sqlite")
        path.with_suffix(".plan.json").write_text(json.dumps(plan), encoding="utf-8")
    yield db
    from dbwarden.connection.connection import dispose_engine
    dispose_engine("sqlite:///" + db.as_posix(), "sqlite")


def test_ceiling_applies_prefix_records_history_and_releases_lock(project):
    with pytest.raises(typer.Exit) as exc:
        migrate_cmd(database="primary", max_severity="INFO", force=True)
    assert exc.value.exit_code == 3
    with sqlite3.connect(project) as conn:
        assert [row[1] for row in conn.execute("PRAGMA table_info(items)")] == ["id"]
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='later'").fetchall()
    from dbwarden.repositories import get_migrated_versions
    assert get_migrated_versions("primary") == ["0001"]
    migrate_cmd(database="primary", max_severity="CRITICAL", force=True)
    assert get_migrated_versions("primary") == ["0001", "0002", "0003"]


def test_dry_run_no_schema_changes_and_zero_exit(project):
    migrate_cmd(database="primary", max_severity="INFO", dry_run=True)
    if project.exists():
        with sqlite3.connect(project) as conn:
            assert not conn.execute("SELECT name FROM sqlite_master WHERE name='items'").fetchall()


def test_baseline_records_metadata_without_running_sql(project):
    migrate_cmd(database="primary", baseline=True, to_version="0002", max_severity="SAFE")
    from dbwarden.repositories import get_migrated_versions
    assert get_migrated_versions("primary") == ["0001", "0002"]
    with sqlite3.connect(project) as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name='items'").fetchall()


def test_force_cannot_bypass_ceiling_or_missing_acknowledgement(project):
    with pytest.raises(RuntimeError, match="preflight"):
        migrate_cmd(database="primary", max_severity="WARN")
    from dbwarden.repositories import get_migrated_versions
    assert get_migrated_versions("primary") == []
    with pytest.raises(typer.Exit) as exc:
        migrate_cmd(database="primary", max_severity="INFO", force=True)
    assert exc.value.exit_code == 3


def test_repeatable_retried_after_ceiling_rises(project, capsys):
    migrate_cmd(database="primary", force=True)
    path = project.parent / "migrations/primary/primary__RA__touch.sql"
    content = "-- upgrade\nINSERT INTO items (id) VALUES (100);\n-- rollback\nDELETE FROM items WHERE id=100;\n"
    path.write_text(content, encoding="utf-8")
    path.with_suffix(".plan.json").write_text(json.dumps(bind_plan({}, content, [{"type": "update"}], "sqlite")), encoding="utf-8")
    migrate_cmd(database="primary", max_severity="INFO")
    with sqlite3.connect(project) as connection:
        assert connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    migrate_cmd(database="primary", max_severity="WARN", force=True)
    with sqlite3.connect(project) as connection:
        assert connection.execute("SELECT id FROM items").fetchall() == [(100,)]
    migrate_cmd(database="primary", max_severity="INFO")
    assert capsys.readouterr().out.count("Skipping repeatable") == 2


def test_failure_keeps_prefix_and_rollback_removes_only_deferred(project):
    from dbwarden.exceptions import LockError
    from dbwarden.commands.rollback import rollback_cmd
    from dbwarden.repositories import get_migrated_versions

    path = project.parent / "migrations/primary/primary__0002_deferred.sql"
    failed = "-- upgrade\nINSERT INTO items (id) VALUES (2);\nSELECT * FROM missing_table;\n-- rollback\nDELETE FROM items WHERE id=2;\n"
    path.write_text(failed, encoding="utf-8")
    path.with_suffix(".plan.json").write_text(json.dumps(bind_plan({}, failed, [{"type": "update"}], "sqlite")), encoding="utf-8")
    with pytest.raises(LockError, match="missing_table"):
        migrate_cmd(database="primary", max_severity="WARN", force=True)
    assert get_migrated_versions("primary") == ["0001"]
    with sqlite3.connect(project) as connection:
        assert connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    repaired = failed.replace("SELECT * FROM missing_table;", "")
    path.write_text(repaired, encoding="utf-8")
    path.with_suffix(".plan.json").write_text(json.dumps(bind_plan({}, repaired, [{"type": "update"}], "sqlite")), encoding="utf-8")
    migrate_cmd(database="primary", max_severity="WARN", force=True, to_version="0002")
    rollback_cmd(database="primary", count=1)
    assert get_migrated_versions("primary") == ["0001"]
    with sqlite3.connect(project) as connection:
        assert connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_unknown_file_stops_at_default_critical_ceiling(project):
    """A file whose severity resolves to explicit UNKNOWN (tamper-evidenced
    here) stops under the default CRITICAL ceiling with exit 3; it must not
    run. Plan-less files are the exception (missing_plan policy)."""
    from dbwarden.repositories import get_migrated_versions

    path = project.parent / "migrations/primary/primary__0004_tampered.sql"
    content = "-- upgrade\nCREATE TABLE tampered (id INTEGER);\n-- rollback\nSELECT 1;\n"
    path.write_text(content, encoding="utf-8")
    plan = bind_plan({}, content, [{"type": "create_table"}], "sqlite")
    path.with_suffix(".plan.json").write_text(json.dumps(plan), encoding="utf-8")
    # Edit after planning: plan no longer matches -> explicit UNKNOWN.
    path.write_text(content + "-- edited after planning\n", encoding="utf-8")

    with pytest.raises(typer.Exit) as exc:
        migrate_cmd(database="primary", force=True)  # default ceiling: CRITICAL
    assert exc.value.exit_code == 3
    assert get_migrated_versions("primary") == ["0001", "0002", "0003"]
    with sqlite3.connect(project) as conn:
        assert not conn.execute(
            "SELECT name FROM sqlite_master WHERE name='tampered'"
        ).fetchall()


def test_unknown_header_cannot_authorize_and_count_bounds_prefix(project):
    from dbwarden.repositories import get_migrated_versions

    path = project.parent / "migrations/primary/primary__0002_deferred.sql"
    path.with_suffix(".plan.json").unlink()
    path.write_text("-- dbwarden: file-severity SAFE\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    migrate_cmd(database="primary", max_severity="INFO", count=1)
    assert get_migrated_versions("primary") == ["0001"]
    with pytest.raises(typer.Exit) as exc:
        migrate_cmd(database="primary", max_severity="INFO", to_version="0003", force=True)
    assert exc.value.exit_code == 3
    assert get_migrated_versions("primary") == ["0001"]
    migrate_cmd(database="primary", max_severity="CRITICAL", force=True)
    assert get_migrated_versions("primary") == ["0001", "0002", "0003"]


@pytest.mark.parametrize("arguments", [
    ["migrate", "--max-severity", "UNKNOWN"],
    ["make-migrations", "--split-at-severity", "WRONG"],
    ["make-migrations", "--split-at-severity", "WARN", "--type", "ra"],
])
def test_invalid_severity_cli_usage(project, arguments):
    from typer.testing import CliRunner
    from dbwarden.cli.main import app
    assert CliRunner().invoke(app, arguments).exit_code == 2


def test_static_bulk_cli_reports_unknown_without_database(project):
    from typer.testing import CliRunner
    from dbwarden.cli.main import app
    path = project.parent / "migrations/primary/primary__0004_manual.sql"
    path.write_text("-- upgrade\nSELECT mystery_function();\n-- rollback\nSELECT 1;\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["check", "--write-plan", "--all", "--database", "primary", "--out", "json"])
    assert result.exit_code == 4, result.output
    assert "unresolved" in result.output and path.name in result.output
    assert not project.exists()
    assert not path.with_suffix(".plan.json").exists()
