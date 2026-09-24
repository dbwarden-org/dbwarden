import json
import sqlite3

import pytest

from dbwarden.engine.core.model_state import model_state_to_dict

MODEL = """from sqlalchemy import Column, String
from sqlalchemy.orm import declarative_base
from dbwarden.data import DataMeta, rows
Base = declarative_base()
class Country(Base):
    __tablename__ = "countries"
    code = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    class Data(DataMeta):
        managed_rows = rows(key="code", rows=[{"code": "UY", "name": "Uruguay"}], owned_columns=["name"], rollback="restore_previous")
"""


def _make_project(tmp_path, monkeypatch, backend="sqlite", model=MODEL):
    from dbwarden.commands.make_migrations import make_migrations_cmd

    monkeypatch.chdir(tmp_path)
    (tmp_path / "models.py").write_text(model, encoding="utf-8")
    url = "sqlite:///target.db" if backend == "sqlite" else "mysql://unused"
    (tmp_path / "dbwarden.py").write_text(
        "from dbwarden import database_config\n"
        "database_config(database_name='primary', default=True, "
        f"database_type='{backend}', database_url_sync='{url}', "
        "model_paths=['models.py'])\n",
        encoding="utf-8",
    )
    (tmp_path / ".dbwarden").mkdir()
    (tmp_path / "migrations" / "primary").mkdir(parents=True)
    (tmp_path / ".dbwarden" / "model_state.primary.json").write_text(
        json.dumps(model_state_to_dict([])), encoding="utf-8"
    )
    make_migrations_cmd(database="primary", offline=True, concurrent=False)


def test_sqlite_rollback_refuses_to_drop_table_with_application_rows(
    tmp_path, monkeypatch
):
    from dbwarden.commands.migrate import migrate_cmd
    from dbwarden.commands.rollback import rollback_cmd

    _make_project(tmp_path, monkeypatch)
    migrate_cmd(database="primary", force=True)
    with sqlite3.connect(tmp_path / "target.db") as connection:
        connection.execute("INSERT INTO countries VALUES ('AR', 'Argentina')")

    with pytest.raises(ValueError, match="contains rows"):
        rollback_cmd(database="primary", count=1)

    with sqlite3.connect(tmp_path / "target.db") as connection:
        assert connection.execute(
            "SELECT code, name FROM countries ORDER BY code"
        ).fetchall() == [("AR", "Argentina"), ("UY", "Uruguay")]


def test_sqlite_rollback_drops_empty_target_after_owned_cleanup(tmp_path, monkeypatch):
    from dbwarden.commands.migrate import migrate_cmd
    from dbwarden.commands.rollback import rollback_cmd

    _make_project(tmp_path, monkeypatch)
    migrate_cmd(database="primary", force=True)
    rollback_cmd(database="primary", count=1)

    with sqlite3.connect(tmp_path / "target.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='countries'"
        ).fetchone() == (0,)


def test_mysql_bundle_renames_checks_then_drops_new_target(tmp_path, monkeypatch):
    from dbwarden.engine.file_parser import parse_rollback_statements

    _make_project(tmp_path, monkeypatch, backend="mysql")
    plan_path = next((tmp_path / "migrations" / "primary").glob("*.plan.json"))
    rollback = json.loads(plan_path.read_text(encoding="utf-8"))["data_execution"][
        "rollback"
    ]

    delete_index = next(
        index
        for index, step in enumerate(rollback)
        if any(sql.startswith("DELETE FROM `countries`") for sql in step["sql"])
    )
    rename_index = next(
        index
        for index, step in enumerate(rollback)
        if any(sql.startswith("RENAME TABLE `countries` TO") for sql in step["sql"])
    )
    drop_index = next(
        index
        for index, step in enumerate(rollback)
        if any(sql.startswith("DROP TABLE `_dbwarden_rollback_") for sql in step["sql"])
    )

    assert delete_index < rename_index < drop_index
    renamed = rollback[rename_index]["sql"][0].split(" TO ", 1)[1].removesuffix(";")
    assert rollback[drop_index]["sql"] == [f"DROP TABLE {renamed};"]
    assert rollback[drop_index]["guards"][0]["query"] == (
        f"SELECT COUNT(*) FROM {renamed}"
    )
    assert rollback[rename_index]["step_id"] != rollback[drop_index]["step_id"]
    sql_path = next((tmp_path / "migrations" / "primary").glob("*.sql"))
    emitted = [
        statement.strip().removesuffix(";")
        for statement in parse_rollback_statements(sql_path)
    ]
    planned = [
        sql.strip().removesuffix(";") for step in rollback for sql in step["sql"]
    ]
    assert emitted == planned


def test_mysql_guarded_drop_uses_rendered_drop_schema():
    from dbwarden.commands.make_migrations.generation import _schema_rollback_steps

    operation = {
        "id": "create-tenant-countries",
        "type": "create_table",
        "table": "countries",
        "state_table": {"schema": None},
    }

    rename, drop = _schema_rollback_steps(
        operation,
        {
            "operation_id": operation["id"],
            "sql": ["DROP TABLE `tenant`.`countries`;"],
            "guards": [],
        },
        "mysql",
    )

    assert rename["sql"][0].startswith(
        "RENAME TABLE `tenant`.`countries` TO `tenant`.`_dbwarden_rollback_"
    )
    assert drop["sql"][0].startswith("DROP TABLE `tenant`.`_dbwarden_rollback_")
