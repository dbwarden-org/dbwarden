from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from .batches import check_duration, execute_statement, execution_attempt
from .ir import canonical_bytes
from .sql import statement_text as text

_JOURNAL = "_dbwarden_data_runs"
_CHECKPOINTS = "_dbwarden_data_operations"
_EDGES = "_dbwarden_data_edges"
_KEYS = "_dbwarden_data_keys"
_EVENTS = "_dbwarden_data_events"
_SUCCESS = "APPLIED_SUCCESS"
_RETRYABLE = "FAILED_RETRYABLE"
_ROLLBACK = "ROLLED_BACK"
_UNKNOWN = "UNKNOWN_REQUIRES_RECONCILIATION"
_CH_LAST_VERSION = 0
_CH_RUN_FIELDS = (
    "migration_id",
    "epoch",
    "checksum",
    "status",
    "run_id",
    "direction",
    "last_operation",
    "error",
    "baseline",
    "started_at",
    "finished_at",
    "version",
    "previous_record_checksum",
    "record_checksum",
)
_CH_CHECKPOINT_FIELDS = (
    "migration_id",
    "epoch",
    "run_id",
    "direction",
    "operation_id",
    "statement_index",
    "statement_checksum",
    "status",
    "started_at",
    "finished_at",
    "version",
    "previous_record_checksum",
    "record_checksum",
)
_CH_EVENT_FIELDS = (
    "event_id",
    "migration_id",
    "epoch",
    "run_id",
    "event_type",
    "status",
    "operation_id",
    "checksum",
    "details",
    "created_at",
)


class DataExecutionError(RuntimeError):
    """A database execution failure with SQL text and values removed."""


class DataOwnershipError(ValueError):
    """The database did not prove that this migration inserted its claimed rows."""


@execution_attempt()
def execute_data_plan(
    connection,
    plan: dict[str, Any],
    *,
    migration_id: str,
    direction: str = "upgrade",
    record_success=None,
    baseline: bool = False,
    baseline_reason: str | None = None,
    reapply: bool = False,
    before_statement=None,
    after_statement=None,
) -> dict[str, Any]:
    try:
        return _execute_data_plan(
            connection,
            plan,
            migration_id=migration_id,
            direction=direction,
            record_success=record_success,
            baseline=baseline,
            baseline_reason=baseline_reason,
            reapply=reapply,
            before_statement=before_statement,
            after_statement=after_statement,
        )
    except DBAPIError as exc:
        raise _sanitized_database_error(exc, None) from None


def _execute_data_plan(
    connection,
    plan: dict[str, Any],
    *,
    migration_id: str,
    direction: str = "upgrade",
    record_success=None,
    baseline: bool = False,
    baseline_reason: str | None = None,
    reapply: bool = False,
    before_statement=None,
    after_statement=None,
) -> dict[str, Any]:
    """Apply one data-plan direction under the caller's migration lock."""
    if direction not in {"upgrade", "rollback"}:
        raise ValueError("direction must be 'upgrade' or 'rollback'")
    if reapply and direction != "upgrade":
        raise ValueError("reapply is only valid for the upgrade direction")
    _verify_plan_binding(connection, plan, migration_id)
    mode = _backend_mode(connection)
    _reject_plain_ignore(plan.get("data_spec"))
    _verify_backend_settings(connection, plan.get("data_spec"))
    if mode == "clickhouse":
        return _execute_clickhouse(
            connection,
            plan,
            migration_id=migration_id,
            direction=direction,
            record_success=record_success,
            baseline=baseline,
            baseline_reason=baseline_reason,
            reapply=reapply,
            before_statement=before_statement,
            after_statement=after_statement,
        )
    if mode == "nontransactional":
        return _execute_nontransactional(
            connection,
            plan,
            migration_id=migration_id,
            direction=direction,
            record_success=record_success,
            baseline=baseline,
            baseline_reason=baseline_reason,
            reapply=reapply,
            before_statement=before_statement,
            after_statement=after_statement,
        )
    checksum = _plan_checksum(plan)
    steps = _steps(plan, direction)
    if direction == "rollback" and _is_irreversible(plan):
        raise ValueError("Data rollback is irreversible and cannot be executed")

    owned_transaction = not connection.in_transaction()
    transaction = connection.begin() if owned_transaction else None
    if (
        connection.dialect.name == "sqlite"
        and not connection.connection.driver_connection.in_transaction
    ):
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    run_id = str(uuid.uuid4())
    run_started = False
    current_operation = None
    try:
        if not _has_table(connection, _JOURNAL):
            _create_journal(connection)
        if not _has_table(connection, _EVENTS):
            _create_events(connection)
        existing = _latest_run(connection, migration_id)
        epoch = _next_epoch(existing, checksum, migration_id, reapply=reapply)
        if direction == "upgrade" and existing and existing["status"] == _SUCCESS:
            if transaction is not None:
                transaction.rollback()
            return {
                "status": "already_applied",
                "migration_id": migration_id,
                "epoch": existing["epoch"],
                "checksum": checksum,
                "baseline": bool(existing["baseline"]),
            }
        previous_epoch = (
            int(existing["epoch"])
            if direction == "upgrade" and existing and existing["status"] == _ROLLBACK
            else None
        )
        if direction == "rollback":
            if not existing or existing["status"] != _SUCCESS or existing["baseline"]:
                raise ValueError(
                    "Rollback requires a successfully executed data migration"
                )
            epoch = existing["epoch"]
            _set_run_direction(connection, migration_id, epoch, direction)
        else:
            _insert_run(
                connection, migration_id, epoch, checksum, run_id, direction, baseline
            )
            if previous_epoch is not None:
                _append_reapply_event(connection, migration_id, epoch, previous_epoch)
        run_started = True
        _prepare_identity_edges(connection, steps, clickhouse=False)
        if baseline:
            if record_success is not None:
                record_success()
            _finish_run(
                connection,
                migration_id,
                epoch,
                _SUCCESS,
                baseline=True,
            )
            _append_baseline_event(
                connection,
                migration_id,
                epoch,
                _baseline_details(plan, baseline_reason, direction),
            )
            if transaction is not None:
                transaction.commit()
            return _result(
                _SUCCESS, migration_id, epoch, checksum, run_id, baseline=True
            )

        _lock_postgresql_tables(connection, plan)
        _verify_rollback_edges(connection, plan, migration_id, epoch, direction)
        for step in steps:
            check_duration(step)
            operation_id = _operation_id(step)
            current_operation = operation_id
            _set_last_operation(connection, migration_id, epoch, operation_id)
            if direction == "rollback" and any(
                statement.lstrip().upper().startswith("DROP TABLE")
                for statement in step.get("sql", [])
            ):
                _check_sqlite_drop_dependencies(connection, step.get("lock_tables", []))
            _run_guards(connection, step.get("guards", []), "before")
            for index, statement in enumerate(step.get("sql", [])):
                execute_statement(
                    connection,
                    _write_sql(statement),
                    step=step,
                    index=index,
                    before=before_statement,
                    after=after_statement,
                )
            _run_guards(connection, step.get("guards", []), "after")
            _record_identity_edges(
                connection,
                step,
                migration_id,
                epoch,
                run_id,
                operation_id,
                clickhouse=False,
            )

        if record_success is not None:
            record_success()
        final_status = _ROLLBACK if direction == "rollback" else _SUCCESS
        _finish_run(connection, migration_id, epoch, final_status, baseline=False)
        if transaction is not None:
            transaction.commit()
        return _result(final_status, migration_id, epoch, checksum, run_id)
    except Exception as exc:
        if connection.in_transaction():
            connection.rollback()
        error = _failure_text(exc, current_operation)
        if run_started and direction == "upgrade":
            _persist_failure(
                connection,
                migration_id,
                checksum,
                run_id,
                error,
                baseline,
                reapply=reapply,
            )
        if isinstance(exc, DBAPIError):
            raise _sanitized_database_error(exc, current_operation) from None
        raise


def reconcile_data_plan(
    connection,
    migration_id: str,
    *,
    apply: bool = False,
    decision: str | None = None,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return _reconcile_data_plan(
            connection,
            migration_id,
            apply=apply,
            decision=decision,
            plan=plan,
        )
    except DBAPIError as exc:
        raise _sanitized_database_error(exc, None) from None


def _reconcile_data_plan(
    connection,
    migration_id: str,
    *,
    apply: bool = False,
    decision: str | None = None,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inspect a data execution record; mutate it only for explicit safe decisions."""
    if plan is not None:
        _verify_plan_binding(connection, plan, migration_id)
        _verify_backend_settings(connection, plan.get("data_spec"))
    mode = _backend_mode(connection)
    if mode == "clickhouse":
        return _reconcile_clickhouse(
            connection,
            migration_id,
            plan=plan,
            apply=apply,
            decision=decision,
        )
    if mode == "nontransactional":
        return _reconcile_nontransactional(
            connection,
            migration_id,
            plan=plan,
            apply=apply,
            decision=decision,
        )
    if not inspect(connection).has_table(_JOURNAL):
        return {
            "migration_id": migration_id,
            "status": "NOT_STARTED",
            "retry_allowed": True,
        }
    owned_transaction = not connection.in_transaction()
    transaction = connection.begin() if owned_transaction else None
    try:
        run = _latest_run(connection, migration_id)
        if run is None:
            result = {
                "migration_id": migration_id,
                "status": "NOT_STARTED",
                "retry_allowed": True,
            }
        else:
            result = {
                "migration_id": migration_id,
                **run,
                "retry_allowed": run["status"] == _RETRYABLE
                and _has_rollback_proof(run),
            }
            if apply and decision == "abandon" and run["status"] == "APPLYING":
                connection.execute(
                    text(
                        f"UPDATE {_JOURNAL} SET status = :status, finished_at = :finished_at WHERE migration_id = :migration_id AND epoch = :epoch"
                    ),
                    {
                        "status": "ABANDONED",
                        "finished_at": _now(),
                        "migration_id": migration_id,
                        "epoch": run["epoch"],
                    },
                )
                result = {**result, "status": "ABANDONED", "retry_allowed": False}
            elif apply and decision == "verified_retry" and result["retry_allowed"]:
                result = {**result, "decision": "verified_retry"}
            elif apply:
                raise ValueError(
                    "Reconciliation requires APPLYING + decision='abandon' or FAILED_RETRYABLE rollback proof + decision='verified_retry'"
                )
        if transaction is not None:
            transaction.commit()
        return result
    except Exception:
        if transaction is not None:
            transaction.rollback()
        raise


def _backend_mode(connection) -> str:
    dialect = connection.dialect.name
    if dialect in {"clickhouse", "clickhousedb"}:
        return "clickhouse"
    if dialect in {"mysql", "mariadb"}:
        return "nontransactional"
    if dialect not in {"sqlite", "postgresql"}:
        raise ValueError(f"Data-plan execution is unsupported for backend '{dialect}'")
    isolation = connection.get_isolation_level()
    if (
        isolation and isolation.upper() == "AUTOCOMMIT"
    ) or connection.get_execution_options().get("isolation_level") == "AUTOCOMMIT":
        raise ValueError(
            "Data-plan execution requires a transactional connection; autocommit is unsupported"
        )
    return "transactional"


def _plan_checksum(plan: dict[str, Any]) -> str:
    if not isinstance(plan.get("data_execution"), dict) or not isinstance(
        plan.get("data_spec"), dict
    ):
        raise TypeError("Data plan requires data_execution and canonical data_spec")
    bundle = plan.get("data_bundle")
    if not isinstance(bundle, dict):
        raise TypeError("Data plan requires a data_bundle integrity manifest")
    payload = {
        "data_bundle": bundle,
        "data_execution": plan["data_execution"],
        "data_spec": plan["data_spec"],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _steps(plan: dict[str, Any], direction: str) -> list[dict[str, Any]]:
    steps = plan["data_execution"].get(direction)
    if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
        raise ValueError(f"data_execution.{direction} must be a list of operations")
    execution_ids = [_operation_id(step) for step in steps]
    if len(execution_ids) != len(set(execution_ids)):
        raise ValueError(f"data_execution.{direction} requires unique step_id values")
    return steps


def _is_irreversible(plan: dict[str, Any]) -> bool:
    if "upgrade_ops" in plan:
        return any(op.get("__irreversible") for op in plan["upgrade_ops"])

    def visit(value: Any) -> bool:
        if isinstance(value, dict):
            if value.get("policy") == "irreversible":
                return True
            return any(
                key in {"rollback", "rollback_policy"}
                and item == "irreversible"
                or visit(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(visit(item) for item in value)
        return False

    return visit(plan["data_spec"])


def _operation_id(step: dict[str, Any]) -> str:
    parent = step.get("operation_id")
    if not isinstance(parent, str) or not parent:
        raise ValueError("Data execution operation requires a non-empty operation_id")
    value = step.get("step_id", parent)
    if not isinstance(value, str) or not value:
        raise ValueError("Data execution step requires a non-empty step_id")
    sql = step.get("sql", [])
    if not isinstance(sql, list) or not all(
        isinstance(item, str) and item.strip() for item in sql
    ):
        raise ValueError(f"Data operation '{value}' requires SQL strings")
    guards = step.get("guards", [])
    if not isinstance(guards, list) or any(
        not isinstance(item, dict) for item in guards
    ):
        raise ValueError(f"Data operation '{value}' guards must be objects")
    proofs = step.get("insert_proofs", [])
    if not isinstance(proofs, list):
        raise TypeError("Insert ownership proofs must be a list")
    indexes = set()
    for proof in proofs:
        index = proof.get("statement_index") if isinstance(proof, dict) else None
        if type(index) is not int or not 0 <= index < len(sql) or index in indexes:
            raise ValueError(
                "Insert ownership proof requires a distinct statement index"
            )
        indexes.add(index)
        if ("expected" in proof) == ("query" in proof):
            raise ValueError("Insert ownership proof requires expected or query")
        if "expected" in proof and (
            type(proof["expected"]) is not int or proof["expected"] < 0
        ):
            raise ValueError("Insert ownership count must be a nonnegative integer")
    return value


def _insert_proof(step, index):
    return next(
        (
            proof
            for proof in step.get("insert_proofs", [])
            if proof["statement_index"] == index
        ),
        None,
    )


def _checkpoint_checksum(step, direction, operation_id, index):
    statements = step.get("sql", [])
    if index == -1 and not statements:
        return _statement_checksum(direction, operation_id, index, "<guard-only-step>")
    if type(index) is not int or not 0 <= index < len(statements):
        return None
    return _statement_checksum(direction, operation_id, index, statements[index])


def _validate_step_checkpoints(step, rows, direction, operation_id):
    expected = {-1} if not step.get("sql", []) else set(range(len(step["sql"])))
    indexes = set()
    for row in rows:
        index = row["statement_index"]
        checksum = _checkpoint_checksum(step, direction, operation_id, index)
        if (
            index in indexes
            or index not in expected
            or row["statement_checksum"] != checksum
        ):
            raise ValueError(
                "Stored operation checkpoint does not match the reviewed plan"
            )
        indexes.add(index)
    return indexes == expected and all(row["status"] == "DONE" for row in rows)


def _expected_inserts(connection, proof, statement):
    import sqlglot
    from sqlglot import exp

    target = sqlglot.parse_one(statement, read="mysql")
    if not isinstance(target, exp.Insert):
        raise TypeError("Insert ownership proof requires an INSERT statement")
    table = target.find(exp.Table)
    if table is None:
        raise ValueError("Insert ownership proof requires a target table")
    _require_transactional_mysql_tables(connection, [(table.name, table.db or None)])
    expected = proof.get("expected")
    if "query" in proof:
        expected = connection.execute(text(_read_only_sql(proof["query"]))).scalar_one()
    if type(expected) is not int or expected < 0:
        raise ValueError("Insert ownership count must be a nonnegative integer")
    return expected


def _mysql_lock_queries(connection, step):
    if "step_id" not in step or not step.get("sql"):
        return []
    import sqlglot
    from sqlglot import exp

    queries = []
    tables = []
    for statement in step["sql"]:
        expression = sqlglot.parse_one(statement, read="mysql")
        if not isinstance(expression, (exp.Delete, exp.Insert, exp.Update)):
            return []
        table = expression.this
        if isinstance(table, exp.Schema):
            table = table.this
        if not isinstance(table, exp.Table):
            raise TypeError("Guarded MySQL write requires one target table")
        tables.append((table.name, table.db or None))
        target = table.sql(dialect="mysql")
        where = expression.args.get("where")
        predicate = f" {where.sql(dialect='mysql')}" if where is not None else ""
        queries.append(f"SELECT 1 FROM {target}{predicate} FOR UPDATE")
    _require_transactional_mysql_tables(connection, tables)
    return list(dict.fromkeys(queries))


def _require_transactional_mysql_tables(connection, tables):
    if connection.execute(text("SELECT @@autocommit")).scalar_one():
        raise ValueError("Guarded writes require transactional MySQL DML")
    for name, schema in [*tables, (_CHECKPOINTS, None)]:
        engine = connection.execute(
            text(
                "SELECT ENGINE FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = COALESCE(:schema, DATABASE()) AND TABLE_NAME = :table"
            ),
            {"schema": schema, "table": name},
        ).scalar_one()
        if str(engine).lower() != "innodb":
            raise ValueError(
                "Guarded MySQL writes require InnoDB target and checkpoint tables"
            )


def _verify_rollback_step_edges(connection, plan, step, migration_id, epoch):
    import sqlglot
    from sqlglot import exp

    referenced = {
        table.name
        for statement in step.get("sql", [])
        for table in sqlglot.parse_one(statement, read="mysql").find_all(exp.Table)
    }
    expected = {
        name
        for name in referenced
        if name.startswith(
            (
                "_dbwarden_edges_",
                "_dbwarden_capture_",
                "_dbwarden_archive_",
                "_dbwarden_unmatched_",
                "_dbwarden_merge_edges_",
            )
        )
    }
    matched = set()
    for upgrade_step in _steps(plan, "upgrade"):
        edge = upgrade_step.get("identity_edges")
        if edge and edge.get("ledger_table") in referenced:
            verify_identity_edges(connection, upgrade_step, migration_id, epoch)
            matched.add(edge["ledger_table"])
    if expected != matched:
        raise ValueError("Rollback identity-edge proof is missing for a locked target")


def _run_guards(connection, guards: list[dict[str, Any]], timing: str) -> None:
    for guard in guards:
        if guard.get("timing", "before") != timing:
            continue
        query = guard.get("query")
        probe_id = guard.get("probe_id")
        if not isinstance(probe_id, str) or not probe_id or not isinstance(query, str):
            raise ValueError("Data guard requires probe_id and query")
        value = connection.execute(text(_read_only_sql(query))).scalar_one()
        if not isinstance(value, (int, float)):
            raise TypeError(f"Data guard '{probe_id}' must return one numeric scalar")
        minimum, maximum = guard.get("minimum"), guard.get("maximum")
        if (
            minimum is not None
            and value < minimum
            or maximum is not None
            and value > maximum
        ):
            message = (
                guard.get("message")
                or f"Data guard '{probe_id}' failed: observed {value}"
            )
            raise ValueError(message)


def _execute_nontransactional(
    connection,
    plan,
    *,
    migration_id,
    direction,
    record_success,
    baseline,
    baseline_reason,
    reapply,
    before_statement,
    after_statement,
):
    checksum = _plan_checksum(plan)
    steps = _steps(plan, direction)
    if direction == "rollback" and _is_irreversible(plan):
        raise ValueError("Data rollback is irreversible and cannot be executed")
    if not _has_table(connection, _JOURNAL):
        _create_journal(connection)
    if not _has_table(connection, _CHECKPOINTS):
        _create_checkpoints(connection)
    if not _has_table(connection, _EVENTS):
        _create_events(connection)
    connection.commit()
    existing = _latest_run(connection, migration_id)
    connection.commit()
    if existing and existing["checksum"] != checksum:
        raise ValueError(f"Data plan checksum mismatch for migration '{migration_id}'")
    if direction == "upgrade" and existing and existing["status"] == _SUCCESS:
        return {
            "status": "already_applied",
            "migration_id": migration_id,
            "epoch": existing["epoch"],
            "checksum": checksum,
            "baseline": bool(existing["baseline"]),
        }
    if (
        direction == "upgrade"
        and existing
        and existing["status"] == _ROLLBACK
        and not reapply
    ):
        raise RuntimeError(
            f"Data migration '{migration_id}' was rolled back; explicit reapply is required"
        )
    if existing and existing["status"] in {"ABANDONED", "FAILED_FINAL"}:
        raise RuntimeError(
            f"Data migration '{migration_id}' is terminally {existing['status']}"
        )
    _prepare_identity_edges(connection, steps, clickhouse=False)
    connection.commit()
    run_id = str(uuid.uuid4())
    if direction == "rollback":
        resumable = (
            existing
            and existing["status"] == _RETRYABLE
            and existing["direction"] == direction
            and _has_nontransactional_proof(existing)
        )
        if (
            not existing
            or existing["baseline"]
            or existing["status"] != _SUCCESS
            and not resumable
        ):
            raise ValueError("Rollback requires a successfully executed data migration")
        epoch = int(existing["epoch"])
        _restart_run(connection, migration_id, epoch, run_id, direction, checksum)
    elif (
        existing
        and existing["status"] == _RETRYABLE
        and existing["direction"] == direction
        and _has_nontransactional_proof(existing)
    ):
        epoch = int(existing["epoch"])
        _restart_run(connection, migration_id, epoch, run_id, direction, checksum)
    else:
        if existing and existing["status"] in {"APPLYING", _UNKNOWN, _RETRYABLE}:
            raise RuntimeError(
                f"Data migration '{migration_id}' requires reconciliation before retry"
            )
        epoch = int(existing["epoch"]) + 1 if existing else 1
        _insert_run(
            connection, migration_id, epoch, checksum, run_id, direction, baseline
        )
        if existing and existing["status"] == _ROLLBACK:
            _append_reapply_event(
                connection, migration_id, epoch, int(existing["epoch"])
            )
    connection.commit()
    possible_effect = bool(_checkpoint_rows(connection, migration_id, epoch, direction))
    connection.commit()
    current_operation = None
    try:
        if baseline:
            if record_success is not None:
                record_success()
            _finish_run(connection, migration_id, epoch, _SUCCESS, baseline=True)
            _append_baseline_event(
                connection,
                migration_id,
                epoch,
                _baseline_details(plan, baseline_reason, direction),
            )
            connection.commit()
            return _result(
                _SUCCESS, migration_id, epoch, checksum, run_id, baseline=True
            )
        if any("step_id" not in step for step in steps) or (
            direction == "rollback" and not possible_effect
        ):
            _verify_rollback_edges(connection, plan, migration_id, epoch, direction)
        for step in steps:
            check_duration(step)
            operation_id = _operation_id(step)
            step_rows = _checkpoint_rows(
                connection, migration_id, epoch, direction, operation_id
            )
            connection.commit()
            if _validate_step_checkpoints(step, step_rows, direction, operation_id):
                continue
            if step_rows:
                raise RuntimeError(
                    f"Operation '{operation_id}' has an unresolved checkpoint; "
                    "reconcile before retry"
                )
            current_operation = operation_id
            _set_last_operation(connection, migration_id, epoch, operation_id)
            connection.commit()
            for reference in step.get("verify_edges", []):
                proof = next(
                    entry
                    for entry in _steps(plan, "upgrade")
                    if entry.get("identity_edges", {}).get("definition_id")
                    == reference["definition_id"]
                )
                verify_identity_edges(
                    connection,
                    proof,
                    migration_id,
                    epoch,
                    verify_target=not reference.get("source_only", False),
                )
            lock_queries = (
                _mysql_lock_queries(connection, step) if direction == "rollback" else []
            )
            if lock_queries:
                for index, statement in enumerate(step["sql"]):
                    _insert_checkpoint(
                        connection,
                        migration_id,
                        epoch,
                        run_id,
                        direction,
                        operation_id,
                        index,
                        _statement_checksum(direction, operation_id, index, statement),
                    )
                connection.commit()
                possible_effect = True
                for query in lock_queries:
                    connection.execute(text(query))
                _verify_rollback_step_edges(connection, plan, step, migration_id, epoch)
                _run_guards(connection, step.get("guards", []), "before")
                for index, statement in enumerate(step["sql"]):
                    insert_proof = _insert_proof(step, index)
                    expected = (
                        _expected_inserts(connection, insert_proof, statement)
                        if insert_proof
                        else None
                    )
                    result = execute_statement(
                        connection,
                        _write_sql(statement),
                        step=step,
                        index=index,
                        before=before_statement,
                        after=after_statement,
                    )
                    if insert_proof:
                        if result.rowcount != expected:
                            raise DataOwnershipError(
                                "Insert ownership proof failed; affected rows do not match claimed rows"
                            )
                        _finish_checkpoint(
                            connection,
                            migration_id,
                            epoch,
                            direction,
                            operation_id,
                            index,
                            status="WRITE_VERIFIED",
                        )
                _run_guards(connection, step.get("guards", []), "after")
                _record_identity_edges(
                    connection,
                    step,
                    migration_id,
                    epoch,
                    run_id,
                    operation_id,
                    clickhouse=False,
                )
                for index in range(len(step["sql"])):
                    _finish_checkpoint(
                        connection,
                        migration_id,
                        epoch,
                        direction,
                        operation_id,
                        index,
                    )
                connection.commit()
                continue
            _run_guards(connection, step.get("guards", []), "before")
            connection.commit()
            if not step.get("sql", []):
                _run_guards(connection, step.get("guards", []), "after")
                _record_identity_edges(
                    connection,
                    step,
                    migration_id,
                    epoch,
                    run_id,
                    operation_id,
                    clickhouse=False,
                )
                _insert_checkpoint(
                    connection,
                    migration_id,
                    epoch,
                    run_id,
                    direction,
                    operation_id,
                    -1,
                    _checkpoint_checksum(step, direction, operation_id, -1),
                    status="DONE",
                )
                connection.commit()
                continue
            pending = []
            for index, statement in enumerate(step.get("sql", [])):
                statement_checksum = _statement_checksum(
                    direction, operation_id, index, statement
                )
                if before_statement is not None:
                    before_statement(statement)
                _insert_checkpoint(
                    connection,
                    migration_id,
                    epoch,
                    run_id,
                    direction,
                    operation_id,
                    index,
                    statement_checksum,
                )
                connection.commit()
                possible_effect = True
                insert_proof = _insert_proof(step, index)
                expected = (
                    _expected_inserts(connection, insert_proof, statement)
                    if insert_proof
                    else None
                )
                result = execute_statement(
                    connection,
                    _write_sql(statement),
                    step=step,
                    index=index,
                    before=before_statement if step.get("batches") else None,
                )
                if insert_proof:
                    if result.rowcount != expected:
                        raise DataOwnershipError(
                            "Insert ownership proof failed; affected rows do not match claimed rows"
                        )
                    _finish_checkpoint(
                        connection,
                        migration_id,
                        epoch,
                        direction,
                        operation_id,
                        index,
                        status="WRITE_VERIFIED",
                    )
                    connection.commit()
                if after_statement is not None:
                    after_statement(statement)
                pending.append(index)
            _run_guards(connection, step.get("guards", []), "after")
            _record_identity_edges(
                connection,
                step,
                migration_id,
                epoch,
                run_id,
                operation_id,
                clickhouse=False,
            )
            for index in pending:
                _finish_checkpoint(
                    connection, migration_id, epoch, direction, operation_id, index
                )
            connection.commit()
        if record_success is not None:
            record_success()
        final_status = _ROLLBACK if direction == "rollback" else _SUCCESS
        _finish_run(connection, migration_id, epoch, final_status, baseline=False)
        connection.commit()
        return _result(final_status, migration_id, epoch, checksum, run_id)
    except BaseException as exc:
        error = _failure_text(exc, current_operation)
        try:
            if connection.in_transaction():
                connection.rollback()
            status = (
                "FAILED_FINAL"
                if isinstance(exc, DataOwnershipError)
                else _UNKNOWN
                if possible_effect
                else _RETRYABLE
            )
            proof = (
                "possible side effects: " if possible_effect else "no side effects: "
            )
            _finish_run(
                connection,
                migration_id,
                epoch,
                status,
                error=proof + error,
                baseline=baseline,
            )
            connection.commit()
        except SQLAlchemyError:
            _best_effort_rollback(connection)
        if isinstance(exc, DBAPIError):
            raise _sanitized_database_error(exc, current_operation) from None
        raise


def _reconcile_nontransactional(connection, migration_id, *, plan, apply, decision):
    if not _has_table(connection, _JOURNAL):
        return {
            "migration_id": migration_id,
            "status": "NOT_STARTED",
            "retry_allowed": True,
        }
    run = _latest_run(connection, migration_id)
    connection.commit()
    if run is None:
        return {
            "migration_id": migration_id,
            "status": "NOT_STARTED",
            "retry_allowed": True,
        }
    checkpoints = _checkpoint_rows(connection, migration_id, run["epoch"])
    current_checkpoints = [row for row in checkpoints if row["run_id"] == run["run_id"]]
    connection.commit()
    visible_status = _UNKNOWN if run["status"] == "APPLYING" else run["status"]
    result = {
        "migration_id": migration_id,
        **run,
        "status": visible_status,
        "checkpoints": checkpoints,
        "retry_allowed": run["status"] == _RETRYABLE
        and _has_nontransactional_proof(run),
    }
    if not apply:
        return result
    if decision == "abandon" and visible_status == _UNKNOWN:
        _finish_run(
            connection,
            migration_id,
            run["epoch"],
            "ABANDONED",
            error=run.get("error"),
            baseline=bool(run["baseline"]),
        )
        connection.commit()
        return {**result, "status": "ABANDONED", "retry_allowed": False}
    if decision != "verified_retry" or visible_status != _UNKNOWN or plan is None:
        raise ValueError(
            "Nontransactional reconciliation requires UNKNOWN_REQUIRES_RECONCILIATION, a plan, and decision='verified_retry'"
        )
    if _plan_checksum(plan) != run["checksum"]:
        raise ValueError(
            "Reconciliation plan checksum does not match the attempted plan"
        )
    steps = {
        (direction, _operation_id(step)): step
        for direction in ("upgrade", "rollback")
        for step in _steps(plan, direction)
    }
    touched = {(row["direction"], row["operation_id"]) for row in current_checkpoints}
    for row in current_checkpoints:
        step = steps.get((row["direction"], row["operation_id"]))
        index = row["statement_index"]
        if step is None or row["statement_checksum"] != _checkpoint_checksum(
            step, row["direction"], row["operation_id"], index
        ):
            raise ValueError(
                "Stored operation checkpoint does not match the reviewed plan"
            )
        if (
            index >= 0
            and _insert_proof(step, index)
            and row["status"] not in {"WRITE_VERIFIED", "DONE"}
        ):
            raise ValueError(
                "Insert ownership is unproven; equivalent rows cannot authorize retry"
            )
    for direction, operation_id in sorted(touched):
        step = steps[(direction, operation_id)]
        rows = [
            row
            for row in current_checkpoints
            if row["direction"] == direction and row["operation_id"] == operation_id
        ]
        if not step.get("sql", []):
            if (
                len(rows) != 1
                or rows[0]["statement_index"] != -1
                or rows[0]["status"] != "DONE"
            ):
                raise ValueError(
                    f"Operation '{operation_id}' guard checkpoint is unproven"
                )
            continue
        if {row["statement_index"] for row in rows} != set(range(len(step["sql"]))):
            raise ValueError(
                f"Operation '{operation_id}' is incomplete and cannot be reconciled"
            )
        after = [
            guard
            for guard in step.get("guards", [])
            if guard.get("timing", "before") == "after"
        ]
        if not after:
            raise ValueError(
                f"Operation '{operation_id}' has no verified postcondition"
            )
        _run_guards(connection, after, "after")
        _record_identity_edges(
            connection,
            step,
            migration_id,
            run["epoch"],
            run["run_id"],
            operation_id,
            clickhouse=False,
        )
        connection.execute(
            text(
                f"UPDATE {_CHECKPOINTS} SET status = 'DONE', finished_at = :finished_at "
                "WHERE migration_id = :migration_id AND epoch = :epoch "
                "AND direction = :direction AND operation_id = :operation_id"
            ),
            {
                "finished_at": _now(),
                "migration_id": migration_id,
                "epoch": run["epoch"],
                "direction": direction,
                "operation_id": operation_id,
            },
        )
    _finish_run(
        connection,
        migration_id,
        run["epoch"],
        _RETRYABLE,
        error="reconciled verified: postconditions passed",
        baseline=bool(run["baseline"]),
    )
    connection.commit()
    checkpoints = _checkpoint_rows(connection, migration_id, run["epoch"])
    connection.commit()
    return {
        **result,
        "status": _RETRYABLE,
        "checkpoints": checkpoints,
        "retry_allowed": True,
        "decision": "verified_retry",
    }


def _execute_clickhouse(
    connection,
    plan,
    *,
    migration_id,
    direction,
    record_success,
    baseline,
    baseline_reason,
    reapply,
    before_statement,
    after_statement,
):
    checksum = _plan_checksum(plan)
    steps = _steps(plan, direction)
    if direction == "rollback" and _is_irreversible(plan):
        raise ValueError("Data rollback is irreversible and cannot be executed")
    if not _ch_metadata_exists(connection):
        _ch_create_metadata(connection)
    existing = _ch_latest_run(connection, migration_id)
    if existing:
        _ch_validate_metadata(connection, migration_id, existing["epoch"])
        if existing["checksum"] != checksum:
            raise ValueError(
                f"Data plan checksum mismatch for migration '{migration_id}'"
            )
    if direction == "upgrade" and existing and existing["status"] == _SUCCESS:
        return {
            "status": "already_applied",
            "migration_id": migration_id,
            "epoch": existing["epoch"],
            "checksum": checksum,
            "baseline": bool(existing["baseline"]),
        }
    if (
        direction == "upgrade"
        and existing
        and existing["status"] == _ROLLBACK
        and not reapply
    ):
        raise RuntimeError(
            f"Data migration '{migration_id}' was rolled back; explicit reapply is required"
        )
    if existing and existing["status"] in {"ABANDONED", "FAILED_FINAL"}:
        raise RuntimeError(
            f"Data migration '{migration_id}' is terminally {existing['status']}"
        )
    _prepare_identity_edges(connection, steps, clickhouse=True)
    resumable = (
        existing
        and existing["status"] == _RETRYABLE
        and existing["direction"] == direction
        and _has_nontransactional_proof(existing)
    )
    if direction == "rollback":
        if (
            not existing
            or existing["baseline"]
            or existing["status"] != _SUCCESS
            and not resumable
        ):
            raise ValueError("Rollback requires a successfully executed data migration")
        epoch = int(existing["epoch"])
    elif resumable:
        epoch = int(existing["epoch"])
    else:
        if existing and existing["status"] in {"APPLYING", _UNKNOWN, _RETRYABLE}:
            raise RuntimeError(
                f"Data migration '{migration_id}' requires reconciliation before retry"
            )
        epoch = int(existing["epoch"]) + 1 if existing else 1
    run_id = str(uuid.uuid4())
    if existing and epoch == int(existing["epoch"]):
        run = _ch_append_run(
            connection,
            existing,
            status="APPLYING",
            run_id=run_id,
            direction=direction,
            error="",
            started_at=_now(),
            finished_at="",
        )
    else:
        run = _ch_new_run(
            connection, migration_id, epoch, checksum, run_id, direction, baseline
        )
        if existing and existing["status"] == _ROLLBACK:
            _ch_append_event(
                connection,
                run,
                "REAPPLY",
                _reapply_details(int(existing["epoch"])),
            )
    possible_effect = bool(
        _ch_checkpoint_rows(connection, migration_id, epoch, direction=direction)
    )
    current_operation = None
    try:
        if baseline:
            if record_success is not None:
                record_success()
            run = _ch_append_run(
                connection,
                run,
                status=_SUCCESS,
                error="",
                baseline=1,
                finished_at=_now(),
            )
            _ch_append_event(
                connection,
                run,
                "BASELINE",
                _baseline_details(plan, baseline_reason, direction),
            )
            return _result(
                _SUCCESS, migration_id, epoch, checksum, run_id, baseline=True
            )
        for step in steps:
            operation_id = _operation_id(step)
            current_operation = operation_id
            run = _ch_append_run(connection, run, last_operation=operation_id)
            _run_guards(connection, step.get("guards", []), "before")
            rows = {
                row["statement_index"]: row
                for row in _ch_checkpoint_rows(
                    connection, migration_id, epoch, direction, operation_id
                )
            }
            pending = []
            for index, statement in enumerate(step.get("sql", [])):
                statement_checksum = _statement_checksum(
                    direction, operation_id, index, statement
                )
                row = rows.get(index)
                if row:
                    if row["statement_checksum"] != statement_checksum:
                        raise ValueError(
                            f"Checkpoint checksum mismatch for operation '{operation_id}'"
                        )
                    if row["status"] == "DONE":
                        continue
                    raise RuntimeError(
                        f"Operation '{operation_id}' has an unresolved checkpoint; "
                        "reconcile before retry"
                    )
                if before_statement is not None:
                    before_statement(statement)
                checkpoint = _ch_new_checkpoint(
                    connection,
                    migration_id,
                    epoch,
                    run_id,
                    direction,
                    operation_id,
                    index,
                    statement_checksum,
                )
                possible_effect = True
                connection.execute(text(_write_sql(statement)))
                _check_clickhouse_mutation(connection, statement)
                if after_statement is not None:
                    after_statement(statement)
                pending.append(checkpoint)
            _run_guards(connection, step.get("guards", []), "after")
            _record_identity_edges(
                connection,
                step,
                migration_id,
                epoch,
                run_id,
                operation_id,
                clickhouse=True,
            )
            for checkpoint in pending:
                _ch_append_checkpoint(
                    connection, checkpoint, status="DONE", finished_at=_now()
                )
        if record_success is not None:
            record_success()
        final_status = _ROLLBACK if direction == "rollback" else _SUCCESS
        _ch_append_run(
            connection, run, status=final_status, baseline=0, finished_at=_now()
        )
        return _result(final_status, migration_id, epoch, checksum, run_id)
    except BaseException as exc:
        status = _UNKNOWN if possible_effect else _RETRYABLE
        proof = "possible side effects: " if possible_effect else "no side effects: "
        _ch_persist_failure(
            connection,
            run,
            status,
            proof + _failure_text(exc, current_operation),
            int(baseline),
        )
        if isinstance(exc, DBAPIError):
            raise _sanitized_database_error(exc, current_operation) from None
        raise


def _reconcile_clickhouse(connection, migration_id, *, plan, apply, decision):
    if not _ch_metadata_exists(connection):
        return {
            "migration_id": migration_id,
            "status": "NOT_STARTED",
            "retry_allowed": True,
        }
    run = _ch_latest_run(connection, migration_id)
    if run is None:
        return {
            "migration_id": migration_id,
            "status": "NOT_STARTED",
            "retry_allowed": True,
        }
    _ch_validate_metadata(connection, migration_id, run["epoch"])
    checkpoints = _ch_checkpoint_rows(connection, migration_id, run["epoch"])
    current = [row for row in checkpoints if row["run_id"] == run["run_id"]]
    visible_status = _UNKNOWN if run["status"] == "APPLYING" else run["status"]
    result = {
        "migration_id": migration_id,
        **run,
        "status": visible_status,
        "checkpoints": checkpoints,
        "retry_allowed": run["status"] == _RETRYABLE
        and _has_nontransactional_proof(run),
    }
    if not apply:
        return result
    if decision == "abandon" and visible_status == _UNKNOWN:
        abandoned = _ch_append_run(
            connection, run, status="ABANDONED", finished_at=_now()
        )
        return {**result, **abandoned, "retry_allowed": False}
    if decision != "verified_retry" or visible_status != _UNKNOWN or plan is None:
        raise ValueError(
            "ClickHouse reconciliation requires UNKNOWN_REQUIRES_RECONCILIATION, "
            "a plan, and decision='verified_retry'"
        )
    if _plan_checksum(plan) != run["checksum"]:
        raise ValueError(
            "Reconciliation plan checksum does not match the attempted plan"
        )
    steps = {
        (item_direction, _operation_id(step)): step
        for item_direction in ("upgrade", "rollback")
        for step in _steps(plan, item_direction)
    }
    touched = {(row["direction"], row["operation_id"]) for row in current}
    for row in current:
        step = steps.get((row["direction"], row["operation_id"]))
        statements = step.get("sql", []) if step else []
        index = row["statement_index"]
        if (
            step is None
            or index >= len(statements)
            or row["statement_checksum"]
            != _statement_checksum(
                row["direction"], row["operation_id"], index, statements[index]
            )
        ):
            raise ValueError(
                "Stored operation checkpoint does not match the reviewed plan"
            )
        _check_clickhouse_mutation(connection, statements[index])
    for item_direction, operation_id in touched:
        step = steps[(item_direction, operation_id)]
        after = [
            guard
            for guard in step.get("guards", [])
            if guard.get("timing", "before") == "after"
        ]
        if not after:
            raise ValueError(
                f"Operation '{operation_id}' has no verified postcondition"
            )
        _run_guards(connection, after, "after")
        _record_identity_edges(
            connection,
            step,
            migration_id,
            run["epoch"],
            run["run_id"],
            operation_id,
            clickhouse=True,
        )
        for checkpoint in current:
            if (
                checkpoint["direction"] == item_direction
                and checkpoint["operation_id"] == operation_id
                and checkpoint["status"] != "DONE"
            ):
                _ch_append_checkpoint(
                    connection, checkpoint, status="DONE", finished_at=_now()
                )
    retriable = _ch_append_run(
        connection,
        run,
        status=_RETRYABLE,
        error="reconciled verified: postconditions passed",
        finished_at=_now(),
    )
    checkpoints = _ch_checkpoint_rows(connection, migration_id, run["epoch"])
    return {
        **result,
        **retriable,
        "checkpoints": checkpoints,
        "retry_allowed": True,
        "decision": "verified_retry",
    }


def _read_only_sql(query: str) -> str:
    from sqlglot import exp, parse
    from sqlglot.errors import ParseError

    try:
        expressions = parse(query, error_level="raise")
    except ParseError:
        raise ValueError("Data guard query is invalid") from None
    if len(expressions) != 1 or not isinstance(
        expressions[0], (exp.Select, exp.Union, exp.Subquery)
    ):
        raise ValueError("Data guard query must be one read-only SELECT or WITH query")
    forbidden = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.Command,
    )
    if any(expressions[0].find(kind) is not None for kind in forbidden):
        raise ValueError("Data guard query must be read-only")
    return query


def _write_sql(statement: str) -> str:
    from sqlglot import parse
    from sqlglot.errors import ParseError

    if not isinstance(statement, str):
        raise TypeError("Data operation SQL must be a string")
    try:
        expressions = parse(statement, error_level="raise")
    except ParseError:
        raise ValueError("Data operation SQL is invalid") from None
    if len(expressions) != 1:
        raise ValueError("Data operation SQL must contain exactly one statement")
    return statement


def _ch_create_metadata(connection) -> None:
    connection.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_JOURNAL} ("
            "migration_id String, epoch UInt64, checksum String, status String, "
            "run_id String, direction String, last_operation String, error String, "
            "baseline UInt8, started_at String, finished_at String, version UInt64, "
            "previous_record_checksum String, record_checksum String) "
            "ENGINE = ReplacingMergeTree(version) ORDER BY (migration_id, epoch)"
        )
    )
    connection.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_EVENTS} (event_id String, "
            "migration_id String, epoch UInt64, run_id String, event_type String, "
            "status String, operation_id String, checksum String, details String, "
            "created_at String) ENGINE = MergeTree ORDER BY "
            "(migration_id, epoch, created_at, event_id)"
        )
    )
    connection.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_CHECKPOINTS} ("
            "migration_id String, epoch UInt64, run_id String, direction String, "
            "operation_id String, statement_index UInt64, statement_checksum String, "
            "status String, started_at String, finished_at String, version UInt64, "
            "previous_record_checksum String, record_checksum String) "
            "ENGINE = ReplacingMergeTree(version) ORDER BY "
            "(migration_id, epoch, direction, operation_id, statement_index)"
        )
    )


def _ch_metadata_exists(connection) -> bool:
    runs = connection.execute(text(f"EXISTS TABLE {_JOURNAL}")).scalar_one()
    checkpoints = connection.execute(text(f"EXISTS TABLE {_CHECKPOINTS}")).scalar_one()
    events = connection.execute(text(f"EXISTS TABLE {_EVENTS}")).scalar_one()
    return bool(runs) and bool(checkpoints) and bool(events)


def _ch_latest_run(connection, migration_id: str) -> dict[str, Any] | None:
    row = (
        connection.execute(
            text(
                f"SELECT {', '.join(_CH_RUN_FIELDS)} FROM {_JOURNAL} FINAL "
                "WHERE migration_id = :migration_id ORDER BY epoch DESC LIMIT 1"
            ),
            {"migration_id": migration_id},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _ch_new_run(connection, migration_id, epoch, checksum, run_id, direction, baseline):
    row = {
        "migration_id": migration_id,
        "epoch": epoch,
        "checksum": checksum,
        "status": "APPLYING",
        "run_id": run_id,
        "direction": direction,
        "last_operation": "",
        "error": "",
        "baseline": int(baseline),
        "started_at": _now(),
        "finished_at": "",
        "version": _ch_version(),
        "previous_record_checksum": "",
    }
    result = _ch_insert_record(connection, _JOURNAL, _CH_RUN_FIELDS, "run", row)
    _ch_append_event(
        connection, result, "RUN_STARTED", "baseline acknowledged" if baseline else ""
    )
    return result


def _ch_append_run(connection, previous, **updates):
    row = {
        key: previous[key]
        for key in _CH_RUN_FIELDS
        if key not in {"version", "previous_record_checksum", "record_checksum"}
    }
    row.update(updates)
    row["version"] = _ch_version()
    row["previous_record_checksum"] = previous["record_checksum"]
    result = _ch_insert_record(connection, _JOURNAL, _CH_RUN_FIELDS, "run", row)
    if "last_operation" in updates and "status" not in updates:
        event_type = "OPERATION_STARTED"
    elif updates.get("status") == "APPLYING" and updates.get("run_id"):
        event_type = "RUN_RESUMED"
    else:
        event_type = "RUN_STATUS"
    details = "baseline acknowledged" if updates.get("baseline") else ""
    _ch_append_event(connection, result, event_type, details)
    return result


def _ch_new_checkpoint(
    connection,
    migration_id,
    epoch,
    run_id,
    direction,
    operation_id,
    statement_index,
    statement_checksum,
):
    row = {
        "migration_id": migration_id,
        "epoch": epoch,
        "run_id": run_id,
        "direction": direction,
        "operation_id": operation_id,
        "statement_index": statement_index,
        "statement_checksum": statement_checksum,
        "status": "PENDING",
        "started_at": _now(),
        "finished_at": "",
        "version": _ch_version(),
        "previous_record_checksum": "",
    }
    return _ch_insert_record(
        connection, _CHECKPOINTS, _CH_CHECKPOINT_FIELDS, "checkpoint", row
    )


def _ch_append_checkpoint(connection, previous, **updates):
    row = {
        key: previous[key]
        for key in _CH_CHECKPOINT_FIELDS
        if key not in {"version", "previous_record_checksum", "record_checksum"}
    }
    row.update(updates)
    row["version"] = _ch_version()
    row["previous_record_checksum"] = previous["record_checksum"]
    return _ch_insert_record(
        connection, _CHECKPOINTS, _CH_CHECKPOINT_FIELDS, "checkpoint", row
    )


def _ch_insert_record(connection, table, fields, kind, row):
    row["record_checksum"] = _ch_record_checksum(kind, row)
    connection.execute(
        text(
            f"INSERT INTO {table} ({', '.join(fields)}) VALUES "
            f"({', '.join(':' + field for field in fields)})"
        ),
        row,
    )
    return row


def _ch_record_checksum(kind: str, row: dict[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "record_checksum"}
    raw = json.dumps(
        {"domain": f"dbwarden.clickhouse.{kind}.v1", "record": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ch_version() -> int:
    global _CH_LAST_VERSION
    _CH_LAST_VERSION = max(time.time_ns(), _CH_LAST_VERSION + 1)
    return _CH_LAST_VERSION


def _ch_append_event(connection, run, event_type, details="") -> None:
    operation_id = run["last_operation"] if event_type == "OPERATION_STARTED" else ""
    record = {
        "event_id": str(uuid.uuid4()),
        "migration_id": run["migration_id"],
        "epoch": run["epoch"],
        "run_id": run["run_id"],
        "event_type": event_type,
        "status": run["status"],
        "operation_id": operation_id,
        "checksum": run["checksum"],
        "details": details,
        "created_at": _now(),
    }
    connection.execute(
        text(
            f"INSERT INTO {_EVENTS} ({', '.join(_CH_EVENT_FIELDS)}) VALUES "
            f"({', '.join(':' + field for field in _CH_EVENT_FIELDS)})"
        ),
        record,
    )


def _ch_persist_failure(connection, run, status, error, baseline) -> bool:
    try:
        _ch_append_run(
            connection,
            run,
            status=status,
            error=error,
            baseline=baseline,
            finished_at=_now(),
        )
    except SQLAlchemyError:
        return False
    return True


def _ch_checkpoint_rows(
    connection, migration_id, epoch, direction=None, operation_id=None
):
    clauses = ["migration_id = :migration_id", "epoch = :epoch"]
    parameters = {"migration_id": migration_id, "epoch": epoch}
    if direction is not None:
        clauses.append("direction = :direction")
        parameters["direction"] = direction
    if operation_id is not None:
        clauses.append("operation_id = :operation_id")
        parameters["operation_id"] = operation_id
    rows = (
        connection.execute(
            text(
                f"SELECT {', '.join(_CH_CHECKPOINT_FIELDS)} FROM {_CHECKPOINTS} FINAL "
                f"WHERE {' AND '.join(clauses)} "
                "ORDER BY direction, operation_id, statement_index"
            ),
            parameters,
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def _ch_validate_metadata(connection, migration_id, epoch) -> None:
    parameters = {"migration_id": migration_id, "epoch": epoch}
    runs = (
        connection.execute(
            text(
                f"SELECT {', '.join(_CH_RUN_FIELDS)} FROM {_JOURNAL} "
                "WHERE migration_id = :migration_id AND epoch = :epoch ORDER BY version"
            ),
            parameters,
        )
        .mappings()
        .all()
    )
    checkpoints = (
        connection.execute(
            text(
                f"SELECT {', '.join(_CH_CHECKPOINT_FIELDS)} FROM {_CHECKPOINTS} "
                "WHERE migration_id = :migration_id AND epoch = :epoch "
                "ORDER BY direction, operation_id, statement_index, version"
            ),
            parameters,
        )
        .mappings()
        .all()
    )
    _ch_validate_chains([dict(row) for row in runs], ("migration_id", "epoch"), "run")
    _ch_validate_chains(
        [dict(row) for row in checkpoints],
        ("migration_id", "epoch", "direction", "operation_id", "statement_index"),
        "checkpoint",
    )


def _ch_validate_chains(rows, key_fields, kind):
    tails = {}
    for row in rows:
        key = tuple(row[field] for field in key_fields)
        expected_previous = tails.get(key, "")
        if row["previous_record_checksum"] != expected_previous:
            raise RuntimeError(f"ClickHouse {kind} journal checksum chain is broken")
        if row["record_checksum"] != _ch_record_checksum(kind, row):
            raise RuntimeError(f"ClickHouse {kind} journal record checksum is invalid")
        tails[key] = row["record_checksum"]


def _check_clickhouse_mutation(connection, statement: str) -> None:
    match = re.match(
        r"\s*ALTER\s+TABLE\s+(.+?)\s+(?:UPDATE|DELETE)\b",
        statement,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return
    identifiers = [item.strip().strip('`"') for item in match.group(1).split(".")]
    table_name = identifiers[-1]
    parameters = {"table_name": table_name}
    if len(identifiers) > 1:
        database_clause = "database = :database_name"
        parameters["database_name"] = identifiers[-2]
    else:
        database_clause = "database = currentDatabase()"
    row = (
        connection.execute(
            text(
                "SELECT is_done, latest_fail_reason FROM system.mutations WHERE "
                f"{database_clause} AND table = :table_name "
                "ORDER BY create_time DESC, mutation_id DESC LIMIT 1"
            ),
            parameters,
        )
        .mappings()
        .first()
    )
    if row is None:
        raise RuntimeError("ClickHouse mutation state is unavailable")
    if not bool(row["is_done"]):
        raise RuntimeError("ClickHouse mutation remains outstanding")
    if row["latest_fail_reason"]:
        raise RuntimeError(
            "ClickHouse mutation failed; inspect backend diagnostics with authorized access"
        )


def _prepare_identity_edges(connection, steps, *, clickhouse: bool) -> None:
    if not any(step.get("identity_edges") for step in steps):
        return
    if clickhouse:
        if not bool(connection.execute(text(f"EXISTS TABLE {_KEYS}")).scalar_one()):
            connection.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {_KEYS} (key_id String, "
                    "key_material String, status String, created_at String) "
                    "ENGINE = MergeTree ORDER BY key_id"
                )
            )
        if not bool(connection.execute(text(f"EXISTS TABLE {_EDGES}")).scalar_one()):
            connection.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {_EDGES} (migration_id String, "
                    "epoch UInt64, run_id String, operation_id String, definition_id String, "
                    "source_identity_hash String, target_identity_hash String, "
                    "value_hash String, owned_write UInt8, key_id String, encoding_version UInt16, "
                    "status String, verification_checksum String, created_at String) "
                    "ENGINE = MergeTree ORDER BY (migration_id, epoch, operation_id, "
                    "definition_id, source_identity_hash)"
                )
            )
    else:
        if not _has_table(connection, _KEYS):
            connection.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {_KEYS} ("
                    "key_id VARCHAR(64) PRIMARY KEY, key_material VARCHAR(128) NOT NULL, "
                    "status VARCHAR(16) NOT NULL, created_at VARCHAR(40) NOT NULL)"
                )
            )
        if not _has_table(connection, _EDGES):
            connection.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {_EDGES} ("
                    "edge_id VARCHAR(64) PRIMARY KEY, migration_id VARCHAR(255) NOT NULL, "
                    "epoch INTEGER NOT NULL, "
                    "run_id VARCHAR(64) NOT NULL, operation_id VARCHAR(255) NOT NULL, "
                    "definition_id VARCHAR(255) NOT NULL, source_identity_hash VARCHAR(128) NOT NULL, "
                    "target_identity_hash VARCHAR(128) NOT NULL, value_hash VARCHAR(128) NOT NULL, "
                    "owned_write INTEGER NOT NULL, "
                    "key_id VARCHAR(64) NOT NULL, encoding_version INTEGER NOT NULL, "
                    "status VARCHAR(32) NOT NULL, verification_checksum VARCHAR(128) NOT NULL, "
                    "created_at VARCHAR(40) NOT NULL)"
                )
            )
    if not _edge_keys(connection):
        connection.execute(
            text(
                f"INSERT INTO {_KEYS} (key_id, key_material, status, created_at) "
                "VALUES (:key_id, :key_material, 'ACTIVE', :created_at)"
            ),
            {
                "key_id": str(uuid.uuid4()),
                "key_material": secrets.token_hex(32),
                "created_at": _now(),
            },
        )


def _record_identity_edges(
    connection, step, migration_id, epoch, run_id, operation_id, *, clickhouse
) -> None:
    definition = step.get("identity_edges")
    if not definition:
        return
    if not isinstance(definition, dict):
        raise TypeError("identity_edges must be an object")
    definition_id = definition.get("definition_id")
    ledger_table = definition.get("ledger_table")
    source_columns = definition.get("source_columns")
    target_identity_columns = definition.get(
        "target_identity_columns", definition.get("target_columns")
    )
    value_columns = definition.get("value_columns", [])
    owned_column = definition.get("owned_column")
    if (
        not isinstance(definition_id, str)
        or not definition_id
        or not isinstance(ledger_table, str)
        or not isinstance(source_columns, list)
        or not source_columns
        or not isinstance(target_identity_columns, list)
        or not target_identity_columns
        or not isinstance(value_columns, list)
        or not isinstance(owned_column, str)
    ):
        raise TypeError("identity_edges metadata is incomplete")
    names = [*source_columns, *target_identity_columns, *value_columns, owned_column]
    if any(not isinstance(name, str) for name in names):
        raise TypeError("identity_edges columns must be strings")
    table_sql = _edge_identifier(ledger_table, connection.dialect.name)
    projections = []
    for index, name in enumerate(source_columns):
        projections.append(
            f"{_edge_identifier(name, connection.dialect.name)} AS _dbw_s_{index}"
        )
    for index, name in enumerate(target_identity_columns):
        projections.append(
            f"{_edge_identifier(name, connection.dialect.name)} AS _dbw_t_{index}"
        )
    for index, name in enumerate(value_columns):
        projections.append(
            f"{_edge_identifier(name, connection.dialect.name)} AS _dbw_v_{index}"
        )
    projections.append(
        f"{_edge_identifier(owned_column, connection.dialect.name)} AS _dbw_owned"
    )
    ledger_rows = (
        connection.execute(text(f"SELECT {', '.join(projections)} FROM {table_sql}"))
        .mappings()
        .all()
    )
    keys = _edge_keys(connection)
    active = next((key for key in keys if key["status"] == "ACTIVE"), None)
    if active is None:
        raise RuntimeError("No active data-edge HMAC key is available")
    pending = {}
    seen_source_hashes = {}
    for ledger_row in ledger_rows:
        source = tuple(
            ledger_row[f"_dbw_s_{index}"] for index in range(len(source_columns))
        )
        target = tuple(
            ledger_row[f"_dbw_t_{index}"]
            for index in range(len(target_identity_columns))
        )
        values = tuple(
            ledger_row[f"_dbw_v_{index}"] for index in range(len(value_columns))
        )
        key_count = definition.get("source_key_count", len(source_columns))
        if type(key_count) is not int or not 0 < key_count <= len(source_columns):
            raise ValueError("Identity-edge source key count is invalid")
        if any(value is None for value in (*source[:key_count], *target)):
            raise ValueError("Identity-edge tuples cannot contain NULL")
        owned = ledger_row["_dbw_owned"]
        if type(owned) not in {bool, int} or int(owned) not in {0, 1}:
            raise ValueError("Identity-edge owned_write must be boolean")
        normalized_source_hash = _edge_identity_hash(active, "source", source)
        normalized_target_hash = _edge_identity_hash(active, "target", target)
        normalized_value_hash = _edge_identity_hash(active, "values", values)
        normalized_effect = (normalized_target_hash, normalized_value_hash, bool(owned))
        if normalized_source_hash in seen_source_hashes:
            if seen_source_hashes[normalized_source_hash] != normalized_effect:
                raise ValueError("Conflicting identity edges in transition ledger")
            raise ValueError(
                "Duplicate normalized source identity in transition ledger"
            )
        seen_source_hashes[normalized_source_hash] = normalized_effect
        matched = False
        for key in keys:
            source_hash = _edge_identity_hash(key, "source", source)
            target_hash = _edge_identity_hash(key, "target", target)
            value_hash = _edge_identity_hash(key, "values", values)
            existing = _find_edge(
                connection,
                migration_id,
                epoch,
                definition_id,
                source_hash,
                key["key_id"],
            )
            if existing is None:
                continue
            _verify_edge(existing, key)
            if (
                existing["target_identity_hash"] != target_hash
                or existing["value_hash"] != value_hash
                or bool(existing["owned_write"]) != bool(owned)
            ):
                raise ValueError("Conflicting verified identity edge")
            matched = True
            break
        if matched:
            continue
        source_hash = normalized_source_hash
        target_hash = normalized_target_hash
        value_hash = normalized_value_hash
        identity = (definition_id, source_hash)
        value = (target_hash, value_hash, bool(owned))
        if identity in pending and pending[identity] != value:
            raise ValueError("Conflicting identity edges in transition ledger")
        pending[identity] = value
    for (edge_definition, source_hash), (
        target_hash,
        value_hash,
        owned,
    ) in pending.items():
        record = {
            "migration_id": migration_id,
            "epoch": epoch,
            "run_id": run_id,
            "operation_id": operation_id,
            "definition_id": edge_definition,
            "source_identity_hash": source_hash,
            "target_identity_hash": target_hash,
            "value_hash": value_hash,
            "owned_write": int(owned),
            "key_id": active["key_id"],
            "encoding_version": 1,
            "status": "VERIFIED",
            "created_at": _now(),
        }
        record["verification_checksum"] = _edge_checksum(record, active)
        if not clickhouse:
            record["edge_id"] = _edge_record_id(
                migration_id, epoch, operation_id, edge_definition, source_hash
            )
        connection.execute(
            text(
                f"INSERT INTO {_EDGES} ({', '.join(record)}) VALUES "
                f"({', '.join(':' + key for key in record)})"
            ),
            record,
        )


def verify_identity_edges(
    connection, step, migration_id, epoch, *, verify_target=True
) -> dict[str, Any]:
    definition = step.get("identity_edges")
    if not isinstance(definition, dict):
        raise TypeError("identity_edges must be an object")
    definition_id = definition.get("definition_id")
    ledger_table = definition.get("ledger_table")
    source_columns = definition.get("source_columns")
    target_identity_columns = definition.get("target_identity_columns")
    value_columns = definition.get("value_columns")
    target_ref = definition.get("target_table")
    target_columns = definition.get("target_columns")
    target_key_columns = definition.get("target_key_columns")
    owned_column = definition.get("owned_column")
    lists = (
        source_columns,
        target_identity_columns,
        value_columns,
        target_columns,
        target_key_columns,
    )
    if (
        not isinstance(definition_id, str)
        or not isinstance(ledger_table, str)
        or not isinstance(target_ref, dict)
        or not isinstance(owned_column, str)
        or any(not isinstance(items, list) or not items for items in lists)
        or len(target_identity_columns) != len(target_key_columns)
        or len(value_columns) != len(target_columns)
    ):
        raise TypeError("identity_edges convergence metadata is incomplete")
    dialect = connection.dialect.name
    ledger_sql = _edge_identifier(ledger_table, dialect)
    projections = [
        *(
            f"{_edge_identifier(name, dialect)} AS _dbw_s_{index}"
            for index, name in enumerate(source_columns)
        ),
        *(
            f"{_edge_identifier(name, dialect)} AS _dbw_t_{index}"
            for index, name in enumerate(target_identity_columns)
        ),
        *(
            f"{_edge_identifier(name, dialect)} AS _dbw_v_{index}"
            for index, name in enumerate(value_columns)
        ),
        f"{_edge_identifier(owned_column, dialect)} AS _dbw_owned",
    ]
    ledger_rows = (
        connection.execute(text(f"SELECT {', '.join(projections)} FROM {ledger_sql}"))
        .mappings()
        .all()
    )
    keys = {key["key_id"]: key for key in _edge_keys(connection)}
    seen = set()
    for ledger_row in ledger_rows:
        source = tuple(
            ledger_row[f"_dbw_s_{index}"] for index in range(len(source_columns))
        )
        target = tuple(
            ledger_row[f"_dbw_t_{index}"]
            for index in range(len(target_identity_columns))
        )
        values = tuple(
            ledger_row[f"_dbw_v_{index}"] for index in range(len(value_columns))
        )
        record = None
        record_key = None
        for key in keys.values():
            source_hash = _edge_identity_hash(key, "source", source)
            candidate = _find_edge(
                connection,
                migration_id,
                epoch,
                definition_id,
                source_hash,
                key["key_id"],
            )
            if candidate is not None:
                record, record_key = candidate, key
                break
        if record is None:
            raise ValueError("Verified identity-edge record is missing")
        _verify_edge(record, record_key)
        if record["source_identity_hash"] in seen:
            raise ValueError(
                "Duplicate normalized source identity in transition ledger"
            )
        if (
            record["target_identity_hash"]
            != _edge_identity_hash(record_key, "target", target)
            or record["value_hash"] != _edge_identity_hash(record_key, "values", values)
            or bool(record["owned_write"]) != bool(ledger_row["_dbw_owned"])
        ):
            raise ValueError("Verified identity edge does not match its ledger")
        if verify_target:
            _verify_target_values(
                connection,
                target_ref,
                target_key_columns,
                target,
                target_columns,
                record,
                record_key,
            )
        if definition.get("retained_source"):
            _verify_retained_source(connection, definition["retained_source"], source)
        seen.add(record["source_identity_hash"])
    if len(ledger_rows) != len(seen):
        raise ValueError("Identity-edge ledger row count is not identity-unique")
    stored = connection.execute(
        text(
            f"SELECT COUNT(*) FROM {_EDGES} WHERE migration_id = :migration_id "
            "AND epoch = :epoch AND definition_id = :definition_id"
        ),
        {
            "migration_id": migration_id,
            "epoch": epoch,
            "definition_id": definition_id,
        },
    ).scalar_one()
    if stored != len(seen):
        raise ValueError("Identity-edge record count does not match its ledger")
    if definition.get("retained_source"):
        ref = definition["retained_source"]["table"]
        source_table = ".".join(
            _edge_identifier(part, dialect)
            for part in (ref.get("schema"), ref["table"])
            if part
        )
        if connection.execute(
            text(f"SELECT COUNT(*) FROM {source_table}")
        ).scalar_one() != len(ledger_rows):
            raise ValueError("Retained merge source row count changed after apply")
    return {"verified": True, "count": len(seen), "definition_id": definition_id}


def _verify_retained_source(connection, proof, source):
    dialect = connection.dialect.name
    ref = proof["table"]
    table = ".".join(
        _edge_identifier(part, dialect)
        for part in (ref.get("schema"), ref["table"])
        if part
    )
    columns = ", ".join(_edge_identifier(name, dialect) for name in proof["columns"])
    keys = [source[index] for index in proof["key_indexes"]]
    where = " AND ".join(
        f"{_edge_identifier(name, dialect)} = :key_{index}"
        for index, name in enumerate(proof["key_columns"])
    )
    rows = connection.execute(
        text(f"SELECT {columns} FROM {table} WHERE {where}"),
        {f"key_{index}": value for index, value in enumerate(keys)},
    ).all()
    expected = [source[index] for index in proof["value_indexes"]]
    if len(rows) != 1 or canonical_bytes(list(rows[0])) != canonical_bytes(expected):
        raise ValueError(
            "Retained merge source changed after apply; rollback and convergence refused"
        )


def _verify_target_values(
    connection, target_ref, key_columns, target, value_columns, record, key
) -> None:
    schema = target_ref.get("schema")
    table_name = target_ref.get("table")
    if (
        not isinstance(table_name, str)
        or schema is not None
        and not isinstance(schema, str)
    ):
        raise TypeError("identity_edges target_table is invalid")
    qualified = f"{schema}.{table_name}" if schema else table_name
    dialect = connection.dialect.name
    where = " AND ".join(
        f"{_edge_identifier(name, dialect)} = :_dbw_key_{index}"
        for index, name in enumerate(key_columns)
    )
    selected = ", ".join(
        f"{_edge_identifier(name, dialect)} AS _dbw_value_{index}"
        for index, name in enumerate(value_columns)
    )
    rows = (
        connection.execute(
            text(
                f"SELECT {selected} FROM {_edge_identifier(qualified, dialect)} WHERE {where}"
            ),
            {f"_dbw_key_{index}": value for index, value in enumerate(target)},
        )
        .mappings()
        .all()
    )
    if len(rows) != 1:
        raise ValueError("Identity-edge target row is missing or duplicated")
    values = tuple(
        rows[0][f"_dbw_value_{index}"] for index in range(len(value_columns))
    )
    if record["value_hash"] != _edge_identity_hash(key, "values", values):
        raise ValueError("Identity-edge target values no longer match verified values")


def _edge_keys(connection):
    rows = (
        connection.execute(
            text(
                f"SELECT key_id, key_material, status, created_at FROM {_KEYS} "
                "ORDER BY created_at DESC, key_id DESC"
            )
        )
        .mappings()
        .all()
    )
    keys = [dict(row) for row in rows]
    for key in keys:
        material = key["key_material"]
        if not isinstance(material, str) or len(material) != 64:
            raise RuntimeError("Data-edge HMAC key material is invalid")
    return keys


def _find_edge(connection, migration_id, epoch, definition_id, source_hash, key_id):
    row = (
        connection.execute(
            text(
                f"SELECT migration_id, epoch, run_id, operation_id, definition_id, "
                "source_identity_hash, target_identity_hash, value_hash, owned_write, key_id, "
                "encoding_version, status, verification_checksum, created_at "
                f"FROM {_EDGES} WHERE migration_id = :migration_id AND epoch = :epoch "
                "AND definition_id = :definition_id AND source_identity_hash = :source_hash "
                "AND key_id = :key_id LIMIT 1"
            ),
            {
                "migration_id": migration_id,
                "epoch": epoch,
                "definition_id": definition_id,
                "source_hash": source_hash,
                "key_id": key_id,
            },
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _edge_identity_hash(key, role, values) -> str:
    payload = canonical_bytes(["dbwarden.identity-edge.v1", role, list(values)])
    return (
        "hmac-sha256:"
        + hmac.new(
            bytes.fromhex(key["key_material"]), payload, hashlib.sha256
        ).hexdigest()
    )


def _edge_record_id(migration_id, epoch, operation_id, definition_id, source_hash):
    return hashlib.sha256(
        canonical_bytes(
            [
                "dbwarden.identity-edge-key.v1",
                migration_id,
                epoch,
                operation_id,
                definition_id,
                source_hash,
            ]
        )
    ).hexdigest()


def _edge_checksum(record, key) -> str:
    payload = canonical_bytes(
        [
            "dbwarden.identity-edge-record.v1",
            {
                name: value
                for name, value in record.items()
                if name != "verification_checksum"
            },
        ]
    )
    return (
        "hmac-sha256:"
        + hmac.new(
            bytes.fromhex(key["key_material"]), payload, hashlib.sha256
        ).hexdigest()
    )


def _verify_edge(record, key) -> None:
    if record["encoding_version"] != 1 or record["status"] != "VERIFIED":
        raise RuntimeError("Identity-edge record metadata is invalid")
    if not hmac.compare_digest(
        record["verification_checksum"], _edge_checksum(record, key)
    ):
        raise RuntimeError("Identity-edge verification checksum is invalid")


def _edge_identifier(value: str, dialect: str) -> str:
    parts = value.split(".")
    if any(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) is None for part in parts):
        raise ValueError("Identity-edge metadata contains an unsafe identifier")
    marker = (
        "`" if dialect in {"mysql", "mariadb", "clickhouse", "clickhousedb"} else '"'
    )
    return ".".join(f"{marker}{part}{marker}" for part in parts)


def _create_journal(connection) -> None:
    connection.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_JOURNAL} ("
            "migration_id VARCHAR(255) NOT NULL, epoch INTEGER NOT NULL, checksum VARCHAR(128) NOT NULL, "
            "status VARCHAR(64) NOT NULL, run_id VARCHAR(64) NOT NULL, direction VARCHAR(16) NOT NULL, "
            "last_operation VARCHAR(255), error TEXT, "
            "baseline INTEGER NOT NULL DEFAULT 0, started_at VARCHAR(40) NOT NULL, finished_at VARCHAR(40), "
            "PRIMARY KEY (migration_id, epoch))"
        )
    )


def _create_checkpoints(connection) -> None:
    connection.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_CHECKPOINTS} ("
            "migration_id VARCHAR(255) NOT NULL, epoch INTEGER NOT NULL, run_id VARCHAR(64) NOT NULL, "
            "direction VARCHAR(16) NOT NULL, operation_id VARCHAR(255) NOT NULL, statement_index INTEGER NOT NULL, "
            "statement_checksum VARCHAR(128) NOT NULL, status VARCHAR(32) NOT NULL, "
            "started_at VARCHAR(40) NOT NULL, finished_at VARCHAR(40), "
            "PRIMARY KEY (migration_id, epoch, direction, operation_id, statement_index))"
        )
    )


def _create_events(connection) -> None:
    connection.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_EVENTS} ("
            "event_id VARCHAR(64) PRIMARY KEY, migration_id VARCHAR(255) NOT NULL, "
            "epoch INTEGER NOT NULL, run_id VARCHAR(64) NOT NULL, event_type VARCHAR(64) NOT NULL, "
            "status VARCHAR(64) NOT NULL, operation_id VARCHAR(255) NOT NULL, "
            "checksum VARCHAR(128) NOT NULL, details TEXT NOT NULL, "
            "created_at VARCHAR(40) NOT NULL)"
        )
    )


def _latest_run(connection, migration_id: str) -> dict[str, Any] | None:
    row = (
        connection.execute(
            text(
                f"SELECT migration_id, epoch, checksum, status, run_id, direction, last_operation, "
                f"error, baseline, started_at, finished_at "
                f"FROM {_JOURNAL} WHERE migration_id = :migration_id ORDER BY epoch DESC LIMIT 1"
            ),
            {"migration_id": migration_id},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _next_epoch(
    existing: dict[str, Any] | None,
    checksum: str,
    migration_id: str,
    *,
    reapply: bool = False,
) -> int:
    if existing is None:
        return 1
    if existing["checksum"] != checksum:
        raise ValueError(f"Data plan checksum mismatch for migration '{migration_id}'")
    if existing["status"] in {"APPLYING", _UNKNOWN}:
        raise RuntimeError(
            f"Data migration '{migration_id}' requires reconciliation before retry"
        )
    if existing["status"] in {"ABANDONED", "FAILED_FINAL"}:
        raise RuntimeError(
            f"Data migration '{migration_id}' is terminally {existing['status']}"
        )
    if existing["status"] == _ROLLBACK and not reapply:
        raise RuntimeError(
            f"Data migration '{migration_id}' was rolled back; explicit reapply is required"
        )
    return int(existing["epoch"]) + 1


def _insert_run(
    connection,
    migration_id: str,
    epoch: int,
    checksum: str,
    run_id: str,
    direction: str,
    baseline: bool,
) -> None:
    connection.execute(
        text(
            f"INSERT INTO {_JOURNAL} (migration_id, epoch, checksum, status, run_id, direction, "
            "baseline, started_at) VALUES (:migration_id, :epoch, :checksum, 'APPLYING', "
            ":run_id, :direction, :baseline, :started_at)"
        ),
        {
            "migration_id": migration_id,
            "epoch": epoch,
            "checksum": checksum,
            "run_id": run_id,
            "direction": direction,
            "baseline": int(baseline),
            "started_at": _now(),
        },
    )
    _append_event(
        connection,
        migration_id,
        epoch,
        run_id,
        "RUN_STARTED",
        "APPLYING",
        "",
        checksum,
        "baseline acknowledged" if baseline else "",
    )


def _restart_run(
    connection,
    migration_id: str,
    epoch: int,
    run_id: str,
    direction: str,
    checksum: str,
) -> None:
    connection.execute(
        text(
            f"UPDATE {_JOURNAL} SET status = 'APPLYING', run_id = :run_id, "
            "direction = :direction, error = NULL, "
            "started_at = :started_at, finished_at = NULL WHERE migration_id = :migration_id AND epoch = :epoch"
        ),
        {
            "run_id": run_id,
            "direction": direction,
            "started_at": _now(),
            "migration_id": migration_id,
            "epoch": epoch,
        },
    )
    _append_event(
        connection,
        migration_id,
        epoch,
        run_id,
        "RUN_RESUMED",
        "APPLYING",
        "",
        checksum,
        "",
    )


def _insert_checkpoint(
    connection,
    migration_id,
    epoch,
    run_id,
    direction,
    operation_id,
    statement_index,
    statement_checksum,
    *,
    status="PENDING",
) -> None:
    finished_at = _now() if status == "DONE" else None
    connection.execute(
        text(
            f"INSERT INTO {_CHECKPOINTS} (migration_id, epoch, run_id, direction, operation_id, "
            "statement_index, statement_checksum, status, started_at, finished_at) VALUES "
            "(:migration_id, :epoch, :run_id, :direction, :operation_id, :statement_index, "
            ":statement_checksum, :status, :started_at, :finished_at)"
        ),
        {
            "migration_id": migration_id,
            "epoch": epoch,
            "run_id": run_id,
            "direction": direction,
            "operation_id": operation_id,
            "statement_index": statement_index,
            "statement_checksum": statement_checksum,
            "status": status,
            "started_at": _now(),
            "finished_at": finished_at,
        },
    )


def _finish_checkpoint(
    connection,
    migration_id,
    epoch,
    direction,
    operation_id,
    statement_index,
    *,
    status="DONE",
) -> None:
    connection.execute(
        text(
            f"UPDATE {_CHECKPOINTS} SET status = :status, finished_at = :finished_at "
            "WHERE migration_id = :migration_id AND epoch = :epoch AND direction = :direction "
            "AND operation_id = :operation_id AND statement_index = :statement_index"
        ),
        {
            "status": status,
            "finished_at": _now(),
            "migration_id": migration_id,
            "epoch": epoch,
            "direction": direction,
            "operation_id": operation_id,
            "statement_index": statement_index,
        },
    )


def _checkpoint_rows(
    connection, migration_id, epoch, direction=None, operation_id=None, run_id=None
):
    clauses = ["migration_id = :migration_id", "epoch = :epoch"]
    parameters = {"migration_id": migration_id, "epoch": epoch}
    if direction is not None:
        clauses.append("direction = :direction")
        parameters["direction"] = direction
    if operation_id is not None:
        clauses.append("operation_id = :operation_id")
        parameters["operation_id"] = operation_id
    if run_id is not None:
        clauses.append("run_id = :run_id")
        parameters["run_id"] = run_id
    rows = (
        connection.execute(
            text(
                f"SELECT run_id, direction, operation_id, statement_index, statement_checksum, "
                f"status, started_at, finished_at "
                f"FROM {_CHECKPOINTS} WHERE {' AND '.join(clauses)} ORDER BY direction, operation_id, statement_index"
            ),
            parameters,
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def _set_last_operation(
    connection, migration_id: str, epoch: int, operation_id: str
) -> None:
    connection.execute(
        text(
            f"UPDATE {_JOURNAL} SET last_operation = :operation_id WHERE migration_id = :migration_id AND epoch = :epoch"
        ),
        {"operation_id": operation_id, "migration_id": migration_id, "epoch": epoch},
    )
    _append_current_event(
        connection,
        migration_id,
        epoch,
        "OPERATION_STARTED",
        "APPLYING",
        operation_id,
    )


def _set_run_direction(
    connection, migration_id: str, epoch: int, direction: str
) -> None:
    connection.execute(
        text(
            f"UPDATE {_JOURNAL} SET direction = :direction "
            "WHERE migration_id = :migration_id AND epoch = :epoch"
        ),
        {"direction": direction, "migration_id": migration_id, "epoch": epoch},
    )
    _append_current_event(
        connection, migration_id, epoch, "ROLLBACK_STARTED", "ROLLING_BACK", ""
    )


def _finish_run(
    connection,
    migration_id: str,
    epoch: int,
    status: str,
    *,
    error: str | None = None,
    baseline: bool,
) -> None:
    connection.execute(
        text(
            f"UPDATE {_JOURNAL} SET status = :status, error = :error, baseline = :baseline, finished_at = :finished_at "
            "WHERE migration_id = :migration_id AND epoch = :epoch"
        ),
        {
            "status": status,
            "error": error,
            "baseline": int(baseline),
            "finished_at": _now(),
            "migration_id": migration_id,
            "epoch": epoch,
        },
    )
    _append_current_event(
        connection,
        migration_id,
        epoch,
        "RUN_STATUS",
        status,
        "",
        "baseline acknowledged" if baseline else "",
    )


def _append_current_event(
    connection, migration_id, epoch, event_type, status, operation_id, details=""
) -> None:
    row = (
        connection.execute(
            text(
                f"SELECT run_id, checksum FROM {_JOURNAL} "
                "WHERE migration_id = :migration_id AND epoch = :epoch"
            ),
            {"migration_id": migration_id, "epoch": epoch},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise RuntimeError("Data run disappeared while appending an execution event")
    _append_event(
        connection,
        migration_id,
        epoch,
        row["run_id"],
        event_type,
        status,
        operation_id,
        row["checksum"],
        details,
    )


def _append_event(
    connection,
    migration_id,
    epoch,
    run_id,
    event_type,
    status,
    operation_id,
    checksum,
    details,
) -> None:
    connection.execute(
        text(
            f"INSERT INTO {_EVENTS} (event_id, migration_id, epoch, run_id, event_type, "
            "status, operation_id, checksum, details, created_at) VALUES "
            "(:event_id, :migration_id, :epoch, :run_id, :event_type, :status, "
            ":operation_id, :checksum, :details, :created_at)"
        ),
        {
            "event_id": str(uuid.uuid4()),
            "migration_id": migration_id,
            "epoch": epoch,
            "run_id": run_id,
            "event_type": event_type,
            "status": status,
            "operation_id": operation_id,
            "checksum": checksum,
            "details": details,
            "created_at": _now(),
        },
    )


def _append_baseline_event(connection, migration_id, epoch, details) -> None:
    _append_current_event(
        connection, migration_id, epoch, "BASELINE", _SUCCESS, "", details
    )


def _append_reapply_event(connection, migration_id, epoch, previous_epoch) -> None:
    _append_current_event(
        connection,
        migration_id,
        epoch,
        "REAPPLY",
        "APPLYING",
        "",
        _reapply_details(previous_epoch),
    )


def _baseline_details(plan, reason, direction) -> str:
    checks = []
    execution = plan.get("data_execution") or {}
    for step in execution.get(direction, []):
        operation_id = _operation_id(step)
        for guard in step.get("guards", []):
            checks.append(
                f"guard:{direction}:{operation_id}:"
                f"{guard.get('timing', 'before')}:{guard.get('probe_id', '<missing>')}"
            )
        edge = step.get("identity_edges")
        if edge:
            checks.append(
                f"identity_edges:{direction}:{operation_id}:"
                f"{edge.get('definition_id', '<missing>')}"
            )
    return json.dumps(
        {
            "acknowledged": True,
            "reason": reason.strip()
            if isinstance(reason, str) and reason.strip()
            else "explicit baseline requested",
            "skipped_checks": sorted(set(checks)),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _reapply_details(previous_epoch) -> str:
    return json.dumps(
        {"previous_epoch": int(previous_epoch)},
        separators=(",", ":"),
        sort_keys=True,
    )


def _persist_failure(
    connection,
    migration_id: str,
    checksum: str,
    run_id: str,
    error: str,
    baseline: bool,
    *,
    reapply: bool = False,
) -> None:
    with connection.begin():
        _create_journal(connection)
        if not _has_table(connection, _EVENTS):
            _create_events(connection)
        existing = _latest_run(connection, migration_id)
        epoch = int(existing["epoch"]) + 1 if existing else 1
        _insert_run(
            connection, migration_id, epoch, checksum, run_id, "upgrade", baseline
        )
        if existing and existing["status"] == _ROLLBACK and reapply:
            _append_reapply_event(
                connection, migration_id, epoch, int(existing["epoch"])
            )
        _finish_run(
            connection,
            migration_id,
            epoch,
            _RETRYABLE,
            error=f"transaction rolled back: {error}",
            baseline=baseline,
        )


def _has_rollback_proof(run: dict[str, Any]) -> bool:
    return isinstance(run.get("error"), str) and run["error"].startswith(
        "transaction rolled back:"
    )


def _has_nontransactional_proof(run: dict[str, Any]) -> bool:
    error = run.get("error")
    return isinstance(error, str) and error.startswith(
        ("no side effects:", "reconciled verified:")
    )


def _statement_checksum(
    direction: str, operation_id: str, index: int, statement: str
) -> str:
    raw = json.dumps([direction, operation_id, index, statement], separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _verify_plan_binding(connection, plan, migration_id):
    if plan.get("migration_id", migration_id) != migration_id:
        raise ValueError(
            "Data plan migration identity does not match the requested migration"
        )
    spec = plan.get("data_spec")
    if isinstance(spec, dict) and "spec_version" in spec:
        from .ir import validate_spec

        validate_spec(spec)
        actual = connection.dialect.name
        if actual == "mysql" and getattr(connection.dialect, "is_mariadb", False):
            actual = "mariadb"
        expected = spec["database"]["backend"]
        if actual != expected:
            raise ValueError(
                f"Data plan backend {expected} does not match connection backend {actual}"
            )


def _verify_backend_settings(connection, value: Any) -> None:
    settings = []

    def visit(item):
        if isinstance(item, dict):
            if item.get("determinism_class") == "backend_pinned":
                pinned = item.get("backend_settings")
                if not isinstance(pinned, dict):
                    raise TypeError(
                        "backend_pinned expression requires backend_settings"
                    )
                settings.append(pinned)
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    if not settings:
        return
    if connection.dialect.name != "postgresql":
        raise ValueError("backend_pinned expressions require PostgreSQL execution")
    actual = {
        "backend_version": connection.execute(text("SHOW server_version")).scalar_one(),
        "timezone": connection.execute(text("SHOW TimeZone")).scalar_one(),
        "search_path": connection.execute(text("SHOW search_path")).scalar_one(),
        "encoding": connection.execute(text("SHOW server_encoding")).scalar_one(),
        "collation": connection.execute(
            text(
                "SELECT datcollate FROM pg_database WHERE datname = current_database()"
            )
        ).scalar_one(),
    }
    for expected in settings:
        expected_version = str(expected.get("backend_version"))
        actual_version = str(actual["backend_version"])
        version_matches = (
            actual_version.split(".", 1)[0] == expected_version
            if expected_version.isdigit()
            else actual_version == expected_version
        )
        if not version_matches:
            raise ValueError("backend_pinned setting mismatch: backend_version")
        for key in ("timezone", "collation", "search_path", "encoding"):
            if str(expected.get(key)) != str(actual[key]):
                raise ValueError(f"backend_pinned setting mismatch: {key}")


def _verify_rollback_edges(connection, plan, migration_id, epoch, direction):
    if direction == "rollback":
        for step in plan["data_execution"]["upgrade"]:
            if step.get("identity_edges"):
                verify_identity_edges(connection, step, migration_id, epoch)


def _check_sqlite_drop_dependencies(connection, tables) -> None:
    if connection.dialect.name != "sqlite" or not tables:
        return
    import sqlglot
    from sqlglot import exp

    inspector = inspect(connection)
    for table in tables:
        schema = table.get("schema") or "main"
        target = table["table"].casefold()
        quoted_schema = '"' + schema.replace('"', '""') + '"'
        triggers = connection.execute(
            text(
                f"SELECT name FROM {quoted_schema}.sqlite_schema "
                "WHERE type = 'trigger' AND tbl_name = :table COLLATE NOCASE"
            ),
            {"table": table["table"]},
        ).all()
        if triggers:
            raise ValueError(
                "Rollback target has dependent triggers; remove them before dropping it"
            )
        for owner in inspector.get_table_names(schema=schema):
            if owner.casefold() != target and any(
                foreign_key["referred_table"].casefold() == target
                for foreign_key in inspector.get_foreign_keys(owner, schema=schema)
            ):
                raise ValueError(
                    "Rollback target has dependent foreign keys; remove them before dropping it"
                )
        for view_schema in {schema, "temp"}:
            for view in inspector.get_view_names(schema=view_schema):
                definition = inspector.get_view_definition(view, schema=view_schema)
                tree = sqlglot.parse_one(definition, read="sqlite")
                if any(
                    reference.name.casefold() == target
                    and (reference.db or schema).casefold() == schema.casefold()
                    for reference in tree.find_all(exp.Table)
                ):
                    raise ValueError(
                        "Rollback target has dependent views; remove them before dropping it"
                    )


def _lock_postgresql_tables(connection, plan: dict[str, Any]) -> None:
    if connection.dialect.name != "postgresql":
        return
    references = set()

    def visit(value):
        if isinstance(value, dict):
            table_name = value.get("table")
            if isinstance(table_name, str) and table_name:
                schema = value.get("schema")
                references.add(
                    (schema if isinstance(schema, str) else None, table_name)
                )
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    for kind in ("managed_rows", "transformations", "validations", "transitions"):
        for declaration in plan["data_spec"].get(kind, []):
            visit(declaration.get("target_table"))
            visit(declaration.get("source"))
            for target in declaration.get("targets", []):
                visit(target["target_table"])
    for operation in plan.get("upgrade_ops", []):
        visit(operation.get("runtime_source"))
    for steps in plan["data_execution"].values():
        for step in steps:
            visit(step.get("lock_tables"))
            visit(
                step.get("identity_edges", {}).get("retained_source", {}).get("table")
            )
    existing = []
    for schema, table_name in sorted(
        references, key=lambda item: (item[0] or "", item[1])
    ):
        parts = [part for part in (schema, table_name) if part is not None]
        if any("\x00" in part for part in parts):
            raise ValueError("PostgreSQL table identity contains NUL")
        qualified = ".".join('"' + part.replace('"', '""') + '"' for part in parts)
        if (
            connection.execute(
                text("SELECT to_regclass(:table_name)"), {"table_name": qualified}
            ).scalar_one()
            is not None
        ):
            existing.append(qualified)
    if existing:
        connection.execute(
            text(f"LOCK TABLE {', '.join(existing)} IN SHARE ROW EXCLUSIVE MODE")
        )


def _reject_plain_ignore(value: Any) -> None:
    if isinstance(value, dict):
        if (
            value.get("on_conflict") == "ignore"
            or value.get("conflict_policy") == "ignore"
        ):
            raise ValueError("Plain target-conflict ignore is unsupported")
        conflict = value.get("conflict")
        if isinstance(conflict, dict) and conflict.get("policy") == "ignore":
            raise ValueError("Plain target-conflict ignore is unsupported")
        for item in value.values():
            _reject_plain_ignore(item)
    elif isinstance(value, list):
        for item in value:
            _reject_plain_ignore(item)


def _has_table(connection, table_name: str, schema: str | None = None) -> bool:
    probe = getattr(connection, "_dbwarden_has_table", None)
    if probe is not None:
        return bool(
            probe(table_name)
            if schema is None
            else probe(table_name, schema=schema)
        )
    if connection.dialect.name in {"mysql", "mariadb"}:
        schema_sql = "DATABASE()" if schema is None else ":schema_name"
        parameters = {"table_name": table_name}
        if schema is not None:
            parameters["schema_name"] = schema
        return bool(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE "
                    f"table_schema = {schema_sql} AND table_name = :table_name"
                ),
                parameters,
            ).scalar_one()
        )
    inspector = inspect(connection)
    return (
        inspector.has_table(table_name)
        if schema is None
        else inspector.has_table(table_name, schema=schema)
    )


def _best_effort_rollback(connection) -> bool:
    try:
        connection.rollback()
    except SQLAlchemyError:
        return False
    return True


def _failure_text(exc: BaseException, operation_id: str | None) -> str:
    if isinstance(exc, DBAPIError):
        return str(_sanitized_database_error(exc, operation_id))
    return str(exc)


def _sanitized_database_error(
    exc: DBAPIError, operation_id: str | None
) -> DataExecutionError:
    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    location = f" in operation '{operation_id}'" if operation_id else ""
    state = f", SQLSTATE {sqlstate}" if sqlstate else ""
    return DataExecutionError(
        f"Data execution failed{location} ({type(exc).__name__}{state})"
    )


def _result(
    status: str,
    migration_id: str,
    epoch: int,
    checksum: str,
    run_id: str,
    *,
    baseline: bool = False,
) -> dict[str, Any]:
    return {
        "status": status,
        "migration_id": migration_id,
        "epoch": epoch,
        "checksum": checksum,
        "run_id": run_id,
        "baseline": baseline,
    }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
