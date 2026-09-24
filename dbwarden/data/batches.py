from __future__ import annotations

import math
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from types import SimpleNamespace

import sqlglot
from sqlglot import exp

from .declarations import column_names
from .sql import statement_text as text

_ATTEMPT_STARTS = ContextVar("data_execution_starts", default=None)


@contextmanager
def execution_attempt():
    token = _ATTEMPT_STARTS.set({})
    try:
        yield
    finally:
        _ATTEMPT_STARTS.reset(token)


def check_duration(step):
    from .planning import decode_value

    limits = step.get("execution_limits", {})
    maximum = decode_value(limits.get("max_duration"))
    starts = _ATTEMPT_STARTS.get()
    if maximum is None or starts is None:
        return
    started = starts.setdefault(limits["id"], time.monotonic())
    if time.monotonic() - started >= maximum:
        raise TimeoutError("Data operation duration limit exceeded")


@dataclass(frozen=True)
class Batch:
    size: int
    key: tuple[str, ...]
    max_duration: float | None = None
    statement_timeout: float | None = None
    lock_timeout: float | None = None
    max_replication_lag: float | None = None


def batch(
    *,
    size,
    key,
    max_duration=None,
    statement_timeout=None,
    lock_timeout=None,
    max_replication_lag=None,
):
    if type(size) is not int or size <= 0:
        raise ValueError("Batch size must be a positive integer")
    keys = column_names(key)
    if not keys:
        raise ValueError("Batching requires a stable non-null unique key")
    limits = (max_duration, statement_timeout, lock_timeout, max_replication_lag)
    if any(
        value is not None
        and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0)
        for value in limits
    ):
        raise ValueError("Batch execution limits must be finite positive seconds")
    return Batch(size, keys, *limits)


def execution_spec(policy):
    if policy is None:
        return {}
    if not isinstance(policy, Batch):
        raise TypeError("execution must be batch(...)")
    value = asdict(batch(**asdict(policy)))
    value["key"] = list(value["key"])
    return value


def _policy(value):
    from .planning import decode_value

    return batch(
        **{key: decode_value(value.get(key)) for key in Batch.__dataclass_fields__}
    )


def attach_execution(upgrade, rollback, item, backend):
    policy = item.get("execution", {})
    if "size" not in policy:
        return
    _policy(policy)
    if backend == "clickhouse":
        raise ValueError("ClickHouse keyset batches require atomic mutation receipts")
    if backend == "mysql" and policy.get("statement_timeout") is not None:
        raise ValueError(
            "MySQL cannot enforce statement_timeout for batched DML; use max_duration"
        )
    if backend == "sqlite" and policy.get("max_replication_lag") is not None:
        raise ValueError("SQLite has no replication-lag measurement")
    dialect = {"postgresql": "postgres", "mariadb": "mysql"}.get(backend, backend)
    target = item.get("target_table")
    expected_targets = (
        [target] if target else [entry["target_table"] for entry in item["targets"]]
    )
    limits = {"id": item["declaration_id"], "max_duration": policy.get("max_duration")}
    for step in [*upgrade, *rollback]:
        step["execution_limits"] = limits
    for step in upgrade:
        batches = {}
        for index, statement in enumerate(step["sql"]):
            tree = sqlglot.parse_one(statement, read=dialect)
            table = (
                tree.this.this
                if isinstance(tree, exp.Insert) and isinstance(tree.this, exp.Schema)
                else tree.this
            )
            if not isinstance(table, exp.Table) or not any(
                table.name == ref["table"] and (table.db or None) == ref.get("schema")
                for ref in expected_targets
            ):
                continue
            if isinstance(tree, exp.Insert) and isinstance(tree.expression, exp.Select):
                source = tree.expression.args.get("from_")
                source = source.this if source is not None else None
                edge = step.get("identity_edges")
                if not isinstance(source, exp.Table) or edge is None:
                    continue
                keys = edge["source_columns"][: len(policy["key"])]
                alias = source.alias_or_name
            elif isinstance(tree, exp.Update):
                source, keys, alias = table, policy["key"], table.alias_or_name
            else:
                continue
            batches[str(index)] = {
                "source": source.sql(dialect=dialect),
                "alias": alias,
                "keys": list(keys),
                "policy": policy,
                "execution_limits": limits,
            }
        if batches:
            step["batches"] = batches


def execute_statement(connection, statement, *, step, index, before=None, after=None):
    check_duration(step)
    descriptor = step.get("batches", {}).get(str(index))
    if descriptor is None:
        if before is not None:
            before(statement)
        result = connection.execute(text(statement))
        if after is not None:
            after(statement)
        check_duration(step)
        return result
    with _statement_limits(connection, asdict(_policy(descriptor["policy"]))):
        return _execute_batches(connection, statement, descriptor, before, after)


def _execute_batches(connection, statement, descriptor, before, after):
    from .execution import _require_transactional_mysql_tables

    backend = connection.dialect.name
    dialect = {"postgresql": "postgres", "mariadb": "mysql"}.get(backend, backend)
    policy = _policy(descriptor["policy"])
    tree = sqlglot.parse_one(statement, read=dialect)
    selection = tree.expression if isinstance(tree, exp.Insert) else tree
    source = (
        sqlglot.parse_one("SELECT * FROM " + descriptor["source"], read=dialect)
        .args["from_"]
        .this
    )
    columns = [
        exp.column(key, table=descriptor["alias"], quoted=True)
        for key in descriptor["keys"]
    ]
    base = exp.select(*[column.copy() for column in columns]).from_(source.copy())
    if selection.args.get("where") is not None:
        base.set("where", selection.args["where"].copy())
    if backend in {"mysql", "mariadb"}:
        target = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
        _require_transactional_mysql_tables(
            connection, [(table.name, table.db or None) for table in (target, source)]
        )
        connection.execute(text(base.sql(dialect=dialect) + " FOR UPDATE")).close()
    started, lower, affected = time.monotonic(), None, 0
    while True:
        check_duration(descriptor)
        if (
            policy.max_duration is not None
            and time.monotonic() - started >= policy.max_duration
        ):
            raise TimeoutError(
                "Data batch duration limit exceeded; current statement must roll back"
            )
        if policy.max_replication_lag is not None:
            _check_replication_lag(connection, policy.max_replication_lag)
        probe = base.copy()
        parameters = {}
        if lower is not None:
            probe = probe.where(_range(columns, lower, ">", parameters))
        probe = probe.order_by(*[column.copy() for column in columns]).limit(
            policy.size
        )
        rows = connection.execute(text(probe.sql(dialect=dialect)), parameters).all()
        if not rows:
            break
        if any(value is None for row in rows for value in row):
            raise ValueError("Data batch key contains NULL")
        high = tuple(rows[-1])
        if lower is not None and high == lower:
            raise ValueError("Data batch key did not advance")
        limited = selection.copy().where(_range(columns, high, "<=", parameters))
        if lower is not None:
            limited = limited.where(_range(columns, lower, ">", parameters))
        command = tree.copy()
        if isinstance(tree, exp.Insert):
            command.set("expression", limited)
        else:
            command = limited
        sql = command.sql(dialect=dialect)
        if before is not None:
            before(sql)
        result = connection.execute(text(sql), parameters)
        if result.rowcount < 0:
            raise ValueError("Backend did not report batched affected-row count")
        affected += result.rowcount
        if after is not None:
            after(sql)
        lower = high
    return SimpleNamespace(rowcount=affected)


@contextmanager
def _statement_limits(connection, policy):
    backend = connection.dialect.name
    statement, lock = policy.get("statement_timeout"), policy.get("lock_timeout")
    restored = []
    driver = None
    failed = False
    try:
        if backend == "postgresql":
            for name, value in (
                ("statement_timeout", statement),
                ("lock_timeout", lock),
            ):
                if value is not None:
                    old = connection.execute(
                        text("SELECT current_setting(:name)"), {"name": name}
                    ).scalar_one()
                    connection.execute(
                        text("SELECT set_config(:name, :value, true)"),
                        {"name": name, "value": str(max(1, int(value * 1000)))},
                    )
                    restored.append((name, old))
        elif backend == "sqlite":
            if lock is not None:
                restored.append(
                    (
                        "busy_timeout",
                        connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one(),
                    )
                )
                connection.exec_driver_sql(
                    f"PRAGMA busy_timeout = {max(1, int(lock * 1000))}"
                )
            if statement is not None:
                driver = connection.connection.driver_connection
                deadline = time.monotonic() + statement
                driver.set_progress_handler(
                    lambda: int(time.monotonic() >= deadline), 1000
                )
        elif backend in {"mysql", "mariadb"}:
            maria = backend == "mariadb" or getattr(
                connection.dialect, "is_mariadb", False
            )
            if statement is not None and not maria:
                raise ValueError(
                    "MySQL cannot enforce statement_timeout for batched DML"
                )
            for name, value in (
                ("innodb_lock_wait_timeout", None if lock is None else math.ceil(lock)),
                ("max_statement_time", statement),
            ):
                if value is not None:
                    old = connection.exec_driver_sql(
                        f"SELECT @@SESSION.{name}"
                    ).scalar_one()
                    connection.execute(
                        text(f"SET SESSION {name} = :value"), {"value": value}
                    )
                    restored.append((name, old))
        yield
    except BaseException:
        failed = True
        raise
    finally:
        if driver is not None:
            driver.set_progress_handler(None, 0)
        for name, old in reversed(restored):
            if backend == "sqlite":
                connection.exec_driver_sql(f"PRAGMA busy_timeout = {int(old)}")
            elif backend == "postgresql":
                if not failed:
                    connection.execute(
                        text("SELECT set_config(:name, :value, true)"),
                        {"name": name, "value": old},
                    )
            else:
                connection.execute(text(f"SET SESSION {name} = :value"), {"value": old})


def _range(columns, values, operator, parameters):
    left = exp.Tuple(expressions=[column.copy() for column in columns])
    names = [
        f"_dbw_batch_{'low' if operator == '>' else 'high'}_{index}"
        for index in range(len(values))
    ]
    parameters.update(zip(names, values))
    right = exp.Tuple(expressions=[exp.Var(this=":" + name) for name in names])
    if len(columns) == 1:
        left, right = left.expressions[0], right.expressions[0]
    return (exp.GT if operator == ">" else exp.LTE)(this=left, expression=right)


def _check_replication_lag(connection, maximum):
    backend = connection.dialect.name
    if backend == "postgresql":
        rows = connection.execute(
            text("SELECT EXTRACT(EPOCH FROM replay_lag) FROM pg_stat_replication")
        ).all()
        values = [row[0] for row in rows]
    elif backend in {"mysql", "mariadb"}:
        maria = backend == "mariadb" or getattr(connection.dialect, "is_mariadb", False)
        rows = (
            connection.exec_driver_sql(
                "SHOW SLAVE STATUS" if maria else "SHOW REPLICA STATUS"
            )
            .mappings()
            .all()
        )
        values = [
            row.get("Seconds_Behind_Source", row.get("Seconds_Behind_Master"))
            for row in rows
        ]
    else:
        raise ValueError("Backend does not support replication-lag guards")
    if any(value is None for value in values):
        raise ValueError("Replication lag is unknown; batch execution refused")
    if any(float(value) > maximum for value in values):
        raise ValueError("Replication lag exceeds the declared batch limit")
