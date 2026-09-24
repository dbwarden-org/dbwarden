import sqlite3

import pytest

from dbwarden.data.expressions import canonical_expression, col
from dbwarden.data.planning import _transition, plan_data


def _expression(value, backend="mysql"):
    return canonical_expression(value, columns=["id", "name"], backend=backend)


def _item(*, transition_id="a" * 64, preserved="legacy_preserved"):
    return {
        "declaration_id": "legacy.split",
        "transition_id": transition_id,
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
                    "id": _expression(col("id")),
                    "name": _expression(col("name")),
                },
                "identity": ["id"],
                "conflict": {"policy": "error"},
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
            "policy": "restore_previous",
            "preservation_ref": preserved,
        },
    }


def _sqlite_mysql(sql):
    sql = sql.replace(" <=> ", " IS NOT DISTINCT FROM ")
    if sql.startswith("RENAME TABLE "):
        sql = "ALTER TABLE " + sql.removeprefix("RENAME TABLE ")
        sql = sql.replace(" TO ", " RENAME TO ", 1)
    return sql


def test_mysql_freezes_source_before_guards_and_retires_after_targets():
    upgrade, rollback, _ = _transition(_item(), None, "mysql")
    frozen = "`_dbwarden_frozen_aaaaaaaaaaaaaaaa`"

    assert upgrade[0]["sql"] == [f"RENAME TABLE `legacy` TO {frozen};"]
    assert all(guard.get("dry_run") is False for guard in upgrade[0]["guards"])
    target_index = next(
        index for index, step in enumerate(upgrade) if step.get("identity_edges")
    )
    assert all(frozen in sql for sql in upgrade[target_index]["sql"][:2])
    assert upgrade[-1]["sql"] == [f"RENAME TABLE {frozen} TO `legacy_preserved`;"]
    assert target_index < len(upgrade) - 1
    assert all(guard.get("dry_run") is False for guard in upgrade[-1]["guards"])

    source_guards = [
        guard
        for step in upgrade
        for guard in step["guards"]
        if frozen in guard["query"]
    ]
    assert source_guards
    assert all("`legacy`" in guard["dry_run_query"] for guard in source_guards)
    assert rollback[-1]["sql"] == ["RENAME TABLE `legacy_preserved` TO `legacy`;"]
    assert all(
        not any(sql.startswith("RENAME TABLE") for sql in step["sql"])
        for step in rollback[:-1]
    )


def test_mysql_revision_freezes_and_restores_prior_preserved_source():
    previous = _item(transition_id="a" * 64, preserved="legacy_preserved_v1")
    current = _item(transition_id="b" * 64, preserved="legacy_preserved_v2")

    upgrade, rollback, _ = _transition(current, previous, "mariadb")

    assert upgrade[0]["sql"] == [
        "RENAME TABLE `legacy_preserved_v1` TO `_dbwarden_frozen_bbbbbbbbbbbbbbbb`;"
    ]
    assert upgrade[-1]["sql"] == [
        "RENAME TABLE `_dbwarden_frozen_bbbbbbbbbbbbbbbb` TO `legacy_preserved_v2`;"
    ]
    assert rollback[-1]["sql"] == [
        "RENAME TABLE `legacy_preserved_v2` TO `legacy_preserved_v1`;"
    ]


def test_source_name_cannot_accept_rows_between_capture_and_retirement():
    upgrade, _, _ = _transition(_item(), None, "mysql")
    target_step = next(step for step in upgrade if step.get("identity_edges"))
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE legacy(id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT);"
        "INSERT INTO legacy VALUES(1, 'first');"
    )

    connection.execute(_sqlite_mysql(upgrade[0]["sql"][0]))
    for sql in target_step["sql"]:
        connection.execute(_sqlite_mysql(sql))
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        connection.execute("INSERT INTO legacy VALUES(2, 'late')")
    connection.execute(_sqlite_mysql(upgrade[-1]["sql"][0]))

    assert connection.execute("SELECT COUNT(*) FROM people").fetchone() == (1,)
    assert connection.execute("SELECT id, name FROM legacy_preserved").fetchall() == [
        (1, "first")
    ]


def test_clickhouse_public_planner_rejects_unbarriered_transition(monkeypatch):
    monkeypatch.setattr("dbwarden.data.planning.validate_spec", lambda spec: None)

    with pytest.raises(ValueError, match="source write barrier"):
        plan_data(
            {
                "database": {"backend": "clickhouse"},
                "transitions": [{}],
            }
        )


def test_mysql_operation_exposes_frozen_guard_source(monkeypatch):
    monkeypatch.setattr("dbwarden.data.planning.validate_spec", lambda spec: None)

    operation = plan_data(
        {
            "database": {"backend": "mysql"},
            "transitions": [_item()],
        }
    )[0]

    assert operation["runtime_source"]["table"] == "legacy"
    assert operation["guard_source"]["table"] == ("_dbwarden_frozen_aaaaaaaaaaaaaaaa")
    assert all(step.get("step_id") for step in operation["data_upgrade"])
