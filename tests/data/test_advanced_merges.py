import os
import uuid

import pytest
from sqlalchemy import Column, Integer, String, create_engine, text
from sqlalchemy.orm import declarative_base

from dbwarden.data import (
    DataTransition,
    aggregate,
    col,
    from_source,
    historical_table,
    into,
    merge_sources,
    winner,
)
from dbwarden.data.compiler import DiscoveredData, compile_data
from dbwarden.data.convergence import check_convergence
from dbwarden.data.execution import execute_data_plan
from dbwarden.data.planning import plan_data


def _compile(
    monkeypatch, *, policy="winner", backend="sqlite", batching=None, allow_empty=False
):
    base = declarative_base()

    class Account(base):
        __tablename__ = "accounts"
        id = Column(Integer, primary_key=True)
        total = Column(Integer, nullable=False)
        name = Column(String, nullable=False)

    def snapshot(identity, **_):
        return {
            "format_version": 1,
            "snapshot_id": identity,
            "database": "primary",
            "backend": backend,
            "schema_checksum": "checked",
            "data_checksum": None,
            "created_by_migration": "schema1",
            "parent_snapshot_ids": [],
            "schema_state": {
                "tables": {
                    table: {
                        "columns": {
                            "id": {"type": "INTEGER", "nullable": False},
                            "account": {"type": "INTEGER", "nullable": False},
                            "amount": {"type": "INTEGER", "nullable": False},
                            "name": {"type": "TEXT", "nullable": False},
                        }
                    }
                    for table in ("legacy_a", "legacy_b")
                }
            },
        }

    monkeypatch.setattr("dbwarden.data.snapshots.resolve_snapshot", snapshot)
    inputs = [
        from_source(
            historical_table(table, snapshot="schema1"),
            identity=["id"],
            map={"id": col("account"), "amount": col("amount"), "name": col("name")},
            priority=index,
        )
        for index, table in enumerate(("legacy_a", "legacy_b"))
    ]
    merged = merge_sources(
        *inputs,
        key=["id"],
        allow_empty=allow_empty,
        values={
            "total": aggregate("sum", col("amount")),
            "name": winner(col("name"))
            if policy == "winner"
            else aggregate(policy, col("name")),
        },
    )

    class Consolidate(DataTransition):
        source = merged
        execution = batching
        targets = (
            into(
                Account,
                map={"id": col("id"), "name": col("name"), "total": col("total")},
            ),
        )

    spec = compile_data(
        DiscoveredData((Account,), (Consolidate,)), database="primary", backend=backend
    )
    operations = plan_data(spec)
    plan = {
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
    return spec, plan


def _populate(connection):
    for table in ("legacy_a", "legacy_b"):
        connection.execute(
            text(
                f"CREATE TABLE {table}(id INTEGER PRIMARY KEY, account INTEGER NOT NULL, amount INTEGER NOT NULL, name TEXT NOT NULL)"
            )
        )
    connection.execute(
        text(
            "CREATE TABLE accounts(id INTEGER PRIMARY KEY, total INTEGER NOT NULL, name TEXT NOT NULL)"
        )
    )
    connection.execute(
        text(
            "INSERT INTO legacy_a VALUES (1, 10, 2, 'first'), (2, 10, 3, 'second'), (3, 20, 5, 'twenty')"
        )
    )
    connection.execute(
        text("INSERT INTO legacy_b VALUES (1, 10, 7, 'other'), (2, 30, 11, 'thirty')")
    )
    connection.commit()


def test_many_source_merge_has_aggregate_winner_provenance_and_rollback(monkeypatch):
    spec, plan = _compile(monkeypatch)
    assert (
        spec["transitions"][0]["identity"]["relationship_cardinality"] == "many_to_one"
    )
    assert plan == _compile(monkeypatch)[1]
    with create_engine("sqlite://").connect() as connection:
        _populate(connection)
        execute_data_plan(connection, plan, migration_id="merge")
        assert connection.execute(text("SELECT * FROM accounts ORDER BY id")).all() == [
            (10, 12, "first"),
            (20, 5, "twenty"),
            (30, 11, "thirty"),
        ]
        assert (
            check_convergence(
                connection, spec, plans=[{**plan, "migration_id": "merge"}]
            )
            == []
        )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM _dbwarden_data_edges")
            ).scalar_one()
            == 8
        )
        connection.commit()
        execute_data_plan(connection, plan, migration_id="merge", direction="rollback")
        assert (
            connection.execute(text("SELECT COUNT(*) FROM accounts")).scalar_one() == 0
        )
        assert (
            connection.execute(text("SELECT COUNT(*) FROM legacy_a")).scalar_one() == 3
        )
        assert (
            connection.execute(text("SELECT COUNT(*) FROM legacy_b")).scalar_one() == 2
        )


def test_merge_require_equal_conflict_rolls_back_before_retirement(monkeypatch):
    _, plan = _compile(monkeypatch, policy="require_equal")
    with create_engine("sqlite://").connect() as connection:
        _populate(connection)
        with pytest.raises(ValueError, match="require_equal"):
            execute_data_plan(connection, plan, migration_id="merge")
        assert (
            connection.execute(text("SELECT COUNT(*) FROM accounts")).scalar_one() == 0
        )
        assert (
            connection.execute(text("SELECT COUNT(*) FROM legacy_a")).scalar_one() == 3
        )


def test_merge_provenance_tamper_blocks_rollback(monkeypatch):
    _, plan = _compile(monkeypatch)
    with create_engine("sqlite://").connect() as connection:
        _populate(connection)
        execute_data_plan(connection, plan, migration_id="merge")
        ledger = next(
            step["identity_edges"]["ledger_table"]
            for step in plan["data_execution"]["upgrade"]
            if step.get("identity_edges", {})
            .get("ledger_table", "")
            .startswith("_dbwarden_merge_edges_")
        )
        connection.execute(text(f'UPDATE "{ledger}" SET s_0 = 999 WHERE s_0 = 1'))
        connection.commit()
        with pytest.raises(ValueError, match="identity"):
            execute_data_plan(
                connection, plan, migration_id="merge", direction="rollback"
            )
        assert (
            connection.execute(text("SELECT COUNT(*) FROM accounts")).scalar_one() == 3
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE {table} SET amount=999 WHERE id=1",
        "INSERT INTO {table} VALUES(99,99,99,'unexpected')",
    ],
)
def test_retained_merge_source_change_blocks_rollback(monkeypatch, mutation):
    _, plan = _compile(monkeypatch)
    with create_engine("sqlite://").connect() as connection:
        _populate(connection)
        execute_data_plan(connection, plan, migration_id="merge")
        source = next(
            step["identity_edges"]["retained_source"]["table"]["table"]
            for step in plan["data_execution"]["upgrade"]
            if step.get("identity_edges", {}).get("retained_source")
        )
        connection.execute(text(mutation.format(table='"' + source + '"')))
        connection.commit()
        with pytest.raises(ValueError, match="Retained merge source"):
            execute_data_plan(
                connection, plan, migration_id="merge", direction="rollback"
            )
        assert (
            connection.execute(text("SELECT COUNT(*) FROM accounts")).scalar_one() == 3
        )


def test_merge_requires_explicit_field_policy_and_distinct_priorities():
    source = from_source(
        historical_table("legacy", snapshot="s1"),
        identity=["id"],
        map={"id": col("id")},
        priority=1,
    )
    with pytest.raises(ValueError, match="aggregate"):
        merge_sources(source, source, key=["id"], values={"value": col("id")})
    with pytest.raises(ValueError, match="priorities"):
        merge_sources(source, source, key=["id"], values={"value": winner(col("id"))})


@pytest.mark.parametrize("allowed", [False, True])
def test_empty_merge_requires_explicit_policy(monkeypatch, allowed):
    _, plan = _compile(monkeypatch, allow_empty=allowed)
    with create_engine("sqlite://").connect() as connection:
        _populate(connection)
        for table in ("legacy_a", "legacy_b"):
            connection.execute(text(f"DELETE FROM {table}"))
        connection.commit()
        if allowed:
            execute_data_plan(connection, plan, migration_id="merge")
            connection.commit()
            execute_data_plan(
                connection, plan, migration_id="merge", direction="rollback"
            )
        else:
            with pytest.raises(ValueError, match="inputs are empty"):
                execute_data_plan(connection, plan, migration_id="merge")
        assert (
            connection.execute(text("SELECT COUNT(*) FROM accounts")).scalar_one() == 0
        )
        assert (
            connection.execute(text("SELECT COUNT(*) FROM legacy_a")).scalar_one() == 0
        )


def test_native_postgresql_merge_and_batch_receipt(monkeypatch):
    from dbwarden.data import batch

    url = os.environ.get("DBWARDEN_DATA_TEST_POSTGRES")
    if not url:
        pytest.skip(
            "Set DBWARDEN_DATA_TEST_POSTGRES to an isolated PostgreSQL database"
        )
    schema = "merge_audit_" + uuid.uuid4().hex
    _, plan = _compile(
        monkeypatch,
        backend="postgresql",
        batching=batch(size=2, key=["id"], statement_timeout=10.0, lock_timeout=2.0),
    )
    engine = create_engine(url)
    with engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        connection.exec_driver_sql(f'SET search_path TO "{schema}"')
        connection.commit()
        try:
            _populate(connection)
            execute_data_plan(connection, plan, migration_id="merge")
            assert connection.execute(
                text("SELECT total FROM accounts ORDER BY id")
            ).scalars().all() == [12, 5, 11]
            connection.commit()
            execute_data_plan(
                connection, plan, migration_id="merge", direction="rollback"
            )
            assert (
                connection.execute(text("SELECT COUNT(*) FROM legacy_a")).scalar_one()
                == 3
            )
        finally:
            connection.rollback()
            connection.exec_driver_sql("SET search_path TO public")
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
            connection.commit()
    engine.dispose()
