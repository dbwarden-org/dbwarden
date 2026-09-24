import pytest
from sqlalchemy import create_engine, text

from dbwarden.data.execution import (
    _prepare_identity_edges,
    _record_identity_edges,
    execute_data_plan,
    verify_identity_edges,
)

NFC = "\u00e9"
NFD = "e\u0301"


def _step():
    return {
        "operation_id": "copy_identity",
        "sql": ["INSERT INTO values_table (id, mapped_value) VALUES (1, 7)"],
        "guards": [
            {
                "probe_id": "target_written",
                "query": "SELECT COUNT(*) FROM values_table WHERE id = 1",
                "minimum": 1,
                "maximum": 1,
                "timing": "after",
            }
        ],
        "identity_edges": {
            "definition_id": "transition.target",
            "ledger_table": "transition_edges",
            "source_columns": ["source_id"],
            "target_identity_columns": ["target_id"],
            "value_columns": ["mapped_value"],
            "target_table": {"schema": None, "table": "values_table"},
            "target_columns": ["mapped_value"],
            "target_key_columns": ["id"],
            "owned_column": "owned_write",
        },
    }


def _schema(connection):
    connection.execute(
        text(
            "CREATE TABLE values_table (id INTEGER PRIMARY KEY, mapped_value INTEGER NOT NULL)"
        )
    )
    connection.execute(
        text(
            "CREATE TABLE transition_edges (source_id TEXT PRIMARY KEY, "
            "target_id INTEGER NOT NULL, mapped_value INTEGER NOT NULL, "
            "owned_write INTEGER NOT NULL)"
        )
    )
    connection.commit()


def test_nfc_collision_rolls_back_target_mutation():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        _schema(connection)
        connection.execute(
            text("INSERT INTO transition_edges VALUES (:source, 1, 7, 1)"),
            [{"source": NFC}, {"source": NFD}],
        )
        connection.commit()
        step = _step()
        plan = {
            "data_spec": {"rollback_policy": "restore_preserved_source"},
            "data_bundle": {"manifest_version": 1},
            "data_execution": {"upgrade": [step], "rollback": []},
        }

        with pytest.raises(ValueError, match="Duplicate normalized source identity"):
            execute_data_plan(connection, plan, migration_id="primary__0001")

        assert (
            connection.execute(text("SELECT COUNT(*) FROM values_table")).scalar_one()
            == 0
        )


def test_existing_edge_retry_is_idempotent_but_verifier_rejects_duplicate_ledger():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        _schema(connection)
        connection.execute(text("INSERT INTO values_table VALUES (1, 7)"))
        connection.execute(
            text("INSERT INTO transition_edges VALUES (:source, 1, 7, 1)"),
            {"source": NFC},
        )
        step = _step()
        _prepare_identity_edges(connection, [step], clickhouse=False)
        _record_identity_edges(
            connection,
            step,
            "primary__0001",
            1,
            "run-1",
            "copy_identity",
            clickhouse=False,
        )
        _record_identity_edges(
            connection,
            step,
            "primary__0001",
            1,
            "run-2",
            "copy_identity",
            clickhouse=False,
        )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM _dbwarden_data_edges")
            ).scalar_one()
            == 1
        )
        connection.execute(
            text("INSERT INTO transition_edges VALUES (:source, 1, 7, 1)"),
            {"source": NFD},
        )

        with pytest.raises(ValueError, match="Duplicate normalized source identity"):
            verify_identity_edges(connection, step, "primary__0001", 1)
