from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from dbwarden.data.execution import (
    DataOwnershipError,
    execute_data_plan,
    reconcile_data_plan,
)
from tests.data.test_nontransactional import MySQLConnection, _plan


class TransactionalMySQL(MySQLConnection):
    storage_engine = "InnoDB"
    autocommit = 0

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if sql == "SELECT @@autocommit":
            return SimpleNamespace(scalar_one=lambda: self.autocommit)
        if sql.startswith("SELECT ENGINE FROM information_schema.TABLES"):
            return SimpleNamespace(scalar_one=lambda: self.storage_engine)
        return super().execute(statement, parameters)


@pytest.fixture
def connection():
    engine = create_engine("sqlite://")
    with engine.connect() as raw:
        raw.execute(text("CREATE TABLE values_table(value INTEGER UNIQUE)"))
        raw.commit()
        yield TransactionalMySQL(raw)
    engine.dispose()


def plan():
    value = _plan()
    step = value["data_execution"]["upgrade"][0]
    step["sql"] = [
        "INSERT INTO values_table SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM values_table WHERE value = 1)"
    ]
    step["insert_proofs"] = [{"statement_index": 0, "expected": 1}]
    return value


def test_equivalent_concurrent_insert_never_becomes_owned(connection):
    value = plan()

    def concurrent_insert(_statement):
        connection.execute(text("INSERT INTO values_table VALUES (1)"))
        connection.commit()

    with pytest.raises(DataOwnershipError, match="affected rows"):
        execute_data_plan(
            connection, value, migration_id="race", before_statement=concurrent_insert
        )
    assert connection.execute(text("SELECT value FROM values_table")).all() == [(1,)]
    state = reconcile_data_plan(connection, "race")
    assert state["status"] == "FAILED_FINAL"
    assert not state["retry_allowed"]
    with pytest.raises(RuntimeError, match="terminally"):
        execute_data_plan(connection, value, migration_id="race", reapply=True)


def test_crash_after_verified_insert_recovers_without_reinsertion(connection):
    value = plan()

    def crash(_statement):
        raise RuntimeError("injected failure after verified insert")

    with pytest.raises(RuntimeError, match="injected failure"):
        execute_data_plan(
            connection, value, migration_id="verified", after_statement=crash
        )
    state = reconcile_data_plan(connection, "verified")
    assert state["status"] == "UNKNOWN_REQUIRES_RECONCILIATION"
    assert state["checkpoints"][0]["status"] == "WRITE_VERIFIED"
    reconcile_data_plan(
        connection, "verified", plan=value, apply=True, decision="verified_retry"
    )
    result = execute_data_plan(connection, value, migration_id="verified")
    assert result["status"] == "APPLIED_SUCCESS"
    assert connection.execute(text("SELECT value FROM values_table")).all() == [(1,)]


def test_unknown_insert_cannot_infer_ownership_from_equivalent_postcondition(
    connection,
):
    value = plan()
    connection.target = "INSERT INTO values_table"
    connection.fail_after_effect = True
    with pytest.raises(RuntimeError):
        execute_data_plan(connection, value, migration_id="unknown")
    state = reconcile_data_plan(connection, "unknown")
    assert state["status"] == "UNKNOWN_REQUIRES_RECONCILIATION"
    assert state["checkpoints"][0]["status"] == "PENDING"
    with pytest.raises(ValueError, match="ownership is unproven"):
        reconcile_data_plan(
            connection, "unknown", plan=value, apply=True, decision="verified_retry"
        )


@pytest.mark.parametrize(
    "attribute,value,message",
    [
        ("storage_engine", "MyISAM", "InnoDB"),
        ("autocommit", 1, "transactional MySQL DML"),
    ],
)
def test_owned_inserts_require_atomic_data_and_checkpoint_storage(
    connection, attribute, value, message
):
    setattr(connection, attribute, value)
    with pytest.raises(ValueError, match=message):
        execute_data_plan(connection, plan(), migration_id="unsupported")
    assert (
        connection.execute(text("SELECT count(*) FROM values_table")).scalar_one() == 0
    )
