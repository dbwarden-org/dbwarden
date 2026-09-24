import json

import pytest
from sqlalchemy import create_engine, text

from dbwarden.data.compiler import compile_data, discover_data
from dbwarden.data.execution import execute_data_plan
from dbwarden.data.planning import plan_data
from dbwarden.data.snapshots import register_snapshot

MODEL = """from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base
from dbwarden.data import DataMeta, rows, derive, col, func
Base = declarative_base()
class Country(Base):
    __tablename__ = "countries"
    code = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    normalized = Column(String)
    class Data(DataMeta):
        managed_rows = rows(key="code", rows=[{"code": "UY", "name": "Uruguay"}], owned_columns=["name"], rollback="restore_previous")
        transformations = [derive("normalized", func.lower(col("name")), rollback="clear")]
"""


def compile_project(tmp_path, monkeypatch, source=MODEL, transitions=None):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models.py").write_text(source, encoding="utf-8")
    paths = []
    if transitions:
        (tmp_path / "transitions.py").write_text(transitions, encoding="utf-8")
        paths = ["transitions.py"]
    return compile_data(
        discover_data(["models.py"], paths), database="primary", backend="sqlite"
    )


def execution_plan(spec, ops):
    return {
        "data_spec": spec,
        "data_bundle": {},
        "upgrade_ops": ops,
        "data_execution": {
            "upgrade": [step for op in ops for step in op["data_upgrade"]],
            "rollback": [step for op in reversed(ops) for step in op["data_rollback"]],
        },
    }


def test_rows_and_derivation_apply_rollback_and_reapply(tmp_path, monkeypatch):
    spec = compile_project(tmp_path, monkeypatch)
    ops = plan_data(spec)
    assert plan_data(spec, spec) == []
    plan = execution_plan(spec, ops)
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(
            text(
                "CREATE TABLE countries(code TEXT PRIMARY KEY, name TEXT NOT NULL, normalized TEXT)"
            )
        )
        connection.commit()
        execute_data_plan(connection, plan, migration_id="primary__0001")
        assert connection.execute(text("SELECT * FROM countries")).all() == [
            ("UY", "Uruguay", "uruguay")
        ]
        connection.commit()
        execute_data_plan(
            connection, plan, migration_id="primary__0001", direction="rollback"
        )
        assert connection.execute(text("SELECT count(*) FROM countries")).scalar() == 0
        connection.commit()
        execute_data_plan(connection, plan, migration_id="primary__0001", reapply=True)
        assert connection.execute(text("SELECT count(*) FROM countries")).scalar() == 1


def test_managed_delete_keeps_preexisting_rows(tmp_path, monkeypatch):
    source = MODEL.replace(
        'rollback="restore_previous"', 'rollback="irreversible"'
    ).replace(
        '        transformations = [derive("normalized", func.lower(col("name")), rollback="clear")]',
        "",
    )
    initial = compile_project(tmp_path, monkeypatch, source)
    updated = compile_project(
        tmp_path,
        monkeypatch,
        source.replace('[{"code": "UY", "name": "Uruguay"}]', "[]").replace(
            'rollback="irreversible"',
            'rollback="irreversible", on_missing="delete", scope=col("code")=="UY", acknowledge_delete=True',
        ),
    )
    with create_engine("sqlite://").connect() as connection:
        connection.execute(
            text(
                "CREATE TABLE countries(code TEXT PRIMARY KEY, name TEXT NOT NULL, normalized TEXT)"
            )
        )
        connection.execute(
            text("INSERT INTO countries VALUES ('UY', 'Existing', 'user data')")
        )
        connection.commit()
        execute_data_plan(
            connection, execution_plan(initial, plan_data(initial)), migration_id="1"
        )
        execute_data_plan(
            connection,
            execution_plan(updated, plan_data(updated, initial)),
            migration_id="2",
        )
        assert connection.execute(text("SELECT * FROM countries")).all() == [
            ("UY", "Uruguay", "user data")
        ]


TRANSITION = """from dbwarden.data import DataTransition, historical_table, into
from models import Country
old = historical_table("legacy", snapshot="primary__0001")
class Split(DataTransition):
    source = old
    source_identity = [old.id]
    targets = [into(Country, map={Country.code: old.code, Country.name: old.name}, key=[Country.code], on_conflict="ignore_if_equivalent")]
"""


def test_transition_preserves_source_and_preexisting_target(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    register_snapshot(
        "primary__0001",
        {
            "tables": {
                "legacy": {
                    "columns": {
                        "id": {"type": "INTEGER", "nullable": False},
                        "code": {"type": "VARCHAR", "nullable": False},
                        "name": {"type": "VARCHAR", "nullable": False},
                    }
                }
            }
        },
        database="primary",
        backend="sqlite",
    )
    source = MODEL[: MODEL.index("    class Data")]
    spec = compile_project(tmp_path, monkeypatch, source, TRANSITION)
    plan = execution_plan(spec, plan_data(spec))
    with create_engine("sqlite://").connect() as connection:
        connection.execute(
            text("CREATE TABLE legacy(id INTEGER PRIMARY KEY, code TEXT, name TEXT)")
        )
        connection.execute(
            text("INSERT INTO legacy VALUES (1,'UY','Uruguay'),(2,'AR','Argentina')")
        )
        connection.execute(
            text(
                "CREATE TABLE countries(code TEXT PRIMARY KEY, name TEXT NOT NULL, normalized TEXT)"
            )
        )
        connection.execute(
            text("INSERT INTO countries VALUES ('UY','Uruguay','untouched')")
        )
        connection.commit()
        execute_data_plan(connection, plan, migration_id="2")
        assert connection.execute(text("SELECT count(*) FROM countries")).scalar() == 2
        connection.commit()
        execute_data_plan(connection, plan, migration_id="2", direction="rollback")
        assert connection.execute(text("SELECT * FROM countries")).all() == [
            ("UY", "Uruguay", "untouched")
        ]
        assert connection.execute(text("SELECT count(*) FROM legacy")).scalar() == 2


@pytest.mark.parametrize(
    "replacement,error",
    [
        (('"code": "UY"', '"code": None'), "NULL"),
        (('"name": "Uruguay"', '"name": 3'), "expected str"),
        (('owned_columns=["name"]', 'owned_columns=["missing"]'), "owned_columns"),
    ],
)
def test_invalid_rows_fail_before_sql(tmp_path, monkeypatch, replacement, error):
    with pytest.raises(ValueError, match=error):
        compile_project(tmp_path, monkeypatch, MODEL.replace(*replacement))


def test_offline_unified_bundle_generation(tmp_path, monkeypatch):
    from dbwarden.commands.make_migrations import make_migrations_cmd
    from dbwarden.data.artifacts import verify_bundle
    from dbwarden.engine.core.model_state import model_state_to_dict

    compile_project(tmp_path, monkeypatch)
    (tmp_path / "dbwarden.py").write_text(
        "from dbwarden import database_config\ndatabase_config(database_name='primary',default=True,database_type='sqlite',database_url_sync='sqlite:///unused.db',model_paths=['models.py'])\n"
    )
    (tmp_path / ".dbwarden").mkdir(exist_ok=True)
    (tmp_path / "migrations/primary").mkdir(parents=True)
    (tmp_path / ".dbwarden/model_state.primary.json").write_text(
        json.dumps(model_state_to_dict([]))
    )
    make_migrations_cmd(database="primary", offline=True, concurrent=False)
    sql = next((tmp_path / "migrations/primary").glob("*.sql"))
    verify_bundle(sql)
    assert not (tmp_path / "unused.db").exists()
    before = {
        path: path.read_bytes()
        for path in (tmp_path / "migrations").rglob("*")
        if path.is_file()
    }
    make_migrations_cmd(database="primary", offline=True, concurrent=False)
    assert before == {
        path: path.read_bytes()
        for path in (tmp_path / "migrations").rglob("*")
        if path.is_file()
    }
    import sqlite3

    from dbwarden.commands.migrate import migrate_cmd
    from dbwarden.commands.rollback import rollback_cmd
    from dbwarden.data.convergence import project_data_findings

    migrate_cmd(database="primary", force=True)
    with sqlite3.connect(tmp_path / "unused.db") as connection:
        assert connection.execute("SELECT * FROM countries").fetchall() == [
            ("UY", "Uruguay", "uruguay")
        ]
        assert connection.execute(
            "SELECT status FROM _dbwarden_data_runs"
        ).fetchone() == ("APPLIED_SUCCESS",)
    assert project_data_findings("primary") == []
    rollback_cmd(database="primary", count=1)
    with sqlite3.connect(tmp_path / "unused.db") as connection:
        assert connection.execute(
            "SELECT status FROM _dbwarden_data_runs"
        ).fetchone() == ("ROLLED_BACK",)
