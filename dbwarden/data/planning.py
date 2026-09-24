from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal

from .expressions import canonical_expression, literal, lower_expression
from .ir import canonical_bytes, digest, validate_spec


def quote(name, backend):
    marker = "`" if backend in {"mysql", "mariadb", "clickhouse"} else '"'
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ValueError("Invalid SQL identifier")
    return marker + name.replace(marker, marker * 2) + marker


def table_sql(ref, backend):
    return ".".join(
        quote(name, backend) for name in (ref.get("schema"), ref["table"]) if name
    )


def decode_value(value):
    if isinstance(value, dict) and len(value) == 1:
        for tag, convert in (
            ("$decimal", Decimal),
            ("$float", float),
            ("$date", date.fromisoformat),
            ("$datetime", datetime.fromisoformat),
        ):
            if tag in value:
                return convert(value[tag])
    return value


def value_sql(value, backend):
    return lower_expression(
        canonical_expression(literal(decode_value(value)), columns=[], backend=backend),
        backend,
    )


def _guard(
    query,
    message,
    *,
    maximum=0,
    minimum=None,
    timing="before",
    convergence=None,
    dry_run=True,
    dry_run_query=None,
):
    guard = {
        "probe_id": digest([query, maximum, minimum, timing], "probe"),
        "kind": "count",
        "query": query,
        "maximum": maximum,
        "minimum": minimum,
        "message": message,
        "timing": timing,
        "threshold": {"maximum": maximum, "minimum": minimum},
        "failure_policy": "abort",
        "required": True,
    }
    if convergence is not None:
        guard["convergence"] = convergence
    if not dry_run:
        guard["dry_run"] = False
    if dry_run_query is not None:
        guard["dry_run_query"] = dry_run_query
    return guard


def _same(left, right, backend):
    if backend == "clickhouse":
        return f"isNotDistinctFrom({left}, {right})"
    return (
        f"({left} <=> {right})"
        if backend in {"mysql", "mariadb"}
        else f"({left} IS NOT DISTINCT FROM {right})"
    )


def _delete(table, alias, where, backend):
    if backend == "mariadb":
        return f"DELETE {alias} FROM {table} AS {alias} WHERE {where};"
    return f"DELETE FROM {table} AS {alias} WHERE {where};"


def _row_where(row, keys, backend, prefix=""):
    return " AND ".join(
        _same(prefix + quote(key, backend), value_sql(row[key], backend), backend)
        for key in keys
    )


def _new_step(sql=(), guards=()):
    return {"sql": list(sql), "guards": list(guards)}


def _mysql_table_exists(ref, backend):
    schema = value_sql(ref["schema"], backend) if ref.get("schema") else "DATABASE()"
    return (
        "SELECT COUNT(*) FROM information_schema.tables WHERE "
        f"table_schema = {schema} AND table_name = {value_sql(ref['table'], backend)}"
    )


def _mysql_rename_step(source_ref, target_ref, backend):
    source = table_sql(source_ref, backend)
    target = table_sql(target_ref, backend)
    before = [
        _guard(
            _mysql_table_exists(source_ref, backend),
            "Transition source is unavailable",
            minimum=1,
            maximum=1,
            dry_run=False,
        ),
        _guard(
            _mysql_table_exists(target_ref, backend),
            "Transition internal source already exists",
            maximum=0,
            dry_run=False,
        ),
    ]
    after = [
        _guard(
            _mysql_table_exists(source_ref, backend),
            "Transition source rename did not retire the prior name",
            maximum=0,
            timing="after",
            dry_run=False,
        ),
        _guard(
            _mysql_table_exists(target_ref, backend),
            "Transition source rename did not create the expected name",
            minimum=1,
            maximum=1,
            timing="after",
            dry_run=False,
        ),
    ]
    for guard in before + after:
        guard["convergence"] = False
    return _new_step([f"RENAME TABLE {source} TO {target};"], before + after)


def _managed(item, previous, backend):
    table = table_sql(item["target_table"], backend)
    keys, owned = item["key_columns"], item["owned_columns"]
    values = item["row_source"]["rows"]
    old_values = (previous or {}).get("row_source", {}).get("rows", [])
    if previous and (
        previous["key_columns"] != keys or previous["owned_columns"] != owned
    ):
        raise ValueError(
            "Managed-row key or ownership changes require a new declaration ID"
        )
    if backend == "clickhouse" and item["on_missing"] in {"delete", "archive"}:
        raise ValueError(
            "ClickHouse managed-row deletion is unsupported because ownership cannot be proven"
        )
    ledger = quote(
        "_dbwarden_rows_" + digest(item["declaration_id"], "ownership")[:20], backend
    )
    key_sql = ", ".join(quote(name, backend) for name in keys)
    up, down = (
        [
            f"CREATE TABLE IF NOT EXISTS {ledger} AS SELECT {key_sql} FROM {table} WHERE FALSE;"
        ],
        [],
    )
    key_guard = _guard(
        f"SELECT COUNT(*) FROM {table} WHERE "
        + " OR ".join(f"{quote(key, backend)} IS NULL" for key in keys),
        "Managed-row key contains NULL",
    )
    guards = [key_guard]
    old_by_key = {
        canonical_bytes([row[name] for name in keys]): row for row in old_values
    }
    insert_proofs = []
    for row in values:
        where = _row_where(row, keys, backend)
        columns = keys + owned
        row_key = canonical_bytes([row[name] for name in keys])
        prior = old_by_key.get(row_key)
        new_key = prior is None
        if backend != "clickhouse":
            up.append(
                f"INSERT INTO {ledger} ({key_sql}) SELECT "
                + ", ".join(value_sql(row[name], backend) for name in keys)
                + f" WHERE NOT EXISTS (SELECT 1 FROM {table} WHERE {where}) AND NOT EXISTS (SELECT 1 FROM {ledger} WHERE {where});"
            )
        if item["rollback"]["policy"] == "restore_previous" and new_key:
            guards.append(
                _guard(
                    f"SELECT COUNT(*) FROM {table} WHERE {where}",
                    "Reversible managed rows must not replace pre-existing rows",
                )
            )
        if backend in {"postgresql", "sqlite"}:
            conflict = (
                "DO UPDATE SET "
                + ", ".join(
                    f"{quote(name, backend)} = excluded.{quote(name, backend)}"
                    for name in owned
                )
                if owned
                else "DO NOTHING"
            )
            up.append(
                f"INSERT INTO {table} ({', '.join(quote(name, backend) for name in columns)}) VALUES ("
                + ", ".join(value_sql(row[name], backend) for name in columns)
                + f") ON CONFLICT ({key_sql}) {conflict};"
            )
        elif owned and prior is not None:
            expected = _row_where(prior, keys + owned, backend)
            desired = _row_where(row, keys + owned, backend)
            up.append(
                f"UPDATE {table} SET "
                + ", ".join(
                    f"{quote(name, backend)} = {value_sql(row[name], backend)}"
                    for name in owned
                )
                + f" WHERE ({expected}) AND NOT ({desired});"
            )
        if backend not in {"postgresql", "sqlite"}:
            up.append(
                f"INSERT INTO {table} ({', '.join(quote(name, backend) for name in columns)}) SELECT "
                + ", ".join(value_sql(row[name], backend) for name in columns)
                + f" WHERE NOT EXISTS (SELECT 1 FROM {table} WHERE {where});"
            )
            if backend in {"mysql", "mariadb"} and new_key:
                proof = {"statement_index": len(up) - 1}
                if item["rollback"]["policy"] == "restore_previous":
                    proof["expected"] = 1
                else:
                    proof["query"] = f"SELECT COUNT(*) FROM {ledger} WHERE {where}"
                insert_proofs.append(proof)
        guards.append(
            _guard(
                f"SELECT COUNT(*) FROM {table} WHERE {_row_where(row, keys + owned, backend)}",
                "Managed row does not converge",
                maximum=1,
                minimum=1,
                timing="after",
            )
        )
    wanted = {canonical_bytes([row[key] for key in keys]) for row in values}
    removed = [
        row
        for row in old_values
        if canonical_bytes([row[key] for key in keys]) not in wanted
    ]
    archive_down = []
    archive_down_guards = []
    archive_prefix = []
    archive_cleanup = None
    if item["on_missing"] == "delete":
        scope = lower_expression(item["scope"], backend)
        for row in removed:
            where = _row_where(row, keys, backend)
            equivalent = _row_where(row, keys + owned, backend)
            guards.append(
                _guard(
                    f"SELECT COUNT(*) FROM {table} WHERE ({where}) AND NOT ({equivalent})",
                    "Owned row changed since its last declaration; refusing delete",
                )
            )
            up.append(
                f"DELETE FROM {table} WHERE ({equivalent}) AND ({scope}) AND EXISTS (SELECT 1 FROM {ledger} WHERE {where});"
            )
            guards.append(
                _guard(
                    f"SELECT COUNT(*) FROM {table} WHERE ({where}) "
                    f"AND EXISTS (SELECT 1 FROM {ledger} WHERE {where})",
                    "Owned row was not deleted",
                    timing="after",
                )
            )
    elif item["on_missing"] == "archive":
        archive = item.get("archive") or {}
        destination_ref = archive.get("destination")
        columns = archive.get("columns") or []
        if not destination_ref or not columns:
            raise ValueError("Managed-row archive metadata is incomplete")
        destination = table_sql(destination_ref, backend)
        archive_ledger_name = (
            "_dbwarden_archive_" + digest([item, previous], "archive")[:20]
        )
        archive_ledger = quote(archive_ledger_name, backend)
        q = lambda name: quote(name, backend)
        source_order = keys + [name for name in columns if name not in keys]
        source_index = {name: index for index, name in enumerate(source_order)}
        archive_prefix.append(
            f"CREATE TABLE IF NOT EXISTS {destination} AS SELECT {', '.join(q(name) for name in columns)} FROM {table} WHERE FALSE;"
        )
        for row in removed:
            where = _row_where(row, keys, backend)
            equivalent = _row_where(row, keys + owned, backend)
            scope = lower_expression(item["scope"], backend)
            selected = (
                [
                    f"t.{q(name)} AS {q('s_' + str(i))}"
                    for i, name in enumerate(source_order)
                ]
                + [f"t.{q(name)} AS {q('t_' + str(i))}" for i, name in enumerate(keys)]
                + [
                    f"t.{q(name)} AS {q('v_' + str(i))}"
                    for i, name in enumerate(columns)
                ]
                + [f"1 AS {q('owned_write')}"]
            )
            capture = (
                f"SELECT {', '.join(selected)} FROM {table} t WHERE ({equivalent}) AND ({scope}) "
                f"AND EXISTS (SELECT 1 FROM {ledger} l WHERE {_row_where(row, keys, backend, 'l.')})"
            )
            up.extend(
                [
                    f"CREATE TABLE IF NOT EXISTS {archive_ledger} AS SELECT * FROM ({capture}) e WHERE FALSE;",
                    f"INSERT INTO {archive_ledger} {capture};",
                    f"INSERT INTO {destination} ({', '.join(q(name) for name in columns)}) SELECT "
                    + ", ".join(f"e.{q('v_' + str(i))}" for i in range(len(columns)))
                    + f" FROM {archive_ledger} e WHERE NOT EXISTS (SELECT 1 FROM {destination} a WHERE "
                    + " AND ".join(
                        _same(f"a.{q(name)}", f"e.{q('t_' + str(i))}", backend)
                        for i, name in enumerate(keys)
                    )
                    + ");",
                    _delete(
                        table,
                        "t",
                        f"({equivalent}) AND EXISTS (SELECT 1 FROM {archive_ledger} e WHERE "
                        + " AND ".join(
                            _same(f"t.{q(name)}", f"e.{q('v_' + str(i))}", backend)
                            for i, name in enumerate(columns)
                        )
                        + f") AND EXISTS (SELECT 1 FROM {destination} a JOIN "
                        + f"{archive_ledger} e ON "
                        + " AND ".join(
                            _same(f"a.{q(name)}", f"e.{q('v_' + str(i))}", backend)
                            for i, name in enumerate(columns)
                        )
                        + ")",
                        backend,
                    ),
                ]
            )
            archive_match = " AND ".join(
                _same(f"a.{q(name)}", f"e.{q('v_' + str(i))}", backend)
                for i, name in enumerate(columns)
            )
            key_edge = " AND ".join(
                _same(f"t.{q(name)}", f"e.{q('t_' + str(i))}", backend)
                for i, name in enumerate(keys)
            )
            guards.extend(
                [
                    _guard(
                        f"SELECT COUNT(*) FROM {destination} a WHERE "
                        + _row_where(row, keys, backend, "a."),
                        "Managed-row archive identity already exists",
                    ),
                    _guard(
                        f"SELECT COUNT(*) FROM {archive_ledger} e WHERE NOT EXISTS (SELECT 1 FROM {destination} a WHERE {archive_match})",
                        "Managed row was not archived",
                        timing="after",
                    ),
                    _guard(
                        f"SELECT COUNT(*) FROM {archive_ledger} e JOIN {table} t ON {key_edge}",
                        "Archived managed row remains in the source table",
                        timing="after",
                    ),
                ]
            )
            archive_down.append(
                f"INSERT INTO {table} ({', '.join(q(name) for name in columns)}) SELECT "
                + ", ".join(
                    f"e.{q('s_' + str(source_index[name]))}" for name in columns
                )
                + f" FROM {archive_ledger} e WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {key_edge});"
            )
            archive_down.append(
                _delete(
                    destination,
                    "a",
                    f"EXISTS (SELECT 1 FROM {archive_ledger} e WHERE {archive_match}) "
                    f"AND EXISTS (SELECT 1 FROM {table} t JOIN {archive_ledger} e ON {key_edge} WHERE "
                    + " AND ".join(
                        _same(
                            f"t.{q(name)}",
                            f"e.{q('s_' + str(source_index[name]))}",
                            backend,
                        )
                        for name in columns
                    )
                    + ")",
                    backend,
                )
            )
            archive_down_guards.extend(
                [
                    _guard(
                        f"SELECT COUNT(*) FROM {archive_ledger} e WHERE NOT EXISTS (SELECT 1 FROM {destination} a WHERE {archive_match})",
                        "Archived managed row changed before rollback",
                    ),
                    _guard(
                        f"SELECT COUNT(*) FROM {archive_ledger} e WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {key_edge} AND "
                        + " AND ".join(
                            _same(
                                f"t.{q(name)}",
                                f"e.{q('s_' + str(source_index[name]))}",
                                backend,
                            )
                            for name in columns
                        )
                        + ")",
                        "Archived managed row was not restored",
                        timing="after",
                    ),
                ]
            )
    policy = item["rollback"]["policy"]
    if policy == "restore_previous":
        down_guards = []
        for row in values:
            where = _row_where(row, keys, backend)
            equivalent = _row_where(row, keys + owned, backend)
            down_guards.append(
                _guard(
                    f"SELECT COUNT(*) FROM {table} WHERE ({where}) AND NOT ({equivalent})",
                    "Managed row changed after apply; rollback refused",
                )
            )
        current_by_key = {
            canonical_bytes([row[key] for key in keys]): row for row in values
        }
        for row in values:
            if canonical_bytes([row[key] for key in keys]) not in old_by_key:
                where = _row_where(row, keys, backend)
                down.append(
                    f"DELETE FROM {table} WHERE {_row_where(row, keys + owned, backend)} "
                    f"AND EXISTS (SELECT 1 FROM {ledger} WHERE {where});"
                )
                down.append(
                    f"DELETE FROM {ledger} WHERE ({where}) AND NOT EXISTS "
                    f"(SELECT 1 FROM {table} t WHERE {_row_where(row, keys, backend, 't.')});"
                )
                down_guards.append(
                    _guard(
                        f"SELECT COUNT(*) FROM {table} WHERE {where}",
                        "Managed row changed during rollback; delete was skipped",
                        timing="after",
                    )
                )
        for row in old_values:
            if item["on_missing"] == "archive" and row in removed:
                continue
            where = _row_where(row, keys, backend)
            row_key = canonical_bytes([row[key] for key in keys])
            current = current_by_key.get(row_key)
            expected = current
            if current is None and item["on_missing"] != "delete":
                expected = row
            if owned and expected is not None:
                down.append(
                    f"UPDATE {table} SET "
                    + ", ".join(
                        f"{quote(name, backend)} = {value_sql(row[name], backend)}"
                        for name in owned
                    )
                    + f" WHERE ({_row_where(expected, keys + owned, backend)}) "
                    f"AND NOT ({_row_where(row, keys + owned, backend)});"
                )
            down.append(
                f"INSERT INTO {table} ({', '.join(quote(name, backend) for name in keys + owned)}) SELECT "
                + ", ".join(value_sql(row[name], backend) for name in keys + owned)
                + f" WHERE NOT EXISTS (SELECT 1 FROM {table} WHERE {where});"
            )
            down_guards.append(
                _guard(
                    f"SELECT COUNT(*) FROM {table} WHERE {_row_where(row, keys + owned, backend)}",
                    "Previous managed row was not restored",
                    maximum=1,
                    minimum=1,
                    timing="after",
                )
            )
        if previous is not None:
            for row in old_values:
                guards.append(
                    _guard(
                        f"SELECT COUNT(*) FROM {table} WHERE {_row_where(row, keys + owned, backend)}",
                        "Cannot prove previous managed-row values for reversible update",
                        maximum=1,
                        minimum=1,
                    )
                )
        if archive_down:
            down.extend(archive_down)
            archive_cleanup = f"DROP TABLE {archive_ledger};"
            down_guards.extend(archive_down_guards)
    else:
        down_guards = []
    upgrade_step = _new_step(up, guards)
    if item["on_missing"] == "archive" and removed:
        upgrade_step["identity_edges"] = {
            "definition_id": item["archive"]["identity_edge"],
            "ledger_table": archive_ledger_name,
            "source_columns": [
                "s_" + str(i) for i in range(len(item["archive"]["columns"]))
            ],
            "source_key_count": len(keys),
            "target_identity_columns": ["t_" + str(i) for i in range(len(keys))],
            "value_columns": [
                "v_" + str(i) for i in range(len(item["archive"]["columns"]))
            ],
            "target_table": item["archive"]["destination"],
            "target_columns": item["archive"]["columns"],
            "target_key_columns": keys,
            "owned_column": "owned_write",
        }
    if insert_proofs:
        upgrade_step["insert_proofs"] = insert_proofs
    return (
        ([_new_step(archive_prefix)] if archive_prefix else []) + [upgrade_step],
        [_new_step(down, down_guards)]
        + ([_new_step([archive_cleanup])] if archive_cleanup else []),
        "CRITICAL"
        if item["on_missing"] in {"delete", "archive"} and removed
        else "INFO",
    )


def _transformation(item, previous, backend):
    table = table_sql(item["target_table"], backend)
    target = quote(item["target_columns"][0], backend)
    expression = lower_expression(item["expression"], backend)
    source_expression = lower_expression(
        item.get("source_expression", item["expression"]), backend
    )
    where = lower_expression(item["domain"], backend) if item["domain"] else "TRUE"
    if previous is None:
        expected = f"{target} IS NULL"
    else:
        old_expression = lower_expression(previous["expression"], backend)
        old_domain = (
            lower_expression(previous["domain"], backend)
            if previous["domain"]
            else "TRUE"
        )
        expected = (
            f"(({old_domain}) AND {_same(target, old_expression, backend)}) OR "
            f"(((NOT ({old_domain})) OR ({old_domain}) IS NULL) AND {target} IS NULL)"
        )
    guards = []
    if source_expression != expression:
        guards.append(
            _guard(
                f"SELECT COUNT(*) FROM {table} WHERE ({where}) AND NOT {_same(source_expression, expression, backend)}",
                "Transformation target cast would lose information",
            )
        )
    if item["domain"] and item["on_unmatched"] == "error":
        guards.append(
            _guard(
                f"SELECT COUNT(*) FROM {table} WHERE NOT ({where}) OR ({where}) IS NULL",
                "Rows fall outside the transformation domain",
            )
        )
    if item["max_rows"] is not None:
        guards.append(
            _guard(
                f"SELECT COUNT(*) FROM {table} WHERE {where}",
                "Transformation row limit exceeded",
                maximum=item["max_rows"],
            )
        )
    rollback = []
    policy = item["rollback"]["policy"]
    if policy == "capture":
        if backend == "clickhouse":
            raise ValueError("ClickHouse cannot authenticate transformation preimages")
        keys = item.get("key_columns") or []
        if not keys:
            raise ValueError("capture rollback requires a target primary key")
        q = lambda name: quote(name, backend)
        ledger_name = item["rollback"]["capture_ref"]
        ledger = q(ledger_name)
        source_columns = ["s_" + str(i) for i in range(len(keys) + 1)]
        target_columns = ["t_" + str(i) for i in range(len(keys))]
        select = [f"{q(name)} AS {q('s_' + str(i))}" for i, name in enumerate(keys)]
        select.append(f"{target} AS {q('s_' + str(len(keys)))}")
        select.extend(f"{q(name)} AS {q('t_' + str(i))}" for i, name in enumerate(keys))
        select.extend([f"{expression} AS {q('v_0')}", f"0 AS {q('owned_write')}"])
        capture = (
            f"SELECT {', '.join(select)} FROM {table} WHERE ({where}) "
            f"AND NOT {_same(target, expression, backend)}"
        )
        edge = " AND ".join(
            _same(f"t.{q(name)}", f"e.{q('t_' + str(i))}", backend)
            for i, name in enumerate(keys)
        )
        old_value = f"e.{q('s_' + str(len(keys)))}"
        write = (
            f"UPDATE {table} AS t SET {target} = (SELECT e.{q('v_0')} FROM {ledger} e WHERE {edge}) "
            f"WHERE EXISTS (SELECT 1 FROM {ledger} e WHERE ({edge}) AND {_same('t.' + target, old_value, backend)});"
        )
        step = _new_step(
            [
                f"CREATE TABLE {ledger} AS SELECT * FROM ({capture}) c WHERE FALSE;",
                f"INSERT INTO {ledger} {capture};",
                write,
            ],
            guards
            + [
                _guard(
                    f"SELECT COUNT(*) FROM {ledger} e JOIN {table} t ON {edge} WHERE NOT {_same('t.' + target, 'e.' + q('v_0'), backend)}",
                    "Captured transformation write did not converge",
                    timing="after",
                )
            ],
        )
        step["identity_edges"] = {
            "definition_id": digest(item["declaration_id"], "capture-edge"),
            "ledger_table": ledger_name,
            "source_columns": source_columns,
            "source_key_count": len(keys),
            "target_identity_columns": target_columns,
            "value_columns": ["v_0"],
            "target_table": item["target_table"],
            "target_columns": item["target_columns"],
            "target_key_columns": keys,
            "owned_column": "owned_write",
        }
        restore = (
            f"UPDATE {table} AS t SET {target} = (SELECT {old_value} FROM {ledger} e WHERE {edge}) "
            f"WHERE EXISTS (SELECT 1 FROM {ledger} e WHERE ({edge}) AND {_same('t.' + target, 'e.' + q('v_0'), backend)});"
        )
        rollback = [
            _new_step(
                [restore],
                [
                    _guard(
                        f"SELECT COUNT(*) FROM {ledger} e JOIN {table} t ON {edge} WHERE NOT {_same('t.' + target, 'e.' + q('v_0'), backend)}",
                        "Derived values changed after apply; rollback refused",
                    ),
                    _guard(
                        f"SELECT COUNT(*) FROM {ledger} e JOIN {table} t ON {edge} WHERE NOT {_same('t.' + target, old_value, backend)}",
                        "Captured transformation rollback did not converge",
                        timing="after",
                    ),
                ],
            ),
            _new_step([f"DROP TABLE {ledger};"]),
        ]
        return [step], rollback, "WARN"
    if policy == "clear":
        guards.append(
            _guard(
                f"SELECT COUNT(*) FROM {table} WHERE ({where}) AND {target} IS NOT NULL",
                "clear rollback requires an initially empty target domain",
            )
        )
        rollback.append(
            _new_step(
                [
                    (
                        f"UPDATE {table} SET {target} = NULL WHERE ({where}) "
                        f"AND {_same(target, expression, backend)} "
                        f"AND {target} IS NOT NULL;"
                    )
                ],
                [
                    _guard(
                        f"SELECT COUNT(*) FROM {table} WHERE ({where}) AND NOT {_same(target, expression, backend)}",
                        "Derived values changed after apply; rollback refused",
                    ),
                    _guard(
                        f"SELECT COUNT(*) FROM {table} WHERE ({where}) AND {target} IS NOT NULL",
                        "Transformation clear rollback did not converge",
                        timing="after",
                    ),
                ],
            )
        )
    elif policy == "recompute":
        if previous is None:
            raise ValueError(
                "recompute rollback requires a previous frozen transformation"
            )
        old = lower_expression(previous["expression"], backend)
        old_where = (
            lower_expression(previous["domain"], backend)
            if previous["domain"]
            else "TRUE"
        )
        if old_where != where:
            raise ValueError(
                "recompute rollback requires an unchanged transformation domain"
            )
        guards.append(
            _guard(
                f"SELECT COUNT(*) FROM {table} WHERE ({where}) AND NOT {_same(target, old, backend)}",
                "Previous transformation no longer holds; cannot prove rollback",
            )
        )
        rollback.append(
            _new_step(
                [
                    (
                        f"UPDATE {table} SET {target} = {old} WHERE ({where}) "
                        f"AND {_same(target, expression, backend)} "
                        f"AND NOT {_same(target, old, backend)};"
                    )
                ],
                [
                    _guard(
                        f"SELECT COUNT(*) FROM {table} WHERE ({where}) AND NOT {_same(target, expression, backend)}",
                        "Derived values changed after apply; rollback refused",
                    ),
                    _guard(
                        f"SELECT COUNT(*) FROM {table} WHERE ({where}) AND NOT {_same(target, old, backend)}",
                        "Transformation recompute rollback did not converge",
                        timing="after",
                    ),
                ],
            )
        )
    return (
        [
            _new_step(
                [
                    (
                        f"UPDATE {table} SET {target} = {expression} WHERE ({where}) "
                        f"AND ({expected}) "
                        f"AND NOT {_same(target, expression, backend)};"
                    )
                ],
                guards
                + [
                    _guard(
                        f"SELECT COUNT(*) FROM {table} WHERE ({where}) AND NOT {_same(target, expression, backend)}",
                        "Transformation write did not converge",
                        timing="after",
                    )
                ],
            )
        ],
        rollback,
        "WARN",
    )


def _transition_runtime_source(item, previous):
    if previous is None:
        return item["source"]
    if previous.get("transition_id") == item.get("transition_id"):
        raise ValueError("Transition ID was reused for changed semantics")
    if previous.get("source") != item.get("source"):
        raise ValueError("A transition revision must retain its logical source table")
    if previous.get("completion", {}).get("policy") == "keep":
        return item["source"]
    preserved = previous.get("rollback", {}).get("preservation_ref")
    if (
        previous.get("completion", {}).get("policy") not in {"preserve", "archive"}
        or not preserved
    ):
        raise ValueError(
            "A transition revision requires the prior source to be preserved"
        )
    return {**item["source"], "table": preserved}


def _transition(item, previous, backend):
    if "merge" in item:
        from .advanced import plan_merge

        return plan_merge(item, previous, backend)
    runtime_source = _transition_runtime_source(item, previous)
    live_source = table_sql(runtime_source, backend)
    frozen_ref = None
    freeze_step = None
    if backend in {"mysql", "mariadb"}:
        frozen_ref = {
            **runtime_source,
            "table": "_dbwarden_frozen_" + item["transition_id"][:16],
        }
        freeze_step = _mysql_rename_step(runtime_source, frozen_ref, backend)
    active_source = frozen_ref or runtime_source
    source = table_sql(active_source, backend)
    aliases = {item["source"]["table"]: "s", "": "s"}
    q = lambda name: quote(name, backend)
    raw_predicates = [
        lower_expression(target["predicate"], backend, aliases=aliases)
        if target["predicate"]
        else "TRUE"
        for target in item["targets"]
    ]
    mode = {
        "exactly_once": "exactly_once_match",
    }.get(item["coverage"]["mode"], item["coverage"]["mode"])
    overlap = item["coverage"]["overlap_policy"]
    if mode == "all" and overlap == "priority":
        mode = "all_assigned_once"
    if overlap == "priority" and mode != "all_assigned_once":
        raise ValueError("priority overlap requires coverage=all_assigned_once")
    if mode == "all_assigned_once" and overlap != "priority":
        raise ValueError("all_assigned_once coverage requires priority overlap")
    predicates = raw_predicates
    if overlap == "priority":
        priorities = [target["priority"] for target in item["targets"]]
        if any(type(priority) is not int for priority in priorities) or len(
            set(priorities)
        ) != len(priorities):
            raise ValueError("Priority overlap requires distinct target priorities")
        predicates = [
            f"({predicate}) AND NOT ("
            + " OR ".join(
                f"({other})"
                for candidate, other in zip(item["targets"], raw_predicates)
                if candidate["priority"] < target["priority"]
            )
            + ")"
            if any(
                candidate["priority"] < target["priority"]
                for candidate in item["targets"]
            )
            else predicate
            for target, predicate in zip(item["targets"], raw_predicates)
        ]
    raw_matches = " + ".join(
        f"CASE WHEN ({predicate}) THEN 1 ELSE 0 END" for predicate in raw_predicates
    )
    effective_matches = " + ".join(
        f"CASE WHEN ({predicate}) THEN 1 ELSE 0 END" for predicate in predicates
    )
    identity = item["identity"]["source_identity"]
    guards = [
        _guard(
            f"SELECT COUNT(*) FROM {source} s WHERE "
            + " OR ".join(f"s.{q(name)} IS NULL" for name in identity),
            "Source identity contains NULL",
            convergence=True,
        ),
        _guard(
            f"SELECT COUNT(*) FROM (SELECT {', '.join(q(name) for name in identity)} FROM {source} GROUP BY {', '.join(q(name) for name in identity)} HAVING COUNT(*) > 1) duplicate_keys",
            "Source identity is not unique",
            convergence=True,
        ),
    ]
    if (
        mode in {"all", "exactly_once_match", "all_assigned_once"}
        or item["coverage"]["on_unmatched"] == "error"
    ):
        matches = effective_matches if mode == "all_assigned_once" else raw_matches
        guards.append(
            _guard(
                f"SELECT COUNT(*) FROM {source} s WHERE ({matches}) = 0",
                "Source rows are not covered by any target",
                convergence=True,
            )
        )
    if (
        mode == "exactly_once_match"
        or overlap == "error"
        or mode == "all_assigned_once"
    ):
        matches = effective_matches if mode == "all_assigned_once" else raw_matches
        guards.append(
            _guard(
                f"SELECT COUNT(*) FROM {source} s WHERE ({matches}) > 1",
                "Source rows match multiple targets",
                convergence=True,
            )
        )
    if item["execution"]["max_rows"] is not None:
        guards.append(
            _guard(
                f"SELECT COUNT(*) FROM {source}",
                "Transition row limit exceeded",
                maximum=item["execution"]["max_rows"],
            )
        )
    source_guard_step = _new_step(guards=guards)
    up = ([freeze_step] if freeze_step else []) + [source_guard_step]
    down = []
    critical = item["completion"]["policy"] == "drop"
    for index, (target, predicate) in enumerate(zip(item["targets"], predicates)):
        table = table_sql(target["target_table"], backend)
        mappings = {
            name: lower_expression(expression, backend, aliases=aliases)
            for name, expression in target["mappings"].items()
        }
        source_mappings = {
            name: lower_expression(expression, backend, aliases=aliases)
            for name, expression in target.get(
                "source_mappings", target["mappings"]
            ).items()
        }
        keys = target["identity"]
        join = " AND ".join(
            _same(f"t.{q(name)}", mappings[name], backend) for name in keys
        )
        equivalent = " AND ".join(
            _same(f"t.{q(name)}", expression, backend)
            for name, expression in mappings.items()
        )
        policy = target["conflict"]["policy"]
        guards = [
            _guard(
                f"SELECT COUNT(*) FROM {source} s WHERE FALSE AND ("
                + " OR ".join(
                    f"{expression} IS NULL" for expression in mappings.values()
                )
                + ")",
                "Transition expressions cannot be resolved against the preserved source",
            ),
            _guard(
                f"SELECT COUNT(*) FROM {source} s WHERE ({predicate}) AND ("
                + " OR ".join(f"{mappings[name]} IS NULL" for name in keys)
                + ")",
                "Target identity maps to NULL",
                convergence=True,
            ),
            _guard(
                f"SELECT COUNT(*) FROM (SELECT {', '.join(mappings[name] for name in keys)} FROM {source} s WHERE {predicate} GROUP BY {', '.join(mappings[name] for name in keys)} HAVING COUNT(*) > 1) duplicate_keys",
                "Multiple source rows map to one target identity",
                convergence=True,
            ),
        ]
        for name, mapping in mappings.items():
            if source_mappings[name] != mapping:
                guards.append(
                    _guard(
                        f"SELECT COUNT(*) FROM {source} s WHERE ({predicate}) AND NOT {_same(source_mappings[name], mapping, backend)}",
                        f"Target cast for {name} would lose information",
                    )
                )
        if policy != "overwrite":
            condition = "" if policy == "error" else f" AND NOT ({equivalent})"
            guards.append(
                _guard(
                    f"SELECT COUNT(*) FROM {source} s JOIN {table} t ON {join} WHERE ({predicate}){condition}",
                    "Target conflict"
                    if policy == "error"
                    else "Existing target differs from owned mapped values",
                )
            )
        else:
            critical = True
            if item["rollback"]["policy"] not in {"irreversible", "capture"}:
                raise ValueError("overwrite requires rollback=capture or irreversible")
        ledger_name = "_dbwarden_edges_" + item["transition_id"][:16] + "_" + str(index)
        ledger = q(ledger_name)
        mapped_columns = list(mappings)
        assigned = [name for name in mappings if name not in keys]
        edge_select = ", ".join(
            [f"s.{q(name)} AS {q('s_' + str(i))}" for i, name in enumerate(identity)]
            + [f"{mappings[name]} AS {q('t_' + str(i))}" for i, name in enumerate(keys)]
            + [
                f"{mappings[name]} AS {q('v_' + str(i))}"
                for i, name in enumerate(mapped_columns)
            ]
            + [
                f"(SELECT t.{q(name)} FROM {table} t WHERE {join}) AS {q('o_' + str(i))}"
                for i, name in enumerate(assigned)
            ]
            + [
                (
                    "0"
                    if backend == "clickhouse"
                    else f"CASE WHEN EXISTS (SELECT 1 FROM {table} t WHERE {join}) THEN 0 ELSE 1 END"
                )
                + f" AS {q('owned_write')}"
            ]
        )
        sql = [
            f"CREATE TABLE {ledger} AS SELECT {edge_select} FROM {source} s WHERE FALSE;",
            f"INSERT INTO {ledger} SELECT {edge_select} FROM {source} s WHERE ({predicate});",
        ]
        target_edge = " AND ".join(
            _same(f"t.{q(name)}", f"e.{q('t_' + str(i))}", backend)
            for i, name in enumerate(keys)
        )
        if policy == "overwrite" and assigned:
            expected_preimage = " AND ".join(
                _same(f"t.{q(name)}", f"e.{q('o_' + str(i))}", backend)
                for i, name in enumerate(assigned)
            )
            sql.append(
                f"UPDATE {table} AS t SET "
                + ", ".join(
                    f"{q(name)} = (SELECT e.{q('v_' + str(mapped_columns.index(name)))} "
                    f"FROM {ledger} e WHERE e.{q('owned_write')} = 0 AND ({target_edge}))"
                    for name in assigned
                )
                + f" WHERE EXISTS (SELECT 1 FROM {ledger} e WHERE e.{q('owned_write')} = 0 "
                f"AND ({target_edge}) AND ({expected_preimage}));"
            )
        insert_filter = (
            ""
            if backend == "clickhouse" and policy == "error"
            else f" WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {target_edge})"
        )
        sql.append(
            f"INSERT INTO {table} ({', '.join(q(name) for name in mappings)}) SELECT "
            + ", ".join(f"e.{q('v_' + str(i))}" for i in range(len(mapped_columns)))
            + f" FROM {ledger} e{insert_filter};"
        )
        insert_proofs = []
        if backend in {"mysql", "mariadb"}:
            expected = (
                f"SELECT COUNT(*) FROM {ledger}"
                if policy == "error"
                else f"SELECT COUNT(*) FROM {ledger} WHERE {q('owned_write')} = 1"
            )
            insert_proofs.append({"statement_index": len(sql) - 1, "query": expected})
        after = _guard(
            f"SELECT COUNT(*) FROM {source} s WHERE ({predicate}) AND NOT EXISTS (SELECT 1 FROM {table} t WHERE ({join}) AND ({equivalent}))",
            "Transition target does not converge",
            timing="after",
        )
        exactly_one = _guard(
            f"SELECT COUNT(*) FROM {source} s WHERE ({predicate}) AND "
            f"(SELECT COUNT(*) FROM {table} t WHERE {join}) <> 1",
            "Transition target identity does not resolve to exactly one row",
            timing="after",
        )
        source_guard_step["guards"].extend(guards)
        target_step = _new_step(sql, [after, exactly_one])
        if insert_proofs:
            target_step["insert_proofs"] = insert_proofs
        up.append(target_step)
        up[-1]["identity_edges"] = {
            "definition_id": item["identity"]["identity_edges"][index],
            "transition_id": item["transition_id"],
            "ledger_table": ledger_name,
            "source_columns": ["s_" + str(i) for i in range(len(identity))]
            + (
                ["o_" + str(i) for i in range(len(assigned))]
                if item["rollback"]["policy"] == "capture"
                else []
            ),
            "source_key_count": len(identity),
            "target_identity_columns": ["t_" + str(i) for i in range(len(keys))],
            "value_columns": ["v_" + str(i) for i in range(len(mapped_columns))],
            "target_table": target["target_table"],
            "target_columns": mapped_columns,
            "target_key_columns": keys,
            "owned_column": "owned_write",
        }
        edge_equivalent = " AND ".join(
            _same(f"t.{q(name)}", f"e.{q('v_' + str(i))}", backend)
            for i, name in enumerate(mapped_columns)
        )
        rollback_guard = _guard(
            f"SELECT COUNT(*) FROM {ledger} e JOIN {table} t ON {target_edge} "
            f"WHERE e.{q('owned_write')} = 1 AND NOT ({edge_equivalent})",
            "Owned target row changed after transition; rollback refused",
        )
        rollback_after = _guard(
            f"SELECT COUNT(*) FROM {ledger} e JOIN {table} t ON {target_edge} "
            f"WHERE e.{q('owned_write')} = 1",
            "Owned transition row was not deleted",
            timing="after",
        )
        rollback_sql = []
        rollback_guards = [rollback_guard]
        if item["rollback"]["policy"] == "capture" and assigned:
            current_postimage = " AND ".join(
                _same(
                    f"t.{q(name)}",
                    f"e.{q('v_' + str(mapped_columns.index(name)))}",
                    backend,
                )
                for name in assigned
            )
            restored_preimage = " AND ".join(
                _same(f"t.{q(name)}", f"e.{q('o_' + str(i))}", backend)
                for i, name in enumerate(assigned)
            )
            rollback_guards.append(
                _guard(
                    f"SELECT COUNT(*) FROM {ledger} e JOIN {table} t ON {target_edge} "
                    f"WHERE e.{q('owned_write')} = 0 AND NOT ({current_postimage})",
                    "Overwritten target changed after transition; rollback refused",
                )
            )
            rollback_sql.append(
                f"UPDATE {table} AS t SET "
                + ", ".join(
                    f"{q(name)} = (SELECT e.{q('o_' + str(i))} FROM {ledger} e "
                    f"WHERE e.{q('owned_write')} = 0 AND ({target_edge}))"
                    for i, name in enumerate(assigned)
                )
                + f" WHERE EXISTS (SELECT 1 FROM {ledger} e WHERE e.{q('owned_write')} = 0 "
                f"AND ({target_edge}) AND ({current_postimage}));"
            )
            rollback_guards.append(
                _guard(
                    f"SELECT COUNT(*) FROM {ledger} e JOIN {table} t ON {target_edge} "
                    f"WHERE e.{q('owned_write')} = 0 AND NOT ({restored_preimage})",
                    "Overwritten target preimage was not restored",
                    timing="after",
                )
            )
        rollback_sql.append(
            _delete(
                table,
                "t",
                f"EXISTS (SELECT 1 FROM {ledger} e "
                f"WHERE e.{q('owned_write')} = 1 AND ({target_edge}) "
                f"AND ({edge_equivalent}))",
                backend,
            )
        )
        rollback_guards.append(rollback_after)
        down[0:0] = [
            _new_step(
                rollback_sql,
                rollback_guards,
            ),
            _new_step([f"DROP TABLE {ledger};"]),
        ]
    unmatched_down = []
    if item["coverage"]["on_unmatched"] == "archive":
        destination_ref = item["coverage"].get("archive_destination")
        columns = item.get("source_columns") or []
        if not destination_ref or not columns:
            raise ValueError("Unmatched archive metadata is incomplete")
        destination = table_sql(destination_ref, backend)
        ledger_name = "_dbwarden_unmatched_" + item["transition_id"][:16]
        ledger = q(ledger_name)
        condition = f"({raw_matches}) = 0"
        source_order = identity + [name for name in columns if name not in identity]
        source_index = {name: index for index, name in enumerate(source_order)}
        selected = (
            [
                f"s.{q(name)} AS {q('s_' + str(i))}"
                for i, name in enumerate(source_order)
            ]
            + [f"s.{q(name)} AS {q('t_' + str(i))}" for i, name in enumerate(identity)]
            + [f"s.{q(name)} AS {q('v_' + str(i))}" for i, name in enumerate(columns)]
            + [f"1 AS {q('owned_write')}"]
        )
        capture = f"SELECT {', '.join(selected)} FROM {source} s WHERE {condition}"
        destination_key = " AND ".join(
            _same(f"a.{q(name)}", f"s.{q(name)}", backend) for name in identity
        )
        up.append(
            _new_step(
                [
                    f"CREATE TABLE IF NOT EXISTS {destination} AS SELECT {', '.join(q(name) for name in columns)} FROM {source} s WHERE FALSE;"
                ]
            )
        )
        archive_match = " AND ".join(
            _same(f"a.{q(name)}", f"e.{q('v_' + str(i))}", backend)
            for i, name in enumerate(columns)
        )
        source_match = " AND ".join(
            _same(f"s.{q(name)}", f"e.{q('v_' + str(i))}", backend)
            for i, name in enumerate(columns)
        )
        key_edge = " AND ".join(
            _same(f"s.{q(name)}", f"e.{q('t_' + str(i))}", backend)
            for i, name in enumerate(identity)
        )
        archive_step = _new_step(
            [
                f"CREATE TABLE {ledger} AS SELECT * FROM ({capture}) e WHERE FALSE;",
                f"INSERT INTO {ledger} {capture};",
                f"INSERT INTO {destination} ({', '.join(q(name) for name in columns)}) SELECT "
                + ", ".join(f"e.{q('v_' + str(i))}" for i in range(len(columns)))
                + f" FROM {ledger} e;",
                _delete(
                    source,
                    "s",
                    f"EXISTS (SELECT 1 FROM {ledger} e WHERE {source_match}) "
                    f"AND EXISTS (SELECT 1 FROM {destination} a JOIN {ledger} e ON {archive_match} WHERE {source_match})",
                    backend,
                ),
            ],
            [
                _guard(
                    f"SELECT COUNT(*) FROM {source} s WHERE {condition} AND EXISTS (SELECT 1 FROM {destination} a WHERE {destination_key})",
                    "Unmatched archive identity already exists",
                ),
                _guard(
                    f"SELECT COUNT(*) FROM {ledger} e WHERE NOT EXISTS (SELECT 1 FROM {destination} a WHERE {archive_match})",
                    "Unmatched source row was not archived",
                    timing="after",
                ),
                _guard(
                    f"SELECT COUNT(*) FROM {ledger} e JOIN {source} s ON {key_edge}",
                    "Archived unmatched row remains in the source",
                    timing="after",
                ),
            ],
        )
        archive_step["identity_edges"] = {
            "definition_id": digest(item["transition_id"], "unmatched-archive-edge"),
            "transition_id": item["transition_id"],
            "ledger_table": ledger_name,
            "source_columns": ["s_" + str(i) for i in range(len(columns))],
            "source_key_count": len(identity),
            "target_identity_columns": ["t_" + str(i) for i in range(len(identity))],
            "value_columns": ["v_" + str(i) for i in range(len(columns))],
            "target_table": destination_ref,
            "target_columns": columns,
            "target_key_columns": identity,
            "owned_column": "owned_write",
        }
        up.append(archive_step)
        if item["rollback"]["policy"] != "irreversible":
            unmatched_down = [
                _new_step(
                    [
                        f"INSERT INTO {table_sql(runtime_source, backend)} ({', '.join(q(name) for name in columns)}) SELECT "
                        + ", ".join(
                            f"e.{q('s_' + str(source_index[name]))}" for name in columns
                        )
                        + f" FROM {ledger} e WHERE NOT EXISTS (SELECT 1 FROM {table_sql(runtime_source, backend)} s WHERE {key_edge});",
                        _delete(
                            destination,
                            "a",
                            f"EXISTS (SELECT 1 FROM {ledger} e WHERE {archive_match}) "
                            f"AND EXISTS (SELECT 1 FROM {table_sql(runtime_source, backend)} s JOIN {ledger} e ON {key_edge} WHERE {source_match})",
                            backend,
                        ),
                    ],
                    [
                        _guard(
                            f"SELECT COUNT(*) FROM {ledger} e WHERE NOT EXISTS (SELECT 1 FROM {destination} a WHERE {archive_match})",
                            "Unmatched archive changed before rollback",
                        ),
                        _guard(
                            f"SELECT COUNT(*) FROM {ledger} e WHERE NOT EXISTS (SELECT 1 FROM {table_sql(runtime_source, backend)} s WHERE {source_match})",
                            "Unmatched source row was not restored",
                            timing="after",
                        ),
                    ],
                ),
                _new_step([f"DROP TABLE {ledger};"]),
            ]
    if item["completion"]["policy"] == "keep":
        if frozen_ref:
            up.append(_mysql_rename_step(active_source, runtime_source, backend))
    elif item["completion"]["policy"] in {"preserve", "archive"}:
        preserved_name = item["rollback"]["preservation_ref"]
        preserved_ref = (
            item["completion"].get("destination")
            if item["completion"]["policy"] == "archive"
            else {**item["source"], "table": preserved_name}
        )
        preserved = table_sql(preserved_ref, backend)
        if backend in {"mysql", "mariadb"}:
            up.append(_mysql_rename_step(active_source, preserved_ref, backend))
            down.append(_mysql_rename_step(preserved_ref, runtime_source, backend))
        else:
            up.append(
                _new_step([f"ALTER TABLE {source} RENAME TO {q(preserved_name)};"])
            )
            down.append(
                _new_step(
                    [f"ALTER TABLE {preserved} RENAME TO {q(runtime_source['table'])};"]
                )
            )
    else:
        if backend in {"mysql", "mariadb"}:
            exists = _mysql_table_exists(active_source, backend)
            up.append(
                _new_step(
                    [f"DROP TABLE {source};"],
                    [
                        _guard(
                            exists,
                            "Transition source is unavailable for retirement",
                            minimum=1,
                            maximum=1,
                            dry_run=False,
                        ),
                        _guard(
                            exists,
                            "Transition source retirement did not complete",
                            maximum=0,
                            timing="after",
                            convergence=False,
                            dry_run=False,
                        ),
                    ],
                )
            )
        else:
            up.append(_new_step([f"DROP TABLE {source};"]))
        down = []
    down.extend(unmatched_down)
    if frozen_ref:
        for step in up:
            for guard in step["guards"]:
                if guard.get("dry_run") is not False and source in guard["query"]:
                    guard["dry_run_query"] = guard["query"].replace(source, live_source)
    return up, down, "CRITICAL" if critical else "WARN"


def _validation(item, previous, backend):
    table = table_sql(item["target_table"], backend)
    guards = []
    for validation in item["validations"]:
        expression = lower_expression(validation["expression"], backend)
        guards.append(
            _guard(
                f"SELECT COUNT(*) FROM {table} WHERE NOT "
                f"{_same(expression, 'TRUE', backend)}",
                validation["message"],
                timing="after",
                convergence=True,
            )
        )
    return [_new_step(guards=guards)], [], "INFO"


def plan_data(spec, previous=None):
    validate_spec(spec)
    backend = spec["database"]["backend"]
    if backend not in {
        "sqlite",
        "postgresql",
        "mysql",
        "mariadb",
        "clickhouse",
    } and any(
        spec.get(key, [])
        for key in ("managed_rows", "transformations", "validations", "transitions")
    ):
        raise ValueError(
            f"Declarative data SQL lowering is not supported for {backend}"
        )
    if backend == "clickhouse" and spec.get("transitions"):
        raise ValueError(
            "ClickHouse historical transitions require a source write barrier"
        )
    previous = previous or {}
    ops = []
    for kind, lower in (
        ("managed_rows", _managed),
        ("transformations", _transformation),
        ("transitions", _transition),
        ("validations", _validation),
    ):
        prior = {item["declaration_id"]: item for item in previous.get(kind, [])}
        for item in spec.get(kind, []):
            old = prior.get(item["declaration_id"])
            if old == item and not (
                kind == "validations"
                and any(
                    op["data_declaration"].get("target_table") == item["target_table"]
                    or any(
                        target["target_table"] == item["target_table"]
                        for target in op["data_declaration"].get("targets", [])
                    )
                    for op in ops
                )
            ):
                continue
            upgrade, rollback, severity = lower(item, old, backend)
            if backend == "clickhouse":
                if (
                    kind != "validations"
                    and item["rollback"]["policy"] != "irreversible"
                ):
                    raise ValueError(
                        "ClickHouse data mutations require rollback=irreversible; transactional rollback is unavailable"
                    )
                if any(
                    target["conflict"]["policy"] == "overwrite"
                    for target in item.get("targets", [])
                ):
                    raise ValueError(
                        "ClickHouse transfer overwrite is unsupported; use error or ignore_if_equivalent"
                    )
                for step in upgrade:
                    step["sql"] = [_clickhouse_statement(sql) for sql in step["sql"]]
                rollback = []
            from .batches import attach_execution

            attach_execution(upgrade, rollback, item, backend)
            operation_id = digest([kind, item], "operation")
            for direction, steps in (("upgrade", upgrade), ("rollback", rollback)):
                for index, step in enumerate(steps):
                    step["operation_id"] = operation_id
                    step["step_id"] = digest(
                        [operation_id, direction, index], "execution-step"
                    )
            ref = item.get("target_table", item.get("source"))
            name = ".".join(part for part in (ref.get("schema"), ref["table"]) if part)
            transition_sources = {}
            if kind == "transitions":
                runtime_source = _transition_runtime_source(item, old)
                guard_source = runtime_source
                if (
                    backend in {"mysql", "mariadb"}
                    and item["completion"]["policy"] != "keep"
                ):
                    guard_source = {
                        **runtime_source,
                        "table": "_dbwarden_frozen_" + item["transition_id"][:16],
                    }
                transition_sources = {
                    "runtime_source": runtime_source,
                    "guard_source": guard_source,
                }
            ops.append(
                {
                    "id": operation_id,
                    "type": "declarative_data",
                    "table": name,
                    "data_kind": kind,
                    "declaration_id": item["declaration_id"],
                    "data_declaration": item,
                    "depends_on": [],
                    "referenced_tables": [
                        ".".join(
                            part
                            for part in (
                                target["target_table"].get("schema"),
                                target["target_table"]["table"],
                            )
                            if part
                        )
                        for target in item.get("targets", [])
                    ],
                    "data_upgrade": upgrade,
                    "data_rollback": rollback,
                    "data_severity": severity,
                    "category": item.get("category"),
                    **transition_sources,
                    "__irreversible": item.get("rollback", {}).get("policy")
                    == "irreversible",
                    "rollback_reason": "Declaration explicitly selects irreversible rollback"
                    if item.get("rollback", {}).get("policy") == "irreversible"
                    else None,
                }
            )
    return ops


def _clickhouse_statement(sql):
    import sqlglot
    from sqlglot import exp

    tree = sqlglot.parse_one(sql, read="clickhouse")
    if isinstance(tree, exp.Update):
        table = tree.this.sql(dialect="clickhouse")
        assignments = ", ".join(
            item.sql(dialect="clickhouse") for item in tree.expressions
        )
        where = tree.args["where"].this.sql(dialect="clickhouse")
        return f"ALTER TABLE {table} UPDATE {assignments} WHERE {where} SETTINGS mutations_sync = 2;"
    if isinstance(tree, exp.Delete):
        return f"ALTER TABLE {tree.this.sql(dialect='clickhouse')} DELETE WHERE {tree.args['where'].this.sql(dialect='clickhouse')} SETTINGS mutations_sync = 2;"
    if isinstance(tree, exp.Create) and tree.args.get("expression") is not None:
        exists = "IF NOT EXISTS " if tree.args.get("exists") else ""
        return f"CREATE TABLE {exists}{tree.this.sql(dialect='clickhouse')} ENGINE = MergeTree ORDER BY tuple() AS {tree.args['expression'].sql(dialect='clickhouse')};"
    if isinstance(tree, exp.Alter) and len(tree.args.get("actions", [])) == 1:
        action = tree.args["actions"][0]
        if isinstance(action, exp.AlterRename):
            return f"RENAME TABLE {tree.this.sql(dialect='clickhouse')} TO {action.this.sql(dialect='clickhouse')};"
    return sql


def previous_data_spec(files, *, database, backend):
    from dbwarden.engine.safety.plans import read_trusted_plan
    from dbwarden.merge.marker import is_superseded

    from .ir import empty_spec

    latest = empty_spec(database, backend)
    for _, path in sorted(files.items()):
        if is_superseded(path):
            continue
        plan, reason = read_trusted_plan(path)
        if plan and "data_spec" in plan:
            from .artifacts import verify_bundle

            verify_bundle(path, plan)
            latest = validate_spec(plan["data_spec"])
        elif not plan:
            from pathlib import Path

            if Path(path).with_suffix(".data.py").exists():
                raise ValueError(f"Untrusted data migration: {reason}")
    return deepcopy(latest)
