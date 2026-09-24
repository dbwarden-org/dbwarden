import pytest
from sqlalchemy import create_engine, text

from dbwarden.data import batch
from dbwarden.data.batches import execute_statement, execution_spec
from dbwarden.data.execution import execute_data_plan


def _step(size=2):
    return {
        "operation_id": "update",
        "sql": ["UPDATE items SET value = id * 2 WHERE value IS NULL"],
        "guards": [],
        "batches": {
            "0": {
                "source": "items",
                "alias": "items",
                "keys": ["id"],
                "policy": execution_spec(batch(size=size, key=["id"])),
            }
        },
    }


def test_keyset_batches_preserve_full_write_and_no_offset():
    with create_engine("sqlite://").connect() as connection:
        connection.execute(
            text("CREATE TABLE items (id INTEGER PRIMARY KEY, value INTEGER)")
        )
        connection.execute(text("INSERT INTO items(id) VALUES (1),(3),(7),(9),(14)"))
        connection.commit()
        commands = []
        step = _step()
        result = execute_statement(
            connection, step["sql"][0], step=step, index=0, before=commands.append
        )
        assert result.rowcount == 5
        assert len(commands) == 3
        assert all("OFFSET" not in sql for sql in commands)
        assert connection.execute(
            text("SELECT value FROM items ORDER BY id")
        ).scalars().all() == [2, 6, 14, 18, 28]
        connection.rollback()
        assert (
            connection.execute(
                text("SELECT count(*) FROM items WHERE value IS NOT NULL")
            ).scalar_one()
            == 0
        )


def test_mid_batch_failure_rolls_back_all_chunks_and_can_retry():
    with create_engine("sqlite://").connect() as connection:
        connection.execute(
            text("CREATE TABLE items (id INTEGER PRIMARY KEY, value INTEGER)")
        )
        connection.execute(text("INSERT INTO items(id) VALUES (1),(2),(3),(4),(5)"))
        connection.commit()
        plan = {
            "data_spec": {},
            "data_bundle": {},
            "data_execution": {"upgrade": [_step()], "rollback": []},
        }
        writes = []

        def fail(sql):
            writes.append(sql)
            if len(writes) == 2:
                raise RuntimeError("injected batch interruption")

        with pytest.raises(RuntimeError, match="injected"):
            execute_data_plan(
                connection, plan, migration_id="batch", after_statement=fail
            )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM items WHERE value IS NOT NULL")
            ).scalar_one()
            == 0
        )
        connection.commit()
        assert (
            execute_data_plan(connection, plan, migration_id="batch")["status"]
            == "APPLIED_SUCCESS"
        )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM items WHERE value IS NOT NULL")
            ).scalar_one()
            == 5
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"size": True},
        {"size": 0},
        {"key": []},
        {"max_duration": float("inf")},
        {"lock_timeout": -1},
    ],
)
def test_batch_rejects_invalid_limits(changes):
    with pytest.raises(ValueError):
        batch(**{"size": 10, "key": ["id"], **changes})


def test_duration_budget_spans_steps_and_resets_on_retry(monkeypatch):
    from dbwarden.data import batches

    clock = [0.0]
    monkeypatch.setattr(batches.time, "monotonic", lambda: clock[0])
    step = {"execution_limits": {"id": "declaration", "max_duration": 1.0}}
    with batches.execution_attempt():
        batches.check_duration(step)
        clock[0] = 0.8
        batches.check_duration(step)
        clock[0] = 1.1
        with pytest.raises(TimeoutError, match="operation duration"):
            batches.check_duration(step)
    with batches.execution_attempt():
        batches.check_duration(step)


def test_composite_string_batch_keys_are_bound_and_visit_every_row():
    with create_engine("sqlite://").connect() as connection:
        connection.execute(
            text(
                "CREATE TABLE items (tenant TEXT, id INTEGER, value INTEGER, PRIMARY KEY(tenant,id))"
            )
        )
        rows = [
            {"tenant": tenant, "id": number}
            for tenant in ("secret'--", "z")
            for number in (1, 3, 9)
        ]
        connection.execute(
            text("INSERT INTO items(tenant,id) VALUES (:tenant,:id)"), rows
        )
        step = _step()
        step["batches"]["0"].update(
            keys=["tenant", "id"],
            policy=execution_spec(batch(size=2, key=["tenant", "id"])),
        )
        commands = []
        assert (
            execute_statement(
                connection, step["sql"][0], step=step, index=0, before=commands.append
            ).rowcount
            == 6
        )
        assert all("secret" not in command for command in commands)
        assert (
            connection.execute(
                text("SELECT count(*) FROM items WHERE value = id * 2")
            ).scalar_one()
            == 6
        )


def test_sqlite_timeout_interrupts_write_and_restores_connection():
    from sqlalchemy.exc import OperationalError

    from dbwarden.data.batches import _statement_limits

    with create_engine("sqlite://").connect() as connection:
        original = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()
        with (
            pytest.raises(OperationalError, match="interrupted"),
            _statement_limits(
                connection, {"statement_timeout": 0.001, "lock_timeout": 0.1}
            ),
        ):
            connection.exec_driver_sql(
                "WITH RECURSIVE numbers(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM numbers WHERE x<10000000) SELECT sum(x) FROM numbers"
            )
        assert (
            connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == original
        )
        assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1
