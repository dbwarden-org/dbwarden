from sqlalchemy import Column, Integer, String, create_engine, text
from sqlalchemy.orm import declarative_base

from dbwarden.data import DataMeta, archive_table, col, derive, rows
from dbwarden.data.compiler import DiscoveredData, compile_data
from dbwarden.data.convergence import check_convergence
from dbwarden.data.execution import execute_data_plan
from dbwarden.data.planning import plan_data


def _plan(spec, *, migration_id, previous=None):
    operations = plan_data(spec, previous)
    return {
        "migration_id": migration_id,
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


def _managed_spec(values):
    base = declarative_base()

    class Item(base):
        __tablename__ = "items"
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)

    class Data(DataMeta):
        managed_rows = rows(
            rows=values,
            owned_columns=["name"],
            on_missing="archive",
            scope=col("id") > 0,
            archive_to=archive_table("items_archive"),
            acknowledge_archive=True,
            rollback="restore_previous",
        )

    Item.Data = Data
    return compile_data(
        DiscoveredData((Item,), ()), database="primary", backend="sqlite"
    )


def test_managed_archive_convergence_requires_authenticated_receipt():
    previous = _managed_spec([{"id": 1, "name": "old"}])
    current = _managed_spec([])
    initial = _plan(previous, migration_id="initial")
    archived = _plan(current, migration_id="archive", previous=previous)

    with create_engine("sqlite://").connect() as connection:
        connection.execute(
            text("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        )
        connection.commit()
        execute_data_plan(connection, initial, migration_id="initial")
        connection.commit()
        execute_data_plan(connection, archived, migration_id="archive")
        assert check_convergence(connection, current, plans=[archived]) == []

        connection.execute(text("UPDATE items_archive SET name='tampered' WHERE id=1"))
        connection.commit()
        findings = check_convergence(connection, current, plans=[archived])
        assert [finding["kind"] for finding in findings] == ["unverifiable_archive"]


def _capture_spec():
    base = declarative_base()

    class Item(base):
        __tablename__ = "items"
        id = Column(Integer, primary_key=True)
        value = Column(Integer, nullable=False)

    class Data(DataMeta):
        transformations = (derive("value", col("value") + 1, rollback="capture"),)

    Item.Data = Data
    return compile_data(
        DiscoveredData((Item,), ()), database="primary", backend="sqlite"
    )


def test_self_referential_capture_convergence_uses_authenticated_receipt():
    spec = _capture_spec()
    plan = _plan(spec, migration_id="capture")

    with create_engine("sqlite://").connect() as connection:
        connection.execute(
            text("CREATE TABLE items(id INTEGER PRIMARY KEY, value INTEGER NOT NULL)")
        )
        connection.execute(text("INSERT INTO items VALUES(1, 3)"))
        connection.commit()
        execute_data_plan(connection, plan, migration_id="capture")
        assert check_convergence(connection, spec, plans=[plan]) == []

        connection.execute(text("UPDATE items SET value=999 WHERE id=1"))
        connection.commit()
        findings = check_convergence(connection, spec, plans=[plan])
        assert [finding["kind"] for finding in findings] == ["unverifiable_capture"]
