import os
import uuid

import pytest
from sqlalchemy import Column, Integer, String, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import declarative_base

from dbwarden.data import DataMeta, archive_table, col, derive, rows
from dbwarden.data.compiler import DiscoveredData, compile_data, discover_data
from dbwarden.data.execution import DataExecutionError, execute_data_plan
from dbwarden.data.planning import plan_data
from dbwarden.data.snapshots import register_snapshot


@pytest.fixture(params=("mariadb", "mysql"))
def mariadb_connection(request):
    backend = request.param
    url = os.environ.get(f"DBWARDEN_DATA_TEST_{backend.upper()}")
    if not url:
        pytest.skip(
            f"Set DBWARDEN_DATA_TEST_{backend.upper()} to an isolated {backend} database"
        )
    engine = create_engine(url)
    database = "data_audit_" + uuid.uuid4().hex
    with engine.connect() as connection:
        connection.exec_driver_sql(f"CREATE DATABASE `{database}`")
        connection.exec_driver_sql(f"USE `{database}`")
        connection.commit()
        connection.info["dbwarden_backend"] = backend
        try:
            yield connection
        finally:
            connection.rollback()
            connection.exec_driver_sql(f"DROP DATABASE `{database}`")
            connection.commit()
    engine.dispose()


def _plan(spec, previous=None):
    operations = plan_data(spec, previous)
    return {
        "data_spec": spec,
        "data_bundle": {},
        "upgrade_ops": operations,
        "data_execution": {
            "upgrade": [step for item in operations for step in item["data_upgrade"]],
            "rollback": [
                step for item in reversed(operations) for step in item["data_rollback"]
            ],
        },
    }


def _archive_spec(values, backend):
    base = declarative_base()

    class Item(base):
        __tablename__ = "items"
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)

    class Data(DataMeta):
        managed_rows = rows(
            key="id",
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
        DiscoveredData((Item,), ()), database="primary", backend=backend
    )


def _capture_spec(backend):
    base = declarative_base()

    class Item(base):
        __tablename__ = "capture_items"
        id = Column(Integer, primary_key=True)
        source = Column(Integer, nullable=False)
        value = Column(Integer, nullable=False)

    class Data(DataMeta):
        transformations = (derive("value", col("source") + 1, rollback="capture"),)

    Item.Data = Data
    return compile_data(
        DiscoveredData((Item,), ()), database="primary", backend=backend
    )


def _backend(connection):
    return connection.info["dbwarden_backend"]


def _execute_with_sql(mariadb_connection, plan, **kwargs):
    statements = []
    errors = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    def capture_error(context):
        errors.append((context.statement, str(context.original_exception)))

    event.listen(mariadb_connection.engine, "before_cursor_execute", capture)
    event.listen(mariadb_connection.engine, "handle_error", capture_error)
    try:
        return execute_data_plan(mariadb_connection, plan, **kwargs)
    except (DataExecutionError, SQLAlchemyError) as exc:
        pytest.fail(
            f"MariaDB execution SQL: {statements[-12:]}; DBAPI errors: {errors}; error: {exc}"
        )
    finally:
        event.remove(mariadb_connection.engine, "before_cursor_execute", capture)
        event.remove(mariadb_connection.engine, "handle_error", capture_error)


def test_mariadb_managed_rows_revision_rollback_reapply(
    tmp_path, monkeypatch, mariadb_connection
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models.py").write_text(
        """from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base
from dbwarden.data import DataMeta, rows
Base = declarative_base()
class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    class Data(DataMeta):
        managed_rows = rows(key="id", rows=[{"id": 1, "name": "managed"}], owned_columns=["name"], rollback="restore_previous")
""",
        encoding="utf-8",
    )
    mariadb_connection.exec_driver_sql(
        "CREATE TABLE items(id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL) ENGINE=InnoDB"
    )
    mariadb_connection.commit()
    plan = _plan(
        compile_data(
            discover_data(["models.py"]),
            database="primary",
            backend=_backend(mariadb_connection),
        )
    )
    assert (
        execute_data_plan(mariadb_connection, plan, migration_id="managed")["status"]
        == "APPLIED_SUCCESS"
    )
    assert mariadb_connection.execute(text("SELECT id, name FROM items")).all() == [
        (1, "managed")
    ]
    mariadb_connection.commit()
    assert (
        execute_data_plan(
            mariadb_connection, plan, migration_id="managed", direction="rollback"
        )["status"]
        == "ROLLED_BACK"
    )
    assert (
        mariadb_connection.execute(text("SELECT COUNT(*) FROM items")).scalar_one() == 0
    )
    mariadb_connection.commit()
    assert (
        execute_data_plan(
            mariadb_connection, plan, migration_id="managed", reapply=True
        )["status"]
        == "APPLIED_SUCCESS"
    )


def test_mariadb_archive_two_revisions_rolls_back_latest_only(mariadb_connection):
    initial_spec = _archive_spec(
        [{"id": 1, "name": "one"}, {"id": 2, "name": "two"}],
        _backend(mariadb_connection),
    )
    first_archive_spec = _archive_spec(
        [{"id": 2, "name": "two"}], _backend(mariadb_connection)
    )
    second_archive_spec = _archive_spec([], _backend(mariadb_connection))
    initial = _plan(initial_spec)
    first_archive = _plan(first_archive_spec, initial_spec)
    second_archive = _plan(second_archive_spec, first_archive_spec)
    mariadb_connection.exec_driver_sql(
        "CREATE TABLE items(id INT PRIMARY KEY, name VARCHAR(64) NOT NULL) ENGINE=InnoDB"
    )
    mariadb_connection.exec_driver_sql(
        "CREATE TABLE items_archive(id INT PRIMARY KEY, name VARCHAR(64) NOT NULL) ENGINE=InnoDB"
    )
    mariadb_connection.commit()
    assert (
        _execute_with_sql(mariadb_connection, initial, migration_id="archive-initial")[
            "status"
        ]
        == "APPLIED_SUCCESS"
    )
    assert (
        _execute_with_sql(
            mariadb_connection, first_archive, migration_id="archive-one"
        )["status"]
        == "APPLIED_SUCCESS"
    )
    assert mariadb_connection.execute(
        text("SELECT id FROM items_archive")
    ).scalars().all() == [1]
    assert (
        _execute_with_sql(
            mariadb_connection, second_archive, migration_id="archive-two"
        )["status"]
        == "APPLIED_SUCCESS"
    )
    assert mariadb_connection.execute(
        text("SELECT id FROM items_archive ORDER BY id")
    ).scalars().all() == [1, 2]
    mariadb_connection.commit()
    assert (
        _execute_with_sql(
            mariadb_connection,
            second_archive,
            migration_id="archive-two",
            direction="rollback",
        )["status"]
        == "ROLLED_BACK"
    )
    assert mariadb_connection.execute(
        text("SELECT id FROM items ORDER BY id")
    ).scalars().all() == [2]
    assert mariadb_connection.execute(
        text("SELECT id FROM items_archive")
    ).scalars().all() == [1]


def test_mariadb_capture_derivation_restores_preimage(mariadb_connection):
    spec = _capture_spec(_backend(mariadb_connection))
    plan = _plan(spec)
    mariadb_connection.exec_driver_sql(
        "CREATE TABLE capture_items(id INT PRIMARY KEY, source INT NOT NULL, value INT NOT NULL) ENGINE=InnoDB"
    )
    mariadb_connection.exec_driver_sql("INSERT INTO capture_items VALUES(1, 7, 3)")
    mariadb_connection.commit()
    assert (
        _execute_with_sql(mariadb_connection, plan, migration_id="capture")["status"]
        == "APPLIED_SUCCESS"
    )
    assert (
        mariadb_connection.execute(text("SELECT value FROM capture_items")).scalar_one()
        == 8
    )
    mariadb_connection.commit()
    assert (
        _execute_with_sql(
            mariadb_connection, plan, migration_id="capture", direction="rollback"
        )["status"]
        == "ROLLED_BACK"
    )
    assert (
        mariadb_connection.execute(text("SELECT value FROM capture_items")).scalar_one()
        == 3
    )


def test_mariadb_transition_preserves_source_and_rolls_back(
    tmp_path, monkeypatch, mariadb_connection
):
    monkeypatch.chdir(tmp_path)
    register_snapshot(
        "mariadb_source",
        {
            "tables": {
                "legacy": {
                    "columns": {
                        "id": {"type": "INTEGER", "nullable": False},
                        "name": {"type": "VARCHAR", "nullable": False},
                    }
                }
            }
        },
        database="primary",
        backend=_backend(mariadb_connection),
    )
    (tmp_path / "models.py").write_text(
        """from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base
from dbwarden.data import DataTransition, historical_table, into
Base = declarative_base()
class Person(Base):
    __tablename__ = "people"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
old = historical_table("legacy", snapshot="mariadb_source")
class Move(DataTransition):
    source = old
    source_identity = [old.id]
    targets = [into(Person, map={Person.id: old.id, Person.name: old.name}, key=[Person.id])]
""",
        encoding="utf-8",
    )
    for statement in (
        "CREATE TABLE legacy(id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL) ENGINE=InnoDB",
        "CREATE TABLE people(id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL) ENGINE=InnoDB",
        "INSERT INTO legacy VALUES(1, 'one'), (2, 'two')",
    ):
        mariadb_connection.exec_driver_sql(statement)
    mariadb_connection.commit()
    plan = _plan(
        compile_data(
            discover_data(["models.py"]),
            database="primary",
            backend=_backend(mariadb_connection),
        )
    )
    assert (
        _execute_with_sql(mariadb_connection, plan, migration_id="transition")["status"]
        == "APPLIED_SUCCESS"
    )
    assert mariadb_connection.execute(
        text("SELECT id, name FROM people ORDER BY id")
    ).all() == [(1, "one"), (2, "two")]
    assert mariadb_connection.execute(
        text("SHOW TABLES LIKE '_dbwarden_preserved_%'")
    ).all()
    mariadb_connection.commit()
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(
        mariadb_connection.engine,
        "before_cursor_execute",
        capture,
    )
    try:
        result = execute_data_plan(
            mariadb_connection, plan, migration_id="transition", direction="rollback"
        )
    except (DataExecutionError, SQLAlchemyError) as exc:
        pytest.fail(f"MariaDB transition rollback SQL: {statements[-8:]}; error: {exc}")
    finally:
        event.remove(mariadb_connection.engine, "before_cursor_execute", capture)
    assert result["status"] == "ROLLED_BACK"
    assert mariadb_connection.execute(
        text("SELECT id, name FROM legacy ORDER BY id")
    ).all() == [(1, "one"), (2, "two")]


def test_mariadb_many_source_merge_batches_round_trip(
    tmp_path, monkeypatch, mariadb_connection
):
    monkeypatch.chdir(tmp_path)
    register_snapshot(
        "mariadb_merge",
        {
            "tables": {
                table: {
                    "columns": {
                        "id": {"type": "INTEGER", "nullable": False},
                        "account": {"type": "INTEGER", "nullable": False},
                        "amount": {"type": "INTEGER", "nullable": False},
                        "name": {"type": "VARCHAR", "nullable": False},
                    }
                }
                for table in ("legacy_a", "legacy_b")
            }
        },
        database="primary",
        backend=_backend(mariadb_connection),
    )
    (tmp_path / "models.py").write_text(
        """from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base
from dbwarden.data import DataTransition, aggregate, batch, col, from_source, historical_table, into, merge_sources, winner
Base = declarative_base()
class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    total = Column(Integer, nullable=False)
    name = Column(String, nullable=False)
a = historical_table("legacy_a", snapshot="mariadb_merge")
b = historical_table("legacy_b", snapshot="mariadb_merge")
class Merge(DataTransition):
    source = merge_sources(from_source(a, identity=["id"], map={"id": col("account"), "amount": col("amount"), "name": col("name")}, priority=0), from_source(b, identity=["id"], map={"id": col("account"), "amount": col("amount"), "name": col("name")}, priority=1), key=["id"], values={"total": aggregate("sum", col("amount")), "name": winner(col("name"))})
    targets = [into(Account, map={Account.id: col("id"), Account.total: col("total"), Account.name: col("name")}, key=[Account.id])]
    execution = batch(size=2, key=["id"])
""",
        encoding="utf-8",
    )
    for table in ("legacy_a", "legacy_b"):
        mariadb_connection.exec_driver_sql(
            f"CREATE TABLE {table}(id INT PRIMARY KEY, account INT NOT NULL, amount INT NOT NULL, name VARCHAR(64) NOT NULL) ENGINE=InnoDB"
        )
    mariadb_connection.exec_driver_sql(
        "CREATE TABLE accounts(id INT PRIMARY KEY,total INT NOT NULL,name VARCHAR(64) NOT NULL) ENGINE=InnoDB"
    )
    mariadb_connection.exec_driver_sql(
        "INSERT INTO legacy_a VALUES(1,10,2,'first'),(2,10,3,'second'),(3,20,5,'twenty')"
    )
    mariadb_connection.exec_driver_sql(
        "INSERT INTO legacy_b VALUES(1,10,7,'other'),(2,30,11,'thirty')"
    )
    mariadb_connection.commit()
    plan = _plan(
        compile_data(
            discover_data(["models.py"]),
            database="primary",
            backend=_backend(mariadb_connection),
        )
    )
    assert any(step.get("batches") for step in plan["data_execution"]["upgrade"])
    assert (
        _execute_with_sql(mariadb_connection, plan, migration_id="merge")["status"]
        == "APPLIED_SUCCESS"
    )
    assert mariadb_connection.execute(
        text("SELECT id,total,name FROM accounts ORDER BY id")
    ).all() == [(10, 12, "first"), (20, 5, "twenty"), (30, 11, "thirty")]
    mariadb_connection.commit()
    assert (
        execute_data_plan(
            mariadb_connection, plan, migration_id="merge", direction="rollback"
        )["status"]
        == "ROLLED_BACK"
    )
    assert (
        mariadb_connection.execute(text("SELECT COUNT(*) FROM legacy_a")).scalar_one()
        == 3
    )
    mariadb_connection.commit()
    assert (
        execute_data_plan(mariadb_connection, plan, migration_id="merge", reapply=True)[
            "status"
        ]
        == "APPLIED_SUCCESS"
    )


def test_mariadb_retained_merge_source_tamper_blocks_rollback(
    tmp_path, monkeypatch, mariadb_connection
):
    monkeypatch.chdir(tmp_path)
    register_snapshot(
        "mariadb_retained_merge",
        {
            "tables": {
                table: {
                    "columns": {
                        "id": {"type": "INTEGER", "nullable": False},
                        "account": {"type": "INTEGER", "nullable": False},
                        "amount": {"type": "INTEGER", "nullable": False},
                        "name": {"type": "VARCHAR", "nullable": False},
                    }
                }
                for table in ("legacy_a", "legacy_b")
            }
        },
        database="primary",
        backend=_backend(mariadb_connection),
    )
    (tmp_path / "models.py").write_text(
        """from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base
from dbwarden.data import DataTransition, aggregate, col, from_source, historical_table, into, merge_sources, winner
Base = declarative_base()
class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    total = Column(Integer, nullable=False)
    name = Column(String, nullable=False)
a = historical_table("legacy_a", snapshot="mariadb_retained_merge")
b = historical_table("legacy_b", snapshot="mariadb_retained_merge")
class Merge(DataTransition):
    source = merge_sources(from_source(a, identity=["id"], map={"id": col("account"), "amount": col("amount"), "name": col("name")}, priority=0), from_source(b, identity=["id"], map={"id": col("account"), "amount": col("amount"), "name": col("name")}, priority=1), key=["id"], values={"total": aggregate("sum", col("amount")), "name": winner(col("name"))})
    targets = [into(Account, map={Account.id: col("id"), Account.total: col("total"), Account.name: col("name")}, key=[Account.id])]
""",
        encoding="utf-8",
    )
    for table in ("legacy_a", "legacy_b"):
        mariadb_connection.exec_driver_sql(
            f"CREATE TABLE {table}(id INT PRIMARY KEY, account INT NOT NULL, amount INT NOT NULL, name VARCHAR(64) NOT NULL) ENGINE=InnoDB"
        )
    mariadb_connection.exec_driver_sql(
        "CREATE TABLE accounts(id INT PRIMARY KEY,total INT NOT NULL,name VARCHAR(64) NOT NULL) ENGINE=InnoDB"
    )
    mariadb_connection.exec_driver_sql(
        "INSERT INTO legacy_a VALUES(1,10,2,'first'),(2,10,3,'second')"
    )
    mariadb_connection.exec_driver_sql("INSERT INTO legacy_b VALUES(1,10,7,'other')")
    mariadb_connection.commit()
    plan = _plan(
        compile_data(
            discover_data(["models.py"]),
            database="primary",
            backend=_backend(mariadb_connection),
        )
    )
    assert (
        _execute_with_sql(mariadb_connection, plan, migration_id="retained-merge")[
            "status"
        ]
        == "APPLIED_SUCCESS"
    )
    retained = next(
        step["identity_edges"]["retained_source"]["table"]["table"]
        for step in plan["data_execution"]["upgrade"]
        if step.get("identity_edges", {}).get("retained_source")
    )
    mariadb_connection.execute(text(f"UPDATE `{retained}` SET amount=999 WHERE id=1"))
    mariadb_connection.commit()
    with pytest.raises(ValueError, match="Retained merge source"):
        execute_data_plan(
            mariadb_connection,
            plan,
            migration_id="retained-merge",
            direction="rollback",
        )
    assert (
        mariadb_connection.execute(text("SELECT COUNT(*) FROM accounts")).scalar_one()
        == 1
    )
