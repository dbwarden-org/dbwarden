from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from .expressions import lower_expression
from .ir import digest
from .planning import _row_where, _same, quote, table_sql
from .sql import statement_text as text


def check_convergence(connection, spec, *, plans=()) -> list[dict]:
    from .execution import _has_table

    backend = spec["database"]["backend"]
    findings = []
    inspector = None if backend in {"mysql", "mariadb"} else inspect(connection)

    def has_table(name, schema=None):
        if inspector is None:
            return _has_table(connection, name, schema)
        return (
            inspector.has_table(name)
            if schema is None
            else inspector.has_table(name, schema=schema)
        )

    def probe(item, query, *, expected=0, message):
        count = connection.execute(text(query)).scalar_one()
        if count != expected:
            findings.append(
                {
                    "declaration_id": item["declaration_id"],
                    "kind": "data_drift",
                    "observed": count,
                    "expected": expected,
                    "message": message,
                }
            )

    for kind in ("managed_rows", "transformations", "validations"):
        for item in spec.get(kind, []):
            ref = item["target_table"]
            if not has_table(ref["table"], ref["schema"]):
                findings.append(
                    {
                        "declaration_id": item["declaration_id"],
                        "kind": "missing_table",
                        "message": "Target table does not exist",
                    }
                )
                continue
            table = table_sql(ref, backend)
            if kind == "managed_rows":
                for row in item["row_source"]["rows"]:
                    probe(
                        item,
                        f"SELECT COUNT(*) FROM {table} WHERE {_row_where(row, item['key_columns'] + item['owned_columns'], backend)}",
                        expected=1,
                        message="Managed row is missing or owned values differ",
                    )
                if item["on_missing"] == "delete":
                    ledger_name = (
                        "_dbwarden_rows_"
                        + digest(item["declaration_id"], "ownership")[:20]
                    )
                    if has_table(ledger_name):
                        keys = item["key_columns"]
                        join = " AND ".join(
                            _same(
                                "l." + quote(key, backend),
                                "t." + quote(key, backend),
                                backend,
                            )
                            for key in keys
                        )
                        desired = (
                            " OR ".join(
                                "(" + _row_where(row, keys, backend, "t.") + ")"
                                for row in item["row_source"]["rows"]
                            )
                            or "FALSE"
                        )
                        scope = lower_expression(
                            item["scope"],
                            backend,
                            aliases={ref["table"]: "t", "": "t"},
                        )
                        probe(
                            item,
                            f"SELECT COUNT(*) FROM {quote(ledger_name, backend)} l "
                            f"JOIN {table} t ON {join} WHERE ({scope}) "
                            f"AND NOT ({desired})",
                            message="Owned row remains after scoped deletion",
                        )
            elif kind == "transformations":
                if item.get("rollback", {}).get("policy") != "capture":
                    where = (
                        lower_expression(item["domain"], backend)
                        if item["domain"]
                        else "TRUE"
                    )
                    expected = lower_expression(item["expression"], backend)
                    probe(
                        item,
                        f"SELECT COUNT(*) FROM {table} WHERE ({where}) AND NOT {_same(quote(item['target_columns'][0], backend), expected, backend)}",
                        message="Derived values do not match the declaration",
                    )
                    if item["domain"] and item["on_unmatched"] == "error":
                        probe(
                            item,
                            f"SELECT COUNT(*) FROM {table} WHERE NOT ({where}) "
                            f"OR ({where}) IS NULL",
                            message="Rows fall outside the transformation domain",
                        )
            else:
                for validation in item["validations"]:
                    expression = lower_expression(validation["expression"], backend)
                    probe(
                        item,
                        f"SELECT COUNT(*) FROM {table} WHERE NOT "
                        f"{_same(expression, 'TRUE', backend)}",
                        message=validation["message"],
                    )
            receipt = (
                "archive"
                if kind == "managed_rows" and item.get("on_missing") == "archive"
                else "capture"
                if kind == "transformations"
                and item.get("rollback", {}).get("policy") == "capture"
                else None
            )
            if receipt is not None:
                _check_operation_receipts(
                    connection, plans, kind, item, receipt, findings
                )
    by_transition = {}
    for plan in plans:
        for op in plan.get("upgrade_ops", []):
            if op.get("data_kind") != "transitions":
                continue
            transition_id = op.get("data_declaration", {}).get("transition_id")
            if transition_id:
                by_transition.setdefault(transition_id, []).append((plan, op))
    for item in spec.get("transitions", []):
        candidates = by_transition.get(item["transition_id"], [])
        if not candidates:
            findings.append(
                {
                    "declaration_id": item["declaration_id"],
                    "kind": "unapplied_transition",
                    "message": "Transition has no frozen execution plan",
                }
            )
            continue
        if not has_table("_dbwarden_data_runs"):
            findings.append(
                {
                    "declaration_id": item["declaration_id"],
                    "kind": "unapplied_transition",
                    "message": "Transition has no execution journal",
                }
            )
            continue
        verified = []
        for plan, op in candidates:
            from .execution import _ch_latest_run, _latest_run, _plan_checksum

            latest = _ch_latest_run if backend == "clickhouse" else _latest_run
            run = latest(connection, plan["migration_id"])
            if (
                run
                and run["status"] == "APPLIED_SUCCESS"
                and not run["baseline"]
                and run["checksum"] == _plan_checksum(plan)
            ):
                verified.append((plan, op, run["epoch"]))
        if not verified:
            findings.append(
                {
                    "declaration_id": item["declaration_id"],
                    "kind": "unverified_transition",
                    "message": "Transition has no verified successful execution",
                }
            )
            continue
        plan, op, epoch = verified[-1]
        preserved = item["rollback"]["preservation_ref"]
        from .execution import verify_identity_edges

        try:
            proofs = [
                verify_identity_edges(connection, step, plan["migration_id"], epoch)
                for step in op["data_upgrade"]
                if step.get("identity_edges")
            ]
        except (SQLAlchemyError, TypeError, ValueError, RuntimeError):
            proofs = []
        if not proofs or any(not proof.get("verified") for proof in proofs):
            findings.append(
                {
                    "declaration_id": item["declaration_id"],
                    "kind": "unverifiable_transition",
                    "message": "Transition identity-edge proof is missing or invalid",
                }
            )
        if preserved is None:
            continue
        if not has_table(preserved, item["source"]["schema"]):
            findings.append(
                {
                    "declaration_id": item["declaration_id"],
                    "kind": "missing_preserved_source",
                    "message": "Preserved source is missing",
                }
            )
            continue
        import sqlglot
        from sqlglot import exp

        dialect = {"postgresql": "postgres", "mariadb": "mysql"}.get(backend, backend)
        runtime_source = (
            op.get("guard_source")
            or op.get("runtime_source")
            or op["data_declaration"]["source"]
        )
        for step in op["data_upgrade"]:
            for guard in step["guards"]:
                if guard.get("convergence") is False:
                    continue
                if guard["timing"] != "after" and not guard.get("convergence"):
                    continue
                query = sqlglot.parse_one(guard["query"], read=dialect)
                for table in query.find_all(exp.Table):
                    schema = table.db or None
                    if table.name == runtime_source[
                        "table"
                    ] and schema == runtime_source.get("schema"):
                        table.set("this", exp.Identifier(this=preserved, quoted=True))
                probe(
                    item,
                    query.sql(dialect=dialect),
                    message=guard["message"],
                )
    return findings


def _check_operation_receipts(connection, plans, kind, item, receipt, findings):
    from .execution import (
        _has_table,
        _latest_run,
        _plan_checksum,
        verify_identity_edges,
    )

    for plan in reversed(tuple(plans)):
        operations = [
            operation
            for operation in plan.get("upgrade_ops", [])
            if operation.get("data_kind") == kind
            and operation.get("data_declaration") == item
        ]
        if not operations:
            continue
        if not _has_table(connection, "_dbwarden_data_runs"):
            continue
        run = _latest_run(connection, plan["migration_id"])
        if (
            not run
            or run["status"] != "APPLIED_SUCCESS"
            or run["baseline"]
            or run["checksum"] != _plan_checksum(plan)
        ):
            continue
        steps = [
            step
            for operation in operations
            for step in operation.get("data_upgrade", [])
            if step.get("identity_edges")
        ]
        if receipt == "capture" and not steps:
            findings.append(
                {
                    "declaration_id": item["declaration_id"],
                    "kind": "unverifiable_capture",
                    "message": "Captured preimage receipt is missing from the applied plan",
                }
            )
            return
        try:
            proofs = [
                verify_identity_edges(
                    connection, step, plan["migration_id"], run["epoch"]
                )
                for step in steps
            ]
        except (SQLAlchemyError, TypeError, ValueError, RuntimeError):
            proofs = []
        if steps and (not proofs or any(not proof.get("verified") for proof in proofs)):
            findings.append(
                {
                    "declaration_id": item["declaration_id"],
                    "kind": f"unverifiable_{receipt}",
                    "message": (
                        "Archive receipt or archived postimage is missing or invalid"
                        if receipt == "archive"
                        else "Captured preimage receipt or derived postimage is missing or invalid"
                    ),
                }
            )
        return


def project_data_findings(database=None, *, offline=False):
    from pathlib import Path

    from dbwarden.commands.data import _compiled
    from dbwarden.connection.connection import get_db_connection
    from dbwarden.engine.version import (
        get_migration_filepaths_by_version,
        get_migrations_directory,
    )
    from dbwarden.merge.marker import is_superseded

    from .integration import load_data_plan

    _, _, spec, _ = _compiled(database, frozen_fallback=True)
    paths = get_migration_filepaths_by_version(get_migrations_directory(database))
    candidates = [path for path in paths.values() if not is_superseded(path)]
    candidates.extend(
        str(path) for path in sorted(Path(".dbwarden/reconciliations").glob("*/*.sql"))
    )
    plans = [
        plan
        for path in candidates
        if (plan := load_data_plan(path, database)) is not None
        and plan.get("data_spec", {}).get("database") == spec["database"]
    ]
    if offline:
        raise ValueError(
            "Offline mode cannot prove live data convergence; use data render for an offline declaration review"
        )
    with get_db_connection(database) as connection:
        return check_convergence(connection, spec, plans=plans)


def verify_sandbox_convergence(database):
    from dbwarden.config import get_database
    from dbwarden.connection.connection import get_db_connection
    from dbwarden.engine.model_discovery import (
        filter_model_tables_by_name,
        get_all_model_tables,
    )
    from dbwarden.engine.snapshot import diff_models_against_snapshot
    from dbwarden.engine.snapshot.extract import extract_full_schema_snapshot
    from dbwarden.engine.version import (
        get_migration_filepaths_by_version,
        get_migrations_directory,
    )
    from dbwarden.merge.marker import is_superseded
    from dbwarden.output import success

    from .integration import load_data_plan

    files = get_migration_filepaths_by_version(get_migrations_directory(database))
    plans = [
        plan
        for path in files.values()
        if not is_superseded(path)
        and (plan := load_data_plan(path, database)) is not None
    ]
    if not plans:
        return
    with get_db_connection(database) as connection:
        findings = check_convergence(connection, plans[-1]["data_spec"], plans=plans)
    if findings:
        raise ValueError(
            "Sandbox data convergence failed: "
            + "; ".join(item["message"] for item in findings)
        )
    config = get_database(database)
    if not config.model_paths:
        raise ValueError("Sandbox schema convergence requires current model_paths")
    tables = filter_model_tables_by_name(
        get_all_model_tables(config.model_paths, db_name=database), config.model_tables
    )
    snapshot = extract_full_schema_snapshot(database=database)
    upgrade, _ = diff_models_against_snapshot(
        tables, snapshot, database=database, db_name=database
    )
    if upgrade:
        raise ValueError(
            "Sandbox schema convergence failed after data migration replay"
        )
    success("Sandbox schema and frozen data converged.")
