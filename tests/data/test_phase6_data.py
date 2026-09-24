import os
import sqlite3
import uuid

import pytest
from sqlalchemy import Integer, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from dbwarden.data.compiler import DiscoveredData, compile_data
from dbwarden.data.convergence import check_convergence
from dbwarden.data.declarations import (
    DataTransition,
    archive_table,
    derive,
    historical_table,
    into,
    rows,
)
from dbwarden.data.execution import execute_data_plan
from dbwarden.data.expressions import canonical_expression, col
from dbwarden.data.planning import (
    _delete,
    _managed,
    _transformation,
    _transition,
    plan_data,
)
from dbwarden.data.snapshots import register_snapshot


def _run_step(connection, step):
    for guard in step["guards"]:
        if guard.get("timing", "before") == "before":
            value = connection.execute(guard["query"]).fetchone()[0]
            minimum, maximum = guard.get("minimum"), guard.get("maximum")
            assert (minimum is None or value >= minimum) and (
                maximum is None or value <= maximum
            )
    for statement in step["sql"]:
        connection.execute(statement)
    for guard in step["guards"]:
        if guard.get("timing") == "after":
            value = connection.execute(guard["query"]).fetchone()[0]
            minimum, maximum = guard.get("minimum"), guard.get("maximum")
            assert (minimum is None or value >= minimum) and (
                maximum is None or value <= maximum
            )


def test_archive_declarations_require_explicit_acknowledgement():
    with pytest.raises(ValueError, match="acknowledge_archive"):
        rows(
            rows=[{"id": 1}],
            on_missing="archive",
            scope=col("id") > 0,
            archive_to=archive_table("retired"),
        )

    class Base(DeclarativeBase):
        pass

    class Target(Base):
        __tablename__ = "target"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        name: Mapped[str] = mapped_column(String, nullable=False)

    from dbwarden.data.declarations import into

    with pytest.raises(ValueError, match="acknowledge_archive"):

        class Invalid(DataTransition):
            source = historical_table("legacy", snapshot="v1")
            source_identity = ("id",)
            targets = (into(Target, map={"id": col("id"), "name": col("name")}),)
            coverage = "subset"
            on_unmatched = "archive"
            unmatched_archive_to = archive_table("retired")


def test_capture_transformation_restores_authenticated_preimage_sqlite():
    expression = canonical_expression(col("value") + 1, columns=["id", "value"])
    item = {
        "declaration_id": "items.capture",
        "target_table": {"database": "primary", "schema": None, "table": "items"},
        "target_columns": ["value"],
        "key_columns": ["id"],
        "expression": expression,
        "domain": None,
        "on_unmatched": "error",
        "rollback": {"policy": "capture", "capture_ref": "_dbwarden_capture_items"},
        "max_rows": None,
        "execution": {},
    }
    upgrade, rollback, _ = _transformation(item, None, "sqlite")
    assert upgrade[0]["identity_edges"]["source_columns"] == ["s_0", "s_1"]

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE items(id INTEGER PRIMARY KEY, value INTEGER NOT NULL);"
        "INSERT INTO items VALUES(1, 7);"
    )
    _run_step(connection, upgrade[0])
    assert connection.execute("SELECT value FROM items").fetchone()[0] == 8
    _run_step(connection, rollback[0])
    _run_step(connection, rollback[1])
    assert connection.execute("SELECT value FROM items").fetchone()[0] == 7


def test_managed_archive_moves_full_row_and_restores_it_sqlite():
    table = {"database": "primary", "schema": None, "table": "items"}
    base = {
        "declaration_id": "items.rows",
        "target_table": table,
        "key_columns": ["id"],
        "owned_columns": ["name"],
        "scope": canonical_expression(col("id") > 0, columns=["id", "name", "note"]),
        "rollback": {"policy": "restore_previous"},
        "archive": {
            "destination": {
                "database": "primary",
                "schema": None,
                "table": "items_archive",
            },
            "columns": ["id", "name", "note"],
            "identity_edge": "edge",
        },
    }
    previous = {
        **base,
        "on_missing": "archive",
        "row_source": {"rows": [{"id": 1, "name": "old"}]},
    }
    current = {**base, "on_missing": "archive", "row_source": {"rows": []}}
    upgrade, rollback, _ = _managed(current, previous, "sqlite")
    assert upgrade[-1]["identity_edges"]["target_columns"] == ["id", "name", "note"]

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT, note TEXT);"
        "INSERT INTO items VALUES(1, 'old', 'application value');"
    )
    ledger_sql = upgrade[-1]["sql"][0]
    ownership = ledger_sql.split()[5]
    connection.execute(ledger_sql)
    connection.execute(f"INSERT INTO {ownership} VALUES(1)")
    for step in upgrade[:-1]:
        _run_step(connection, step)
    _run_step(connection, upgrade[-1])
    assert connection.execute("SELECT * FROM items").fetchall() == []
    assert connection.execute("SELECT * FROM items_archive").fetchall() == [
        (1, "old", "application value")
    ]
    _run_step(connection, rollback[0])
    assert connection.execute("SELECT * FROM items").fetchall() == [
        (1, "old", "application value")
    ]


def test_managed_archive_ledgers_are_revision_scoped():
    base = {
        "declaration_id": "items.rows",
        "target_table": {"database": "primary", "schema": None, "table": "items"},
        "key_columns": ["id"],
        "owned_columns": ["name"],
        "scope": canonical_expression(col("id") > 0, columns=["id", "name"]),
        "on_missing": "archive",
        "rollback": {"policy": "restore_previous"},
        "archive": {
            "destination": {
                "database": "primary",
                "schema": None,
                "table": "items_archive",
            },
            "columns": ["id", "name"],
            "identity_edge": "edge",
        },
    }
    first = {
        **base,
        "row_source": {"rows": [{"id": 1, "name": "one"}, {"id": 2, "name": "two"}]},
    }
    second = {**base, "row_source": {"rows": [{"id": 2, "name": "two"}]}}
    third = {**base, "row_source": {"rows": []}}
    first_up, _, _ = _managed(second, first, "sqlite")
    second_up, second_down, _ = _managed(third, second, "sqlite")
    first_ledger = first_up[-1]["identity_edges"]["ledger_table"]
    second_ledger = second_up[-1]["identity_edges"]["ledger_table"]
    assert first_ledger != second_ledger
    assert all(first_ledger not in sql for step in second_down for sql in step["sql"])

    other_previous = {
        **base,
        "row_source": {"rows": [{"id": 3, "name": "three"}]},
    }
    other_up, _, _ = _managed(third, other_previous, "sqlite")
    assert other_up[-1]["identity_edges"]["ledger_table"] != second_ledger


def test_derive_capture_is_public_and_frozen():
    declaration = derive("value", col("value") + 1, rollback="capture")
    assert declaration.rollback == "capture"


def test_mariadb_delete_alias_uses_supported_multi_table_form():
    assert _delete("`people`", "t", "t.`id` = 1", "mariadb") == (
        "DELETE t FROM `people` AS t WHERE t.`id` = 1;"
    )


def _transition_item(*, conflict="overwrite", unmatched="error", completion="preserve"):
    columns = ["id", "name"]
    destination = {"database": "primary", "schema": None, "table": "legacy_archive"}
    unmatched_destination = {
        "database": "primary",
        "schema": None,
        "table": "legacy_unmatched",
    }
    return {
        "declaration_id": "legacy.move",
        "transition_id": "a" * 64,
        "source": {"database": "primary", "schema": None, "table": "legacy"},
        "source_columns": columns,
        "targets": [
            {
                "target_table": {
                    "database": "primary",
                    "schema": None,
                    "table": "people",
                },
                "predicate": canonical_expression(col("id") > 0, columns=columns),
                "priority": None,
                "mappings": {
                    "id": canonical_expression(col("id"), columns=columns),
                    "name": canonical_expression(col("name"), columns=columns),
                },
                "identity": ["id"],
                "conflict": {"policy": conflict},
            }
        ],
        "coverage": {
            "mode": "subset" if unmatched == "archive" else "all",
            "on_unmatched": unmatched,
            "overlap_policy": "error",
            "archive_destination": unmatched_destination
            if unmatched == "archive"
            else None,
        },
        "identity": {"source_identity": ["id"], "identity_edges": ["edge"]},
        "execution": {"max_rows": None},
        "completion": {
            "policy": completion,
            "destination": destination if completion == "archive" else None,
        },
        "rollback": {
            "policy": "capture",
            "preservation_ref": "legacy_archive"
            if completion == "archive"
            else "legacy_preserved",
        },
    }


def test_transition_capture_authenticates_and_restores_overwrite_preimages():
    upgrade, rollback, _ = _transition(_transition_item(), None, "sqlite")
    edge = next(step["identity_edges"] for step in upgrade if "identity_edges" in step)
    assert edge["source_columns"] == ["s_0", "o_0"]
    assert any(
        sql.startswith("UPDATE") and '"o_0"' in sql
        for step in rollback
        for sql in step["sql"]
    )
    assert rollback[-1]["sql"][0].startswith("ALTER TABLE")


def test_transition_archive_completion_and_unmatched_rows_are_planned_reversibly():
    item = _transition_item(conflict="error", unmatched="archive", completion="archive")
    upgrade, rollback, _ = _transition(item, None, "sqlite")
    unmatched = next(
        step
        for step in upgrade
        if step.get("identity_edges", {})
        .get("ledger_table", "")
        .startswith("_dbwarden_unmatched_")
    )
    assert unmatched["identity_edges"]["target_table"]["table"] == "legacy_unmatched"
    assert any(
        'RENAME TO "legacy_archive"' in sql for step in upgrade for sql in step["sql"]
    )
    assert any(
        sql.startswith('INSERT INTO "legacy"')
        for step in rollback
        for sql in step["sql"]
    )


@pytest.fixture(params=("postgresql", "mariadb", "mysql"))
def native_keep_connection(request):
    backend = request.param
    variable = "POSTGRES" if backend == "postgresql" else backend.upper()
    url = os.environ.get("DBWARDEN_DATA_TEST_" + variable)
    if not url:
        pytest.skip(f"Set DBWARDEN_DATA_TEST_{variable} for current-source keep tests")
    engine = create_engine(url)
    namespace = "data_keep_" + uuid.uuid4().hex
    with engine.connect() as connection:
        if backend == "postgresql":
            connection.exec_driver_sql(f'CREATE SCHEMA "{namespace}"')
            connection.exec_driver_sql(f'SET search_path TO "{namespace}"')
        else:
            connection.exec_driver_sql(f"CREATE DATABASE `{namespace}`")
            connection.exec_driver_sql(f"USE `{namespace}`")
        connection.commit()
        try:
            yield backend, connection
        finally:
            connection.rollback()
            if backend == "postgresql":
                connection.exec_driver_sql("SET search_path TO public")
                connection.exec_driver_sql(f'DROP SCHEMA "{namespace}" CASCADE')
            else:
                connection.exec_driver_sql(f"DROP DATABASE `{namespace}`")
            connection.commit()
    engine.dispose()


def test_current_model_source_keep_native_round_trip(
    tmp_path, native_keep_connection
):
    backend, connection = native_keep_connection
    registry = tmp_path / "registry.json"
    register_snapshot(
        "keep-current",
        {
            "tables": {
                "keep_source": {
                    "columns": {
                        "id": {"type": "INTEGER", "nullable": False},
                        "name": {"type": "VARCHAR(64)", "nullable": False},
                    }
                }
            }
        },
        database="primary",
        backend=backend,
        registry_path=registry,
    )

    class Base(DeclarativeBase):
        pass

    class Source(Base):
        __tablename__ = "keep_source"
        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str] = mapped_column(String(64), nullable=False)

    class Target(Base):
        __tablename__ = "keep_target"
        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str] = mapped_column(String(64), nullable=False)

    class Copy(DataTransition):
        source = Source
        source_snapshot = "keep-current"
        source_identity = (Source.id,)
        on_complete = "keep"
        targets = (into(Target, map={"id": Source.id, "name": Source.name}),)

    engine_suffix = " ENGINE=InnoDB" if backend in {"mysql", "mariadb"} else ""
    for statement in (
        "CREATE TABLE keep_source(id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL)"
        + engine_suffix,
        "CREATE TABLE keep_target(id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL)"
        + engine_suffix,
        "INSERT INTO keep_source VALUES(1, 'one'), (2, 'two')",
    ):
        connection.exec_driver_sql(statement)
    connection.commit()
    spec = compile_data(
        DiscoveredData((Source, Target), (Copy,)),
        database="primary",
        backend=backend,
        registry_path=registry,
    )
    operations = plan_data(spec)
    migration_id = "keep-native"
    plan = {
        "migration_id": migration_id,
        "data_spec": spec,
        "data_bundle": {},
        "upgrade_ops": operations,
        "data_execution": {
            "upgrade": [step for op in operations for step in op["data_upgrade"]],
            "rollback": [
                step
                for op in reversed(operations)
                for step in op["data_rollback"]
            ],
        },
    }
    assert (
        execute_data_plan(connection, plan, migration_id=migration_id)["status"]
        == "APPLIED_SUCCESS"
    )
    assert connection.execute(
        text("SELECT id, name FROM keep_source ORDER BY id")
    ).all() == [(1, "one"), (2, "two")]
    assert check_convergence(connection, spec, plans=[plan]) == []
    connection.commit()
    assert (
        execute_data_plan(
            connection, plan, migration_id=migration_id, direction="rollback"
        )["status"]
        == "ROLLED_BACK"
    )
    assert connection.execute(
        text("SELECT COUNT(*) FROM keep_source")
    ).scalar_one() == 2
    assert connection.execute(
        text("SELECT COUNT(*) FROM keep_target")
    ).scalar_one() == 0
    connection.commit()
    assert (
        execute_data_plan(connection, plan, migration_id=migration_id, reapply=True)[
            "status"
        ]
        == "APPLIED_SUCCESS"
    )
    assert check_convergence(connection, spec, plans=[plan]) == []
