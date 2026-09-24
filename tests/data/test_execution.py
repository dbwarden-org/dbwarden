import json

import pytest
from sqlalchemy import create_engine, event, text

from dbwarden.data.execution import execute_data_plan, reconcile_data_plan


def _plan(*, rollback_policy="restore_preserved_source", maximum=0):
    return {
        "data_spec": {"rollback_policy": rollback_policy},
        "data_bundle": {"manifest_version": 1},
        "data_execution": {
            "upgrade": [
                {
                    "operation_id": "write",
                    "sql": ["INSERT INTO values_table (value) VALUES (1)"],
                    "guards": [
                        {
                            "probe_id": "empty",
                            "query": "SELECT count(*) FROM values_table",
                            "maximum": maximum,
                            "minimum": None,
                            "timing": "before",
                            "message": "table is not empty",
                        }
                    ],
                }
            ],
            "rollback": [
                {
                    "operation_id": "clear",
                    "sql": ["DELETE FROM values_table"],
                    "guards": [],
                }
            ],
        },
    }


@pytest.fixture
def connection():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(text("CREATE TABLE values_table (value INTEGER NOT NULL)"))
        connection.commit()
        yield connection


def test_execute_is_atomic_journaled_and_at_most_once(connection):
    calls = []
    result = execute_data_plan(
        connection,
        _plan(),
        migration_id="primary__0001",
        record_success=lambda: calls.append("success"),
    )

    assert result["status"] == "APPLIED_SUCCESS"
    assert (
        connection.execute(text("SELECT count(*) FROM values_table")).scalar_one() == 1
    )
    assert calls == ["success"]
    assert (
        execute_data_plan(connection, _plan(), migration_id="primary__0001")["status"]
        == "already_applied"
    )
    assert (
        connection.execute(text("SELECT count(*) FROM values_table")).scalar_one() == 1
    )


def test_guard_failure_rolls_back_and_records_retryable_attempt(connection):
    with pytest.raises(ValueError, match="table is not empty"):
        execute_data_plan(connection, _plan(maximum=-1), migration_id="primary__0001")

    assert (
        connection.execute(text("SELECT count(*) FROM values_table")).scalar_one() == 0
    )
    state = reconcile_data_plan(connection, "primary__0001")
    assert state["status"] == "FAILED_RETRYABLE"
    assert state["retry_allowed"] is True


def test_rejects_mutating_guard_and_irreversible_rollback(connection):
    plan = _plan()
    plan["data_execution"]["upgrade"][0]["guards"][0]["query"] = (
        "DELETE FROM values_table"
    )
    with pytest.raises(ValueError, match="read-only"):
        execute_data_plan(connection, plan, migration_id="primary__0001")

    with pytest.raises(ValueError, match="irreversible"):
        execute_data_plan(
            connection,
            _plan(rollback_policy="irreversible"),
            migration_id="primary__0002",
            direction="rollback",
        )


def test_baseline_records_structured_acknowledgement(connection):
    result = execute_data_plan(
        connection,
        _plan(),
        migration_id="primary__0001",
        baseline=True,
        baseline_reason="existing production data accepted",
    )

    assert result["status"] == "APPLIED_SUCCESS"
    assert result["baseline"] is True
    assert (
        connection.execute(text("SELECT count(*) FROM values_table")).scalar_one() == 0
    )
    row = connection.execute(
        text("SELECT details FROM _dbwarden_data_events WHERE event_type = 'BASELINE'")
    ).scalar_one()
    assert json.loads(row) == {
        "acknowledged": True,
        "reason": "existing production data accepted",
        "skipped_checks": ["guard:upgrade:write:before:empty"],
    }
    assert (
        connection.execute(
            text(
                "SELECT error FROM _dbwarden_data_runs WHERE migration_id = 'primary__0001'"
            )
        ).scalar_one()
        is None
    )


def test_rollback_requires_explicit_reapply_and_links_new_epoch(connection):
    first = execute_data_plan(connection, _plan(), migration_id="primary__0001")
    execute_data_plan(
        connection, _plan(), migration_id="primary__0001", direction="rollback"
    )

    with pytest.raises(RuntimeError, match="explicit reapply"):
        execute_data_plan(connection, _plan(), migration_id="primary__0001")

    reapplied = execute_data_plan(
        connection, _plan(), migration_id="primary__0001", reapply=True
    )
    assert reapplied["epoch"] == first["epoch"] + 1
    details = connection.execute(
        text("SELECT details FROM _dbwarden_data_events WHERE event_type = 'REAPPLY'")
    ).scalar_one()
    assert json.loads(details) == {"previous_epoch": first["epoch"]}


@pytest.mark.parametrize("status", ["ABANDONED", "FAILED_FINAL"])
def test_terminal_status_cannot_start_new_epoch(connection, status):
    execute_data_plan(connection, _plan(), migration_id="primary__0001")
    connection.execute(
        text(
            "UPDATE _dbwarden_data_runs SET status = :status WHERE migration_id = 'primary__0001'"
        ),
        {"status": status},
    )
    connection.commit()

    with pytest.raises(RuntimeError, match=f"terminally {status}"):
        execute_data_plan(
            connection, _plan(), migration_id="primary__0001", reapply=True
        )


def test_repeated_success_executes_no_mutating_sql(connection):
    connection.execute(
        text(
            "CREATE TABLE transition_edges (source_id TEXT, target_id INTEGER, "
            "owned_write INTEGER)"
        )
    )
    connection.execute(text("INSERT INTO transition_edges VALUES ('source', 1, 1)"))
    connection.commit()
    plan = _plan()
    plan["data_execution"]["upgrade"][0]["identity_edges"] = {
        "definition_id": "transition.target",
        "ledger_table": "transition_edges",
        "source_columns": ["source_id"],
        "target_columns": ["target_id"],
        "value_columns": [],
        "owned_column": "owned_write",
    }
    execute_data_plan(connection, plan, migration_id="primary__0001")
    connection.execute(text("DROP TABLE _dbwarden_data_edges"))
    connection.execute(text("DROP TABLE _dbwarden_data_keys"))
    connection.commit()
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.strip().upper())

    event.listen(connection.engine, "before_cursor_execute", capture)
    try:
        result = execute_data_plan(connection, plan, migration_id="primary__0001")
    finally:
        event.remove(connection.engine, "before_cursor_execute", capture)

    assert result["status"] == "already_applied"
    assert not any(
        statement.startswith(("CREATE", "INSERT", "UPDATE", "DELETE", "DROP"))
        for statement in statements
    )
