import sqlite3

import pytest

from dbwarden.data.expressions import canonical_expression, col, func, literal
from dbwarden.data.planning import _managed, _transformation, _transition


def _expr(value, columns, backend="sqlite"):
    return canonical_expression(value, columns=columns, backend=backend)


def _managed_item(rows):
    return {
        "declaration_id": "items.data",
        "target_table": {"database": "primary", "schema": None, "table": "items"},
        "key_columns": ["id"],
        "owned_columns": ["name"],
        "row_source": {"rows": rows},
        "scope": _expr(literal(True), ["id", "name"]),
        "on_missing": "delete",
        "rollback": {"policy": "restore_previous"},
    }


def _run_before_guards(connection, step):
    for guard in step["guards"]:
        if guard.get("timing", "before") != "before":
            continue
        value = connection.execute(guard["query"]).fetchone()[0]
        assert value <= guard["maximum"]


def _after_failures(connection, step):
    failures = []
    for guard in step["guards"]:
        if guard.get("timing") != "after":
            continue
        value = connection.execute(guard["query"]).fetchone()[0]
        if value > guard["maximum"] or (
            guard["minimum"] is not None and value < guard["minimum"]
        ):
            failures.append(guard["message"])
    return failures


def test_managed_writes_bind_frozen_values_and_have_postconditions():
    previous = _managed_item([{"id": 1, "name": "old"}, {"id": 2, "name": "removed"}])
    item = _managed_item([{"id": 1, "name": "new"}])
    upgrade, rollback, _ = _managed(item, previous, "mysql")

    update = next(sql for sql in upgrade[0]["sql"] if sql.startswith("UPDATE"))
    delete = next(sql for sql in upgrade[0]["sql"] if sql.startswith("DELETE"))
    assert "'old'" in update and "'new'" in update
    assert "'removed'" in delete
    assert any(
        guard["timing"] == "after" and "not deleted" in guard["message"]
        for guard in upgrade[0]["guards"]
    )

    rollback_update = next(
        sql for sql in rollback[0]["sql"] if sql.startswith("UPDATE")
    )
    assert "'new'" in rollback_update and "'old'" in rollback_update
    assert any(guard["timing"] == "after" for guard in rollback[0]["guards"])


def test_mysql_managed_new_rows_carry_insert_ownership_proofs():
    reversible = _managed_item([{"id": 1, "name": "new"}])
    reversible["on_missing"] = "keep"
    upgrade, _, _ = _managed(reversible, None, "mysql")
    assert upgrade[0]["insert_proofs"] == [
        {
            "statement_index": len(upgrade[0]["sql"]) - 1,
            "expected": 1,
        }
    ]
    preexisting = next(
        guard
        for guard in upgrade[0]["guards"]
        if "must not replace" in guard["message"]
    )
    assert "_dbwarden_rows_" not in preexisting["query"]

    irreversible = _managed_item([{"id": 1, "name": "new"}])
    irreversible["on_missing"] = "keep"
    irreversible["rollback"] = {"policy": "irreversible"}
    upgrade, _, _ = _managed(irreversible, None, "mysql")
    proof = upgrade[0]["insert_proofs"][0]
    assert proof["statement_index"] == len(upgrade[0]["sql"]) - 1
    assert proof["query"].startswith("SELECT COUNT(*) FROM `_dbwarden_rows_")

    previous = _managed_item([{"id": 1, "name": "old"}])
    current = _managed_item([{"id": 1, "name": "new"}])
    current["on_missing"] = "keep"
    upgrade, _, _ = _managed(current, previous, "mysql")
    assert "insert_proofs" not in upgrade[0]


def test_clickhouse_managed_rows_never_claim_insert_ownership_or_delete():
    item = _managed_item([{"id": 1, "name": "new"}])
    item["on_missing"] = "keep"
    item["rollback"] = {"policy": "irreversible"}
    upgrade, _, _ = _managed(item, None, "clickhouse")
    assert not any(
        sql.startswith("INSERT INTO `_dbwarden_rows_") for sql in upgrade[0]["sql"]
    )

    item["on_missing"] = "delete"
    with pytest.raises(
        ValueError, match="ClickHouse managed-row deletion is unsupported"
    ):
        _managed(item, None, "clickhouse")


def test_managed_delete_detects_change_between_guard_and_write():
    previous = _managed_item([{"id": 2, "name": "removed"}])
    item = _managed_item([])
    upgrade, _, _ = _managed(item, previous, "sqlite")
    step = upgrade[0]
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT);"
        "INSERT INTO items VALUES(2, 'removed');"
    )
    ledger = next(sql for sql in step["sql"] if sql.startswith("CREATE TABLE"))
    ledger_name = ledger.split()[5]
    connection.execute(ledger)
    connection.execute(f"INSERT INTO {ledger_name} VALUES(2)")
    _run_before_guards(connection, step)
    connection.execute("UPDATE items SET name='racer' WHERE id=2")
    delete = next(sql for sql in step["sql"] if sql.startswith("DELETE"))
    connection.execute(delete)
    assert connection.execute("SELECT name FROM items").fetchone() == ("racer",)
    assert "Owned row was not deleted" in _after_failures(connection, step)


def test_transformation_detects_change_between_guard_and_write():
    expression = _expr(func.lower(col("name")), ["id", "name", "slug"])
    item = {
        "declaration_id": "items.slug",
        "target_table": {"database": "primary", "schema": None, "table": "items"},
        "target_columns": ["slug"],
        "expression": expression,
        "domain": None,
        "on_unmatched": "error",
        "rollback": {"policy": "clear"},
        "max_rows": None,
    }
    upgrade, rollback, _ = _transformation(item, None, "sqlite")
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE items(id INTEGER, name TEXT, slug TEXT)")
    connection.execute("INSERT INTO items VALUES(1, 'ONE', NULL)")
    _run_before_guards(connection, upgrade[0])
    connection.execute("UPDATE items SET slug='racer' WHERE id=1")
    connection.execute(upgrade[0]["sql"][0])
    assert connection.execute("SELECT slug FROM items").fetchone() == ("racer",)
    assert "Transformation write did not converge" in _after_failures(
        connection, upgrade[0]
    )

    connection.execute("UPDATE items SET slug='one' WHERE id=1")
    _run_before_guards(connection, rollback[0])
    connection.execute("UPDATE items SET slug='racer' WHERE id=1")
    connection.execute(rollback[0]["sql"][0])
    assert connection.execute("SELECT slug FROM items").fetchone() == ("racer",)
    assert "Transformation clear rollback did not converge" in _after_failures(
        connection, rollback[0]
    )


def _transition_item(conflict):
    columns = ["id", "name"]
    return {
        "declaration_id": "legacy.split",
        "transition_id": "a" * 64,
        "source": {"database": "primary", "schema": None, "table": "legacy"},
        "targets": [
            {
                "target_table": {
                    "database": "primary",
                    "schema": None,
                    "table": "people",
                },
                "predicate": None,
                "priority": 0,
                "mappings": {
                    "id": _expr(col("id"), columns, "mysql"),
                    "name": _expr(col("name"), columns, "mysql"),
                },
                "identity": ["id"],
                "conflict": {"policy": conflict},
            }
        ],
        "coverage": {
            "mode": "all",
            "on_unmatched": "error",
            "overlap_policy": "error",
        },
        "identity": {"source_identity": ["id"], "identity_edges": ["edge"]},
        "execution": {"max_rows": None},
        "completion": {"policy": "preserve"},
        "rollback": {
            "policy": "irreversible" if conflict == "overwrite" else "restore_previous",
            "preservation_ref": "legacy_preserved",
        },
    }


def test_transition_overwrite_and_rollback_use_ledger_values():
    overwrite, _, _ = _transition(_transition_item("overwrite"), None, "mysql")
    update = next(
        sql for step in overwrite for sql in step["sql"] if sql.startswith("UPDATE")
    )
    assert "`o_0`" in update
    assert "`owned_write` = 0" in update
    assert "t.`name` <=> e.`o_0`" in update

    _, rollback, _ = _transition(_transition_item("error"), None, "mysql")
    delete_step = next(
        step
        for step in rollback
        if any(sql.startswith("DELETE") for sql in step["sql"])
    )
    delete = delete_step["sql"][0]
    assert "e.`v_0`" in delete and "e.`v_1`" in delete
    assert any(guard["timing"] == "after" for guard in delete_step["guards"])
    drop_step = next(
        step
        for step in rollback
        if any(sql.startswith("DROP TABLE") for sql in step["sql"])
    )
    assert drop_step is not delete_step


def _sqlite_mysql(sql):
    sql = sql.replace(" <=> ", " IS NOT DISTINCT FROM ")
    if sql.startswith("RENAME TABLE "):
        sql = "ALTER TABLE " + sql.removeprefix("RENAME TABLE ")
        sql = sql.replace(" TO ", " RENAME TO ", 1)
    return sql


@pytest.mark.parametrize(
    ("conflict", "insert_after_capture"),
    [("ignore_if_equivalent", True), ("error", False)],
)
def test_mysql_transition_insert_proof_rejects_equivalent_ownership_race(
    conflict, insert_after_capture
):
    upgrade, _, _ = _transition(_transition_item(conflict), None, "mysql")
    step = next(step for step in upgrade if step.get("insert_proofs"))
    proof = step["insert_proofs"][0]
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE legacy(id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT);"
        "INSERT INTO legacy VALUES(1, 'same');"
    )
    connection.execute(_sqlite_mysql(upgrade[0]["sql"][0]))
    observed = None
    for index, sql in enumerate(step["sql"]):
        if index == 1 and not insert_after_capture:
            connection.execute("INSERT INTO people VALUES(1, 'same')")
        cursor = connection.execute(_sqlite_mysql(sql))
        if index == 1 and insert_after_capture:
            connection.execute("INSERT INTO people VALUES(1, 'same')")
        if index == proof["statement_index"]:
            observed = cursor.rowcount
    expected = connection.execute(_sqlite_mysql(proof["query"])).fetchone()[0]
    assert observed == 0
    assert expected == 1


def test_clickhouse_transition_error_inserts_all_edges_and_detects_duplicates():
    upgrade, _, _ = _transition(_transition_item("error"), None, "clickhouse")
    step = next(step for step in upgrade if step.get("identity_edges"))
    assert "0 AS `owned_write`" in step["sql"][0]
    assert "NOT EXISTS" not in step["sql"][-1]
    assert any("exactly one row" in guard["message"] for guard in step["guards"])

    ignored, _, _ = _transition(
        _transition_item("ignore_if_equivalent"), None, "clickhouse"
    )
    ignored_step = next(step for step in ignored if step.get("identity_edges"))
    assert "NOT EXISTS" in ignored_step["sql"][-1]

    connection = sqlite3.connect(":memory:")
    connection.create_function(
        "isNotDistinctFrom", 2, lambda left, right: int(left == right)
    )
    connection.executescript(
        "CREATE TABLE legacy(id INTEGER, name TEXT);"
        "CREATE TABLE people(id INTEGER, name TEXT);"
        "INSERT INTO legacy VALUES(1, 'same');"
    )
    for sql in step["sql"][:2]:
        connection.execute(sql)
    connection.execute("INSERT INTO people VALUES(1, 'same')")
    connection.execute(step["sql"][-1])
    assert connection.execute("SELECT COUNT(*) FROM people").fetchone() == (2,)
    failures = _after_failures(connection, step)
    assert "Transition target identity does not resolve to exactly one row" in failures


def test_transition_writes_detect_target_changes_after_ledger_capture():
    upgrade, _, _ = _transition(_transition_item("overwrite"), None, "sqlite")
    target_step = next(
        step for step in upgrade if any(sql.startswith("UPDATE") for sql in step["sql"])
    )
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE legacy(id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT);"
        "INSERT INTO legacy VALUES(1, 'new');"
        "INSERT INTO people VALUES(1, 'old');"
    )
    for sql in target_step["sql"][:2]:
        connection.execute(sql)
    connection.execute("UPDATE people SET name='racer' WHERE id=1")
    for sql in target_step["sql"][2:]:
        connection.execute(sql)
    assert connection.execute("SELECT name FROM people").fetchone() == ("racer",)
    assert "Transition target does not converge" in _after_failures(
        connection, target_step
    )

    connection.close()
    upgrade, rollback, _ = _transition(_transition_item("error"), None, "sqlite")
    target_step = next(step for step in upgrade if step.get("identity_edges"))
    delete_step = next(
        step
        for step in rollback
        if any(sql.startswith("DELETE") for sql in step["sql"])
    )
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE legacy(id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT);"
        "INSERT INTO legacy VALUES(1, 'new');"
    )
    for sql in target_step["sql"]:
        connection.execute(sql)
    _run_before_guards(connection, delete_step)
    connection.execute("UPDATE people SET name='racer' WHERE id=1")
    connection.execute(delete_step["sql"][0])
    assert connection.execute("SELECT name FROM people").fetchone() == ("racer",)
    assert "Owned transition row was not deleted" in _after_failures(
        connection, delete_step
    )
