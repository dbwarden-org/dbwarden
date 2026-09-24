from __future__ import annotations

from pathlib import Path

from .ir import parse_json


def filter_data_tables(snapshot, database=None):
    from dbwarden.config import get_database
    from dbwarden.constants import INTERNAL_TABLE_PREFIXES
    from dbwarden.exceptions import ConfigurationError

    archives = set()
    try:
        config = get_database(database)
        configured = getattr(config, "migrations_dir", None)
        directory = Path(configured) if configured is not None else None
        history_table = getattr(config, "migration_table", None)
        if isinstance(history_table, str):
            archives.add((getattr(config, "postgres_schema", None), history_table))
    except ConfigurationError:
        directory = None
    if directory is not None:
        for frozen in directory.glob("*.data.py"):
            plan = load_data_plan(frozen.with_suffix("").with_suffix(".sql"), database)
            if plan is None:
                continue
            spec = plan["data_spec"]
            destinations = [
                (item.get("archive") or {}).get("destination")
                for item in spec["managed_rows"]
            ]
            destinations += [
                destination
                for item in spec["transitions"]
                for destination in (
                    item["completion"].get("destination"),
                    item["coverage"].get("archive_destination"),
                )
            ]
            for ref in destinations:
                if ref:
                    archives.add((ref.get("schema"), ref["table"]))
    removed = {
        name
        for name, table in snapshot.get("tables", {}).items()
        if name.rsplit(".", 1)[-1].startswith(INTERNAL_TABLE_PREFIXES)
        or (table.get("schema"), name) in archives
        or (tuple(name.rsplit(".", 1)) if "." in name else (None, name)) in archives
    }
    result = dict(snapshot)
    result["tables"] = {
        name: value
        for name, value in snapshot.get("tables", {}).items()
        if name not in removed
    }
    for kind in ("indexes", "constraints"):
        if kind in snapshot:
            result[kind] = {
                name: value
                for name, value in snapshot[kind].items()
                if value.get("table") not in removed
            }
    return result


def load_data_plan(filename, db_name=None):
    path = Path(filename)
    if not path.is_file():
        from dbwarden.config import get_database

        path = Path(get_database(db_name).migrations_dir) / path.name
    if not path.is_file():
        return None
    content = path.read_text(encoding="utf-8")
    plan_path = path.with_suffix(".plan.json")
    marked = (
        "-- dbwarden: data-bundle" in content.splitlines()
        or path.with_suffix(".data.py").exists()
    )
    if not plan_path.exists():
        if marked:
            raise ValueError(
                "Data migration plan is missing; restore the complete frozen bundle"
            )
        return None
    try:
        plan = parse_json(plan_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        if marked:
            raise ValueError("Data migration plan is malformed") from None
        return None
    if not marked and "data_spec" not in plan:
        return None
    from dbwarden.engine.safety.plans import read_trusted_plan

    from .artifacts import verify_bundle
    from .ir import validate_spec

    trusted, reason = read_trusted_plan(path)
    if trusted is None:
        raise ValueError(f"Untrusted data migration: {reason}")
    verify_bundle(path, trusted)
    validate_spec(trusted["data_spec"])
    if db_name is not None and trusted["data_spec"]["database"]["name"] != db_name:
        raise ValueError("Data migration belongs to a different configured database")
    return trusted


def applied_data_spec(connection, paths, *, database, backend):
    from .execution import _ch_latest_run, _has_table, _latest_run, _plan_checksum
    from .ir import digest, empty_spec, seal_spec

    spec = empty_spec(database, backend)
    if not _has_table(connection, "_dbwarden_data_runs"):
        return spec
    runs = []
    latest = _ch_latest_run if backend == "clickhouse" else _latest_run
    for path in paths:
        plan = load_data_plan(path, database)
        if plan is None:
            continue
        run = latest(connection, plan["migration_id"])
        if not run or run["status"] != "APPLIED_SUCCESS" or run["baseline"]:
            continue
        if run["checksum"] != _plan_checksum(plan):
            raise ValueError(
                "Applied data migration checksum differs from frozen history"
            )
        runs.append(
            (run["finished_at"] or run["started_at"], plan["migration_id"], plan)
        )
    declarations = {}
    for _, _, plan in sorted(runs):
        for op in plan.get("upgrade_ops", []):
            if op["type"] == "declarative_data":
                declarations[(op["data_kind"], op["declaration_id"])] = op[
                    "data_declaration"
                ]
    for (kind, _), item in sorted(declarations.items()):
        spec.setdefault(kind, []).append(item)
    ids = sorted(key[1] for key in declarations)
    spec["declaration_set"] = {
        "declaration_ids": ids,
        "declaration_checksum": digest(
            sorted(declarations.values(), key=lambda item: item["declaration_id"]),
            "declarations",
        ),
    }
    return seal_spec(spec)


def execute_migration_bundle(
    connection,
    plan,
    *,
    version,
    filename,
    migration_type,
    direction,
    sql_statements,
    db_name,
    baseline=False,
    baseline_reason=None,
    reapply=False,
    before_statement=None,
    after_statement=None,
):
    from dbwarden.repositories.migrations_repo import _record_rollback, _record_upgrade

    from .execution import execute_data_plan

    def record():
        if direction == "upgrade":
            _record_upgrade(
                version=version,
                filename=filename,
                migration_type=migration_type,
                sql_statements=sql_statements,
                db_name=db_name,
                connection=connection,
            )
        else:
            _record_rollback(version=version, db_name=db_name, connection=connection)

    result = execute_data_plan(
        connection,
        plan,
        migration_id=Path(filename).stem,
        direction=direction,
        record_success=record,
        baseline=baseline,
        baseline_reason=baseline_reason,
        reapply=reapply,
        before_statement=before_statement,
        after_statement=after_statement,
    )
    connection.commit()
    return result
