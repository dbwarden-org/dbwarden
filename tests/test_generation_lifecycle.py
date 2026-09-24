import json
import sqlite3

import pytest
import typer

from dbwarden.commands.make_migrations import make_migrations_cmd
from dbwarden.commands.migrate import migrate_cmd
from dbwarden.config import set_dev_mode
from dbwarden.engine.core.model_state import model_state_to_dict
from dbwarden.engine.core.models import ModelColumn, ModelTable
from dbwarden.engine.safety.plans import read_trusted_plan


@pytest.mark.parametrize("state_name", ["model_state.primary.json", "model_state.json"])
def test_offline_state_write_failure_restores_project(
    tmp_path, monkeypatch, state_name
):
    from dbwarden import files
    from dbwarden.engine.generation_state import effective_state, schema_state

    set_dev_mode(False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dbwarden.py").write_text(
        "from dbwarden import database_config\n"
        "database_config(database_name='primary', default=True, database_type='sqlite', "
        "database_url_sync='sqlite:///unused.db', model_paths=['models.py'])\n",
        encoding="utf-8",
    )
    (tmp_path / "models.py").write_text("", encoding="utf-8")
    directory = tmp_path / "migrations/primary"
    directory.mkdir(parents=True)
    state_dir = tmp_path / ".dbwarden"
    state_dir.mkdir()
    baseline = model_state_to_dict([])
    for name in ("model_state.primary.json", "model_state.json"):
        (state_dir / name).write_bytes((json.dumps(baseline) + "\r\n").encode())
    tables = [
        ModelTable(
            "items", [ModelColumn("id", "INTEGER", False, True, False, None, None)]
        )
    ]
    monkeypatch.setattr(
        "dbwarden.commands.make_migrations.get_all_model_tables",
        lambda *a, **kw: tables,
    )
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    write = files._atomic_write_bytes
    failed = False

    def fail_once(path, content):
        nonlocal failed
        if not failed and path.name == state_name:
            failed = True
            raise OSError("injected state write failure")
        return write(path, content)

    with monkeypatch.context() as scoped:
        scoped.setattr(files, "_atomic_write_bytes", fail_once)
        with pytest.raises(OSError, match="injected state"):
            make_migrations_cmd(
                database="primary", offline=True, split_at_severity="WARN"
            )
    assert failed
    assert {
        p: p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    } == before
    make_migrations_cmd(database="primary", offline=True, split_at_severity="WARN")
    assert not (tmp_path / "unused.db").exists()
    recorded = json.loads((state_dir / state_name).read_text())
    paths = {
        p.name.split("__")[1].split("_")[0]: str(p) for p in directory.glob("*.sql")
    }
    assert effective_state(baseline, paths, strict=True)[0] == schema_state(recorded)
    assert (state_dir / "model_state.primary.json").read_bytes() == (
        state_dir / "model_state.json"
    ).read_bytes()


@pytest.mark.parametrize("database", [None, "primary"])
def test_generate_apply_split_resume_and_regenerate_deleted_deferred(
    tmp_path, monkeypatch, database
):
    set_dev_mode(False)
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "app.db"
    url = "sqlite:///" + db.as_posix()
    (tmp_path / "dbwarden.py").write_text(
        "from dbwarden import database_config\n"
        f"database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync={url!r}, model_paths=['models.py'])\n",
        encoding="utf-8",
    )
    (tmp_path / "models.py").write_text("", encoding="utf-8")
    directory = tmp_path / "migrations/primary"
    directory.mkdir(parents=True)
    (tmp_path / ".dbwarden").mkdir()
    (tmp_path / ".dbwarden/model_state.primary.json").write_text(
        json.dumps(model_state_to_dict([])), encoding="utf-8"
    )
    tables = [
        ModelTable(
            name=name,
            columns=[
                ModelColumn(
                    name="id",
                    type="INTEGER",
                    primary_key=True,
                    nullable=False,
                    unique=False,
                    default=None,
                    foreign_key=None,
                )
            ],
        )
        for name in ("items", "obsolete")
    ]
    monkeypatch.setattr(
        "dbwarden.commands.make_migrations.get_all_model_tables",
        lambda *args, **kwargs: tables,
    )
    monkeypatch.setattr(
        "dbwarden.engine.discovery.get_all_model_tables", lambda *args, **kwargs: tables
    )
    try:
        make_migrations_cmd(database="primary", offline=True)
        migrate_cmd(database=database)
        tables.pop()
        tables[0].columns.append(
            ModelColumn(
                name="nickname",
                type="TEXT",
                nullable=True,
                primary_key=False,
                unique=False,
                default=None,
                foreign_key=None,
            )
        )
        make_migrations_cmd(database="primary", split_at_severity="WARN")
        paths = sorted(directory.glob("*.sql"))
        assert len(paths) == 3
        assert read_trusted_plan(paths[-2])[0]["severity"]["file"] == "INFO"
        assert read_trusted_plan(paths[-1])[0]["severity"]["file"] == "CRITICAL"
        make_migrations_cmd(database="primary", offline=True)
        assert sorted(directory.glob("*.sql")) == paths
        with pytest.raises(typer.Exit) as exc:
            migrate_cmd(database=database, max_severity="INFO")
        assert exc.value.exit_code == 3
        with sqlite3.connect(db) as connection:
            assert "nickname" in [
                row[1] for row in connection.execute("PRAGMA table_info(items)")
            ]
            assert connection.execute(
                "SELECT name FROM sqlite_master WHERE name='obsolete'"
            ).fetchone()
        make_migrations_cmd(database="primary", offline=True)
        assert sorted(directory.glob("*.sql")) == paths
        paths[-1].unlink()
        paths[-1].with_suffix(".plan.json").unlink()
        make_migrations_cmd(database="primary", offline=True, split_at_severity="WARN")
        pending = max(directory.glob("*.sql"))
        assert read_trusted_plan(pending)[0]["upgrade_ops"][0]["type"] == "drop_table"
        migrate_cmd(database=database, force=True)
        state_dir = tmp_path / ".dbwarden"
        assert (state_dir / "model_state.primary.json").read_bytes() == (state_dir / "model_state.json").read_bytes()
        assert not (state_dir / "model_state.default.json").exists()
        make_migrations_cmd(database="primary", offline=True)
        assert len(list(directory.glob("*.sql"))) == 3
    finally:
        from dbwarden.connection.connection import dispose_engine

        dispose_engine(url, "sqlite")
