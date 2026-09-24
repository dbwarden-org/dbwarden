import json
import sqlite3
from copy import deepcopy

from dbwarden.commands.make_migrations.generation import generate_files
from dbwarden.commands.migrate import migrate_cmd
from dbwarden.commands.reconcile import reconcile_cmd
from dbwarden.config import set_dev_mode
from dbwarden.engine.generation_state import schema_state
from dbwarden.engine.offline import diff_model_states
from dbwarden.engine.snapshot import extract_full_schema_snapshot
from dbwarden.merge.detection import check_dirty_environment
from dbwarden.merge.marker import mark_file_superseded
from dbwarden.repositories import get_migrated_versions


def test_reconcile_real_sql_converges_and_does_not_replay_normal_reconciliation(tmp_path, monkeypatch):
    set_dev_mode(False)
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "app.db"
    url = "sqlite:///" + db.as_posix()
    monkeypatch.setenv("DBWARDEN_TEST_STAGE", url)
    (tmp_path / "dbwarden.py").write_text(
        "from dbwarden import database_config\n"
        f"database_config(database_name='primary', default=True, database_type='sqlite', database_url_sync={url!r}, environments=[dict(name='staging', url_env='DBWARDEN_TEST_STAGE', persistent=True)])\n",
        encoding="utf-8",
    )
    directory = tmp_path / "migrations/primary"
    directory.mkdir(parents=True)
    branch = directory / "primary__0001_branch.sql"
    branch.write_text("-- upgrade\nCREATE TABLE items (id INTEGER NOT NULL PRIMARY KEY, obsolete TEXT);\n-- rollback\nDROP TABLE items;\n", encoding="utf-8")
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE items (id INTEGER NOT NULL PRIMARY KEY, obsolete TEXT)")
    try:
        migrate_cmd(database="primary", baseline=True, to_version="0001")
        actual = schema_state(extract_full_schema_snapshot(database="primary"))
        target = deepcopy(actual)
        del target["tables"]["items"]["columns"]["obsolete"]
        empty = {"format_version": 2, "tables": {}, "indexes": {}, "constraints": {}, "enums": {}}
        up, down = diff_model_states(empty, target, db_name="primary")
        artifacts = generate_files(up, down, migrations_dir=str(directory), database="primary", db_name="primary", baseline=empty, target=target)
        mark_file_superseded(branch, merged_into="0002", merge_base="baseline", branch="feature")
        state_path = tmp_path / ".dbwarden/model_state.primary.json"
        state_path.parent.mkdir(exist_ok=True)
        state_path.write_text(json.dumps({**target, "generation_base": empty}), encoding="utf-8")
        record_path = tmp_path / ".dbwarden/merges/0002.json"
        record_path.parent.mkdir()
        record_path.write_text(json.dumps({"database": "primary", "superseded_files": [branch.name], "superseded_versions": ["0001"], "reconciliation_versions": ["0002"], "reconciliation_files": [artifacts[0]["filename"]], "probe_results": {"staging": "dirty"}}), encoding="utf-8")
        assert check_dirty_environment("primary")
        reconcile_cmd("staging", database="primary", dry_run=True, force=True)
        assert not (tmp_path / ".dbwarden/reconciliations").exists()
        reconcile_cmd("staging", database="primary", force=True)
        assert set(get_migrated_versions("primary")) == {"0001", "0002"}
        assert not check_dirty_environment("primary")
        with sqlite3.connect(db) as connection:
            assert [row[1] for row in connection.execute("PRAGMA table_info(items)")] == ["id"]
        migrate_cmd(database="primary")
        reconcile_cmd("staging", database="primary", force=True)
        assert json.loads(record_path.read_text())["probe_results"]["staging"] == "reconciled"
    finally:
        from dbwarden.connection.connection import dispose_engine
        dispose_engine(url, "sqlite")
