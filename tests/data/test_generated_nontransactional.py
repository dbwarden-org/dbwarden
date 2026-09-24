import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from dbwarden.data.compiler import compile_data, discover_data
from dbwarden.data.convergence import check_convergence
from dbwarden.data.execution import execute_data_plan, reconcile_data_plan
from dbwarden.data.planning import plan_data
from dbwarden.data.snapshots import register_snapshot
from tests.data.test_insert_proofs import TransactionalMySQL
from tests.data.test_nontransactional import MySQLConnection

MODEL = """from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base
from dbwarden.data import DataTransition, historical_table, into
Base = declarative_base()
class Person(Base):
    __tablename__ = "people"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
class Address(Base):
    __tablename__ = "addresses"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
old = historical_table("legacy", snapshot="primary__0001")
class Split(DataTransition):
    source = old
    source_identity = [old.id]
    coverage = "exactly_once"
    targets = [
        into(Person, map={Person.id: old.id, Person.name: old.name}, where=old.kind == "person"),
        into(Address, map={Address.id: old.id, Address.name: old.name}, where=old.kind == "address"),
    ]
"""


class SQLiteMySQL(TransactionalMySQL):
    def __init__(self, connection):
        super().__init__(connection)
        self.trace = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.trace.append(sql)
        if sql == "SELECT @@autocommit" or sql.startswith(
            "SELECT ENGINE FROM information_schema.TABLES"
        ):
            return super().execute(statement, parameters)
        if sql.startswith("SELECT COUNT(*) FROM information_schema.tables"):
            name = re.search(r"table_name = '([^']+)'", sql).group(1)
            return self.connection.execute(
                text(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'table' AND name = :name"
                ),
                {"name": name},
            )
        translated = sql.replace(" <=> ", " IS NOT DISTINCT FROM ")
        translated = translated.removesuffix(" FOR UPDATE")
        rename = re.fullmatch(
            r"RENAME TABLE (.+?) TO (.+?);?", translated, flags=re.IGNORECASE
        )
        if rename:
            translated = f"ALTER TABLE {rename.group(1)} RENAME TO {rename.group(2)}"
        return MySQLConnection.execute(self, text(translated), parameters)

    def commit(self):
        self.trace.append("<COMMIT>")
        super().commit()


def _plan(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    register_snapshot(
        "primary__0001",
        {
            "tables": {
                "legacy": {
                    "columns": {
                        "id": {"type": "INTEGER", "nullable": False},
                        "name": {"type": "VARCHAR", "nullable": False},
                        "kind": {"type": "VARCHAR", "nullable": False},
                    }
                }
            }
        },
        database="primary",
        backend="mysql",
    )
    Path("models.py").write_text(MODEL, encoding="utf-8")
    spec = compile_data(
        discover_data(["models.py"]), database="primary", backend="mysql"
    )
    operations = plan_data(spec)
    return {
        "data_spec": spec,
        "data_bundle": {},
        "upgrade_ops": operations,
        "data_execution": {
            "upgrade": [
                step for operation in operations for step in operation["data_upgrade"]
            ],
            "rollback": [
                step
                for operation in reversed(operations)
                for step in operation["data_rollback"]
            ],
        },
    }


def test_generated_mysql_multitarget_recovery_and_rollback(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch)
    plan["migration_id"] = "split"
    upgrade = plan["data_execution"]["upgrade"]
    assert len({step["step_id"] for step in upgrade}) == len(upgrade)
    assert len({step["operation_id"] for step in upgrade}) == 1
    target_steps = [step for step in upgrade if step.get("identity_edges")]
    assert len(target_steps) == 2
    first_insert = target_steps[0]["sql"][
        target_steps[0]["insert_proofs"][0]["statement_index"]
    ]

    engine = create_engine("sqlite://")
    with engine.connect() as raw:
        raw.execute(
            text("CREATE TABLE legacy(id INTEGER PRIMARY KEY, name TEXT, kind TEXT)")
        )
        raw.execute(text("CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT)"))
        raw.execute(text("CREATE TABLE addresses(id INTEGER PRIMARY KEY, name TEXT)"))
        raw.execute(
            text(
                "INSERT INTO legacy VALUES (1, 'Person', 'person'), "
                "(2, 'Address', 'address')"
            )
        )
        raw.commit()
        connection = SQLiteMySQL(raw)

        def crash_after_first_target(statement):
            if statement == first_insert:
                raise RuntimeError("injected after first generated target")

        with pytest.raises(RuntimeError, match="first generated target"):
            execute_data_plan(
                connection,
                plan,
                migration_id="split",
                after_statement=crash_after_first_target,
            )
        state = reconcile_data_plan(connection, "split")
        assert state["status"] == "UNKNOWN_REQUIRES_RECONCILIATION"
        reconciled = reconcile_data_plan(
            connection, "split", plan=plan, apply=True, decision="verified_retry"
        )
        assert reconciled["status"] == "FAILED_RETRYABLE"
        assert any(
            row["statement_index"] == -1 and row["status"] == "DONE"
            for row in reconciled["checkpoints"]
        )

        result = execute_data_plan(connection, plan, migration_id="split")
        assert result["status"] == "APPLIED_SUCCESS"
        assert raw.execute(text("SELECT * FROM people")).all() == [(1, "Person")]
        assert raw.execute(text("SELECT * FROM addresses")).all() == [(2, "Address")]
        assert (
            raw.execute(
                text(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'legacy'"
                )
            ).scalar_one()
            == 0
        )

        monkeypatch.setattr("dbwarden.data.convergence.inspect", lambda _: inspect(raw))
        assert check_convergence(connection, plan["data_spec"], plans=[plan]) == []

        connection.trace.clear()
        rolled_back = execute_data_plan(
            connection, plan, migration_id="split", direction="rollback"
        )
        assert rolled_back["status"] == "ROLLED_BACK"
        delete_step = next(
            step
            for step in plan["data_execution"]["rollback"]
            if any(
                statement.startswith("DELETE FROM `people`")
                for statement in step["sql"]
            )
        )
        delete_sql = delete_step["sql"][0]
        before_guard = next(
            guard["query"]
            for guard in delete_step["guards"]
            if guard["timing"] == "before"
        )
        after_guard = next(
            guard["query"]
            for guard in delete_step["guards"]
            if guard["timing"] == "after"
        )
        lock_index = next(
            index
            for index, statement in enumerate(connection.trace)
            if statement.startswith("SELECT 1 FROM `people` AS t")
            and statement.endswith("FOR UPDATE")
        )
        delete_index = connection.trace.index(delete_sql)
        before_index = connection.trace.index(before_guard)
        after_index = connection.trace.index(after_guard)
        pending_index = max(
            index
            for index, statement in enumerate(connection.trace[:lock_index])
            if statement.startswith("INSERT INTO _dbwarden_data_operations")
        )
        assert (
            pending_index
            < connection.trace.index("<COMMIT>", pending_index)
            < lock_index
        )
        assert lock_index < before_index < delete_index < after_index
        assert "<COMMIT>" not in connection.trace[lock_index:after_index]
        assert raw.execute(text("SELECT * FROM legacy ORDER BY id")).all() == [
            (1, "Person", "person"),
            (2, "Address", "address"),
        ]
        assert raw.execute(text("SELECT COUNT(*) FROM people")).scalar_one() == 0
        assert raw.execute(text("SELECT COUNT(*) FROM addresses")).scalar_one() == 0


def test_duplicate_runtime_step_ids_fail_before_user_sql():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(text("CREATE TABLE values_table(value INTEGER)"))
        connection.commit()
        step = {
            "operation_id": "same",
            "sql": ["INSERT INTO values_table VALUES (1)"],
            "guards": [],
        }
        plan = {
            "data_spec": {"rollback_policy": "restore_previous"},
            "data_bundle": {},
            "data_execution": {"upgrade": [step, dict(step)], "rollback": []},
        }
        with pytest.raises(ValueError, match="unique step_id"):
            execute_data_plan(connection, plan, migration_id="duplicate")
        assert (
            connection.execute(text("SELECT COUNT(*) FROM values_table")).scalar_one()
            == 0
        )
