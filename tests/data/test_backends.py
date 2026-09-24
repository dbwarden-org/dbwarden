import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from dbwarden.data.compiler import compile_data, discover_data
from dbwarden.data.execution import execute_data_plan
from dbwarden.data.planning import plan_data
from dbwarden.data.snapshots import register_snapshot


@pytest.fixture(params=["sqlite", "postgresql"])
def backend_connection(request):
    backend = request.param
    url = (
        "sqlite://"
        if backend == "sqlite"
        else os.environ.get("DBWARDEN_DATA_TEST_POSTGRES")
    )
    if not url:
        pytest.skip(
            "Set DBWARDEN_DATA_TEST_POSTGRES to an isolated PostgreSQL database"
        )
    engine = create_engine(url)
    schema = "data_audit_" + uuid.uuid4().hex
    with engine.connect() as connection:
        if backend == "postgresql":
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql(f'SET search_path TO "{schema}"')
            connection.commit()
        try:
            yield backend, connection
        finally:
            connection.rollback()
            if backend == "postgresql":
                connection.exec_driver_sql("SET search_path TO public")
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
                connection.commit()
    engine.dispose()


def _plan(spec):
    ops = plan_data(spec)
    return {
        "data_spec": spec,
        "data_bundle": {},
        "upgrade_ops": ops,
        "data_execution": {
            "upgrade": [step for op in ops for step in op["data_upgrade"]],
            "rollback": [step for op in reversed(ops) for step in op["data_rollback"]],
        },
    }


def test_postgresql_guards_hold_write_lock_through_mutation(
    tmp_path, monkeypatch, backend_connection
):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy.exc import OperationalError

    backend, connection = backend_connection
    if backend != "postgresql":
        pytest.skip("PostgreSQL table-lock concurrency contract")
    monkeypatch.chdir(tmp_path)
    (
        tmp_path / "models.py"
    ).write_text("""from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base
from dbwarden.data import DataMeta, rows
Base = declarative_base()
class Item(Base):
    __tablename__ = 'items'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    class Data(DataMeta):
        managed_rows = rows(rows=[{'id': 1, 'name': 'managed'}], rollback='restore_previous')
""")
    connection.exec_driver_sql(
        "CREATE TABLE items(id INTEGER PRIMARY KEY, name VARCHAR)"
    )
    schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
    connection.commit()
    plan = _plan(
        compile_data(discover_data(["models.py"]), database="primary", backend=backend)
    )
    checked = []

    def concurrent_write():
        with connection.engine.connect() as other:
            other.exec_driver_sql("SET lock_timeout = '100ms'")
            try:
                other.exec_driver_sql(
                    f"INSERT INTO \"{schema}\".items VALUES(1, 'application')"
                )
            except OperationalError as exc:
                return exc.orig.pgcode
            return "write_was_not_blocked"

    def before(_statement):
        if not checked:
            with ThreadPoolExecutor(max_workers=1) as executor:
                assert executor.submit(concurrent_write).result(timeout=5) == "55P03"
            checked.append(True)

    execute_data_plan(
        connection, plan, migration_id="concurrent", before_statement=before
    )
    assert checked
    assert connection.exec_driver_sql("SELECT * FROM items").all() == [(1, "managed")]


def test_native_managed_upsert_and_failure_rollback(
    tmp_path, monkeypatch, backend_connection
):
    backend, connection = backend_connection
    monkeypatch.chdir(tmp_path)
    (
        tmp_path / "models.py"
    ).write_text("""from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base
from dbwarden.data import DataMeta, rows, derive, col, func
Base = declarative_base()
class Item(Base):
    __tablename__ = 'items'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    slug = Column(String)
    class Data(DataMeta):
        managed_rows = rows(rows=[{'id':1,'name':"O'Reilly"}], owned_columns=['name'], rollback='restore_previous')
        transformations = [derive('slug',func.lower(col('name')),rollback='clear')]
""")
    spec = compile_data(
        discover_data(["models.py"]), database="primary", backend=backend
    )
    plan = _plan(spec)
    connection.exec_driver_sql(
        "CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT NOT NULL, slug TEXT)"
    )
    connection.commit()
    execute_data_plan(connection, plan, migration_id="1")
    assert connection.execute(text("SELECT * FROM items")).all() == [
        (1, "O'Reilly", "o'reilly")
    ]
    connection.commit()
    execute_data_plan(connection, plan, migration_id="1", direction="rollback")
    assert connection.execute(text("SELECT COUNT(*) FROM items")).scalar_one() == 0
    connection.commit()
    execute_data_plan(connection, plan, migration_id="1", reapply=True)
    connection.execute(text("UPDATE items SET slug = 'user edit' WHERE id = 1"))
    connection.commit()
    with pytest.raises(ValueError, match="changed after apply"):
        execute_data_plan(connection, plan, migration_id="1", direction="rollback")
    assert (
        connection.execute(text("SELECT slug FROM items")).scalar_one() == "user edit"
    )


@pytest.mark.parametrize("failure", [False, True])
def test_native_split_coverage_identity_and_owned_rollback(
    tmp_path, monkeypatch, backend_connection, failure
):
    backend, connection = backend_connection
    monkeypatch.chdir(tmp_path)
    register_snapshot(
        "primary__0001",
        {
            "tables": {
                "legacy": {
                    "columns": {
                        "id": {"type": "INTEGER", "nullable": False},
                        "name": {"type": "VARCHAR", "nullable": False},
                        "kind": {"type": "VARCHAR", "nullable": False},
                    }
                }
            }
        },
        database="primary",
        backend=backend,
    )
    (
        tmp_path / "models.py"
    ).write_text("""from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base
from dbwarden.data import DataTransition, historical_table, into
Base = declarative_base()
class Person(Base):
    __tablename__ = 'people'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
class Address(Base):
    __tablename__ = 'addresses'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
old = historical_table('legacy', snapshot='primary__0001')
class Split(DataTransition):
    source = old
    source_identity = [old.id]
    coverage = 'exactly_once'
    targets = [into(Person,map={Person.id: old.id,Person.name: old.name},where=old.kind=='person',on_conflict='ignore_if_equivalent'),
               into(Address,map={Address.id: old.id,Address.name: old.name},where=old.kind=='address')]
""")
    spec = compile_data(
        discover_data(["models.py"]), database="primary", backend=backend
    )
    plan = _plan(spec)
    for sql in [
        "CREATE TABLE legacy(id INTEGER PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL)",
        "CREATE TABLE people(id INTEGER PRIMARY KEY,name TEXT NOT NULL)",
        "CREATE TABLE addresses(id INTEGER PRIMARY KEY,name TEXT NOT NULL)",
        "INSERT INTO people VALUES (1,'One')",
        "INSERT INTO legacy VALUES(1,'One','person'),(2,'Two','address')",
    ]:
        connection.exec_driver_sql(sql)
    if failure:
        connection.exec_driver_sql("INSERT INTO legacy VALUES(3,'Three','unknown')")
    connection.commit()
    if failure:
        with pytest.raises(ValueError, match="not covered"):
            execute_data_plan(connection, plan, migration_id="2")
        assert (
            connection.exec_driver_sql("SELECT COUNT(*) FROM legacy").scalar_one() == 3
        )
        assert (
            connection.exec_driver_sql("SELECT COUNT(*) FROM addresses").scalar_one()
            == 0
        )
    else:
        execute_data_plan(connection, plan, migration_id="2")
        assert connection.exec_driver_sql("SELECT * FROM addresses").all() == [
            (2, "Two")
        ]
        connection.commit()
        execute_data_plan(connection, plan, migration_id="2", direction="rollback")
        assert connection.exec_driver_sql("SELECT * FROM people").all() == [(1, "One")]
        assert (
            connection.exec_driver_sql("SELECT COUNT(*) FROM addresses").scalar_one()
            == 0
        )
        assert (
            connection.exec_driver_sql("SELECT COUNT(*) FROM legacy").scalar_one() == 2
        )
