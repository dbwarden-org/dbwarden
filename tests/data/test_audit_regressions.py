import sqlite3
from copy import deepcopy

import pytest
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import declarative_base

from dbwarden.data.compiler import DiscoveredData, compile_data, discover_data
from dbwarden.data.convergence import check_convergence
from dbwarden.data.declarations import (
    DataMeta,
    DataTransition,
    derive,
    historical_table,
    into,
    rows,
    validate,
)
from dbwarden.data.execution import DataExecutionError, execute_data_plan
from dbwarden.data.expressions import canonical_expression, col, func, literal
from dbwarden.data.ir import digest, seal_spec
from dbwarden.data.planning import plan_data
from dbwarden.data.snapshots import register_snapshot


def _plan(spec, previous=None, migration_id="migration"):
    ops = plan_data(spec, previous)
    return {
        "migration_id": migration_id,
        "data_spec": spec,
        "data_bundle": {},
        "upgrade_ops": ops,
        "data_execution": {
            "upgrade": [step for op in ops for step in op["data_upgrade"]],
            "rollback": [step for op in reversed(ops) for step in op["data_rollback"]],
        },
    }


def _reseal_declarations(spec):
    declarations = [
        item
        for kind in ("managed_rows", "transformations", "validations", "transitions")
        for item in spec.get(kind, [])
    ]
    ordered = sorted(declarations, key=lambda item: item["declaration_id"])
    spec["declaration_set"] = {
        "declaration_ids": [item["declaration_id"] for item in ordered],
        "declaration_checksum": digest(ordered, "declarations"),
    }
    return seal_spec(spec)


def _managed_spec(values):
    base = declarative_base()

    class Item(base):
        __tablename__ = "items"
        id = Column(Integer, primary_key=True)
        name = Column(String)

    declaration = rows(
        rows=values,
        owned_columns=["name"],
        rollback="restore_previous",
    )

    class Data(DataMeta):
        managed_rows = declaration

    Item.Data = Data
    return compile_data(
        DiscoveredData((Item,), ()), database="primary", backend="sqlite"
    )


def test_discovery_rejects_missing_and_symlinked_configured_paths(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        discover_data([tmp_path / "missing.py"])
    source = tmp_path / "models.py"
    source.write_text("")
    link = tmp_path / "linked.py"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("Symlinks are unavailable")
    with pytest.raises(ValueError, match="symlink"):
        discover_data([link])


def test_discovery_rejects_duplicate_table_owners(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "one.py").write_text(
        "from sqlalchemy import Column,Integer\n"
        "from sqlalchemy.orm import declarative_base\n"
        "Base=declarative_base()\n"
        "class One(Base):\n __tablename__='items'\n id=Column(Integer,primary_key=True)\n"
    )
    (tmp_path / "two.py").write_text(
        "from sqlalchemy import Column,Integer\n"
        "from sqlalchemy.orm import declarative_base\n"
        "Base=declarative_base()\n"
        "class Two(Base):\n __tablename__='items'\n id=Column(Integer,primary_key=True)\n"
    )
    with pytest.raises(ValueError, match="Multiple mapped classes"):
        discover_data(["one.py", "two.py"])


def test_compiled_spec_freezes_compact_model_schema_without_row_values():
    base = declarative_base()

    class Parent(base):
        __tablename__ = "parents"
        id = Column(Integer, primary_key=True)

    class Item(base):
        __tablename__ = "items"
        __table_args__ = (
            UniqueConstraint("name", name="uq_items_name"),
            CheckConstraint("id > 0", name="ck_items_id"),
        )
        id = Column(Integer, primary_key=True)
        parent_id = Column(Integer, ForeignKey("parents.id"), nullable=False)
        name = Column(String, nullable=False)

    class Data(DataMeta):
        managed_rows = rows(
            rows=[{"id": 1, "parent_id": 2, "name": "secret row"}],
            rollback="irreversible",
        )

    Item.Data = Data
    spec = compile_data(
        DiscoveredData((Item, Parent), ()), database="primary", backend="sqlite"
    )
    frozen = str(spec["model_schema"])
    assert "secret row" not in frozen
    item = next(table for table in spec["model_schema"] if table["name"] == "items")
    assert item["foreign_keys"][0]["target"] == {
        "schema": None,
        "table": "parents",
        "columns": ["id"],
    }
    assert item["unique_constraints"] == [
        {"name": "uq_items_name", "columns": ["name"]}
    ]
    assert item["check_constraints"] == [
        {"name": "ck_items_id", "expression": "id > 0"}
    ]


def test_managed_revision_never_claims_or_deletes_preexisting_new_key():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT)"
        )
        old = _managed_spec([{"id": 1, "name": "one"}])
        execute_data_plan(
            connection, _plan(old, migration_id="initial"), migration_id="initial"
        )
        connection.execute(text("INSERT INTO items VALUES (2, 'external')"))
        connection.commit()

        current = _managed_spec([{"id": 1, "name": "one"}, {"id": 2, "name": "two"}])
        revision = _plan(current, old, "revision")
        with pytest.raises(ValueError, match="must not replace pre-existing"):
            execute_data_plan(connection, revision, migration_id="revision")
        assert (
            connection.execute(text("SELECT name FROM items WHERE id=2")).scalar_one()
            == "external"
        )

        connection.execute(text("DELETE FROM items WHERE id=2"))
        connection.commit()
        clean_revision = _plan(current, old, "revision-clean")
        execute_data_plan(connection, clean_revision, migration_id="revision-clean")
        connection.commit()
        execute_data_plan(
            connection,
            clean_revision,
            migration_id="revision-clean",
            direction="rollback",
        )
        ledger = (
            "_dbwarden_rows_"
            + digest(current["managed_rows"][0]["declaration_id"], "ownership")[:20]
        )
        assert connection.execute(text("SELECT * FROM items")).all() == [(1, "one")]
        assert connection.execute(text(f'SELECT * FROM "{ledger}"')).all() == [(1,)]


def test_managed_convergence_reports_scoped_owned_stale_row():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT)"
        )
        old = _managed_spec([{"id": 1, "name": "one"}, {"id": 2, "name": "two"}])
        execute_data_plan(
            connection, _plan(old, migration_id="initial"), migration_id="initial"
        )
        current = deepcopy(old)
        item = current["managed_rows"][0]
        item["row_source"]["rows"] = [{"id": 1, "name": "one"}]
        item["on_missing"] = "delete"
        item["scope"] = canonical_expression(literal(True), columns=[])
        findings = check_convergence(connection, current)
        assert any(
            finding["message"] == "Owned row remains after scoped deletion"
            for finding in findings
        )


def test_validation_only_data_is_planned_and_converged():
    base = declarative_base()

    class Item(base):
        __tablename__ = "items"
        id = Column(Integer, primary_key=True)
        value = Column(Integer)

    class Data(DataMeta):
        validations = (validate(col("value") > 0, message="value must be positive"),)

    Item.Data = Data
    spec = compile_data(
        DiscoveredData((Item,), ()), database="primary", backend="sqlite"
    )
    assert not spec["managed_rows"] and not spec["transformations"]
    assert len(spec["validations"]) == 1
    plan = _plan(spec, migration_id="validation")
    assert plan["upgrade_ops"][0]["data_kind"] == "validations"
    assert plan["upgrade_ops"][0]["data_upgrade"][0]["sql"] == []

    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql("CREATE TABLE items(id INTEGER, value INTEGER)")
        connection.exec_driver_sql("INSERT INTO items VALUES (1, NULL)")
        connection.commit()
        with pytest.raises(ValueError, match="value must be positive"):
            execute_data_plan(connection, plan, migration_id="validation")
        findings = check_convergence(connection, spec)
        assert [finding["message"] for finding in findings] == [
            "value must be positive"
        ]


def test_transformation_errors_on_unmatched_and_does_not_rewrite_equal_rows():
    base = declarative_base()

    class Item(base):
        __tablename__ = "items"
        id = Column(Integer, primary_key=True)
        name = Column(String)
        slug = Column(String)

    class Data(DataMeta):
        transformations = (
            derive(
                "slug",
                func.lower(col("name")),
                when=col("id") > 0,
                rollback="irreversible",
            ),
        )

    Item.Data = Data
    spec = compile_data(
        DiscoveredData((Item,), ()), database="primary", backend="sqlite"
    )
    plan = _plan(spec)
    sql = plan["upgrade_ops"][0]["data_upgrade"][0]["sql"][0]
    assert "IS NOT DISTINCT FROM" in sql

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE items(id INTEGER, name TEXT, slug TEXT);"
        "CREATE TABLE writes(value INTEGER);"
        "CREATE TRIGGER audit AFTER UPDATE ON items BEGIN INSERT INTO writes VALUES(1); END;"
        "INSERT INTO items VALUES(1, 'ONE', NULL);"
    )
    connection.execute(sql)
    connection.execute(sql)
    assert connection.execute("SELECT COUNT(*) FROM writes").fetchone() == (1,)
    connection.execute("INSERT INTO items VALUES(-1, 'OUTSIDE', NULL)")
    connection.commit()
    engine = create_engine("sqlite://", creator=lambda: connection)
    with engine.connect() as wrapped:
        findings = check_convergence(wrapped, spec)
    assert any(
        "outside the transformation domain" in item["message"].lower()
        for item in findings
    )


def _transition_spec(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    register_snapshot(
        "primary__audit",
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
        backend="sqlite",
    )
    base = declarative_base()

    class Person(base):
        __tablename__ = "people"
        id = Column(Integer, primary_key=True)
        name = Column(String)

    class Address(base):
        __tablename__ = "addresses"
        id = Column(Integer, primary_key=True)
        name = Column(String)

    Person.declaration_id = "audit.person"
    Address.declaration_id = "audit.address"

    old = historical_table("legacy", snapshot="primary__audit")

    class Split(DataTransition):
        declaration_id = "audit.split"
        source = old
        source_identity = (old.id,)
        coverage = "exactly_once"
        targets = (
            into(
                Person,
                map={Person.id: old.id, Person.name: old.name},
                where=old.kind.in_(("person", "both")),
            ),
            into(
                Address,
                map={Address.id: old.id, Address.name: old.name},
                where=old.kind.in_(("address", "both")),
            ),
        )

    return compile_data(
        DiscoveredData((Person, Address), (Split,)),
        database="primary",
        backend="sqlite",
    )


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ((3, "Three", "unknown"), "not covered"),
        ((1, "One again", "person"), "not unique"),
        ((3, "Three", "both"), "multiple targets"),
    ],
)
def test_transition_convergence_rechecks_preserved_source_invariants(
    tmp_path, monkeypatch, row, message
):
    spec = _transition_spec(tmp_path, monkeypatch)
    plan = _plan(spec, migration_id="transition")
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        for sql in (
            "CREATE TABLE legacy(id INTEGER, name TEXT, kind TEXT)",
            "CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT)",
            "CREATE TABLE addresses(id INTEGER PRIMARY KEY, name TEXT)",
            "INSERT INTO legacy VALUES(1,'One','person'),(2,'Two','address')",
        ):
            connection.exec_driver_sql(sql)
        connection.commit()
        execute_data_plan(connection, plan, migration_id="transition")
        preserved = spec["transitions"][0]["rollback"]["preservation_ref"]
        connection.execute(
            text(f'INSERT INTO "{preserved}" VALUES(:id,:name,:kind)'),
            dict(zip(("id", "name", "kind"), row)),
        )
        connection.commit()
        findings = check_convergence(connection, spec, plans=[plan])
        assert any(message in finding["message"] for finding in findings)


def test_transition_convergence_matches_exact_id_and_qualified_source():
    engine = create_engine("sqlite://")
    item = {
        "declaration_id": "split",
        "transition_id": "current",
        "source": {"database": "primary", "schema": "old", "table": "legacy"},
        "rollback": {"preservation_ref": "preserved"},
    }
    exact_op = {
        "data_kind": "transitions",
        "declaration_id": "split",
        "data_declaration": item,
        "runtime_source": item["source"],
        "data_upgrade": [
            {
                "sql": [],
                "guards": [
                    {
                        "timing": "after",
                        "message": "qualified target drift",
                        "query": "SELECT COUNT(*) FROM old.legacy s WHERE NOT EXISTS (SELECT 1 FROM new.legacy t WHERE t.id = s.id)",
                    }
                ],
            }
        ],
    }
    wrong_op = {
        **exact_op,
        "data_declaration": {**item, "transition_id": "old"},
    }
    with engine.connect() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS old")
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS new")
        connection.exec_driver_sql("CREATE TABLE old.preserved(id INTEGER)")
        connection.exec_driver_sql("CREATE TABLE new.legacy(id INTEGER)")
        connection.exec_driver_sql("INSERT INTO old.preserved VALUES(1)")
        connection.exec_driver_sql("INSERT INTO new.legacy VALUES(1)")
        spec = {
            "database": {"name": "primary", "backend": "sqlite"},
            "managed_rows": [],
            "transformations": [],
            "validations": [],
            "transitions": [item],
        }
        from dbwarden.data.execution import _create_journal, _plan_checksum

        plans = [
            {
                "migration_id": name,
                "upgrade_ops": [op],
                "data_spec": spec,
                "data_bundle": {},
                "data_execution": {"upgrade": [], "rollback": []},
            }
            for name, op in (("wrong", wrong_op), ("exact", exact_op))
        ]
        _create_journal(connection)
        for plan in plans:
            connection.execute(
                text(
                    "INSERT INTO _dbwarden_data_runs(migration_id,epoch,status,baseline,checksum,run_id,direction,started_at) VALUES(:id,1,'APPLIED_SUCCESS',0,:checksum,'test','upgrade','test')"
                ),
                {"id": plan["migration_id"], "checksum": _plan_checksum(plan)},
            )
        findings = check_convergence(
            connection,
            spec,
            plans=plans,
        )
        assert [finding["kind"] for finding in findings] == ["unverifiable_transition"]
        assert not any(
            finding["message"] == "qualified target drift" for finding in findings
        )
        assert (
            check_convergence(
                connection,
                spec,
                plans=plans[:1],
            )[0]["kind"]
            == "unapplied_transition"
        )


def test_preserved_transition_convergence_requires_authenticated_edges(
    tmp_path, monkeypatch
):
    spec = _transition_spec(tmp_path, monkeypatch)
    plan = _plan(spec, migration_id="edge_tamper")
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        for sql in (
            "CREATE TABLE legacy(id INTEGER, name TEXT, kind TEXT)",
            "CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT)",
            "CREATE TABLE addresses(id INTEGER PRIMARY KEY, name TEXT)",
            "INSERT INTO legacy VALUES(1,'One','person'),(2,'Two','address')",
        ):
            connection.exec_driver_sql(sql)
        connection.commit()
        execute_data_plan(connection, plan, migration_id="edge_tamper")
        assert check_convergence(connection, spec, plans=[plan]) == []
        connection.exec_driver_sql(
            "UPDATE _dbwarden_data_edges SET owned_write = 0 WHERE owned_write = 1"
        )
        findings = check_convergence(connection, spec, plans=[plan])
        assert any(item["kind"] == "unverifiable_transition" for item in findings)
        connection.commit()
        with pytest.raises((RuntimeError, ValueError), match="[Ii]dentity.edge"):
            execute_data_plan(
                connection, plan, migration_id="edge_tamper", direction="rollback"
            )
        assert connection.exec_driver_sql("SELECT * FROM people").all() == [(1, "One")]
        assert connection.exec_driver_sql("SELECT * FROM addresses").all() == [
            (2, "Two")
        ]


@pytest.mark.parametrize(
    "dependent",
    [
        "CREATE VIEW country_report AS SELECT * FROM countries",
        "CREATE TABLE app_orders(country TEXT REFERENCES countries(code))",
        "CREATE TRIGGER app_audit AFTER UPDATE ON countries BEGIN SELECT 1; END",
    ],
)
def test_created_target_rollback_preserves_application_dependencies(
    tmp_path, monkeypatch, dependent
):
    import sqlite3

    from dbwarden.commands.migrate import migrate_cmd
    from dbwarden.commands.rollback import rollback_cmd
    from tests.data.test_rollback_targets import _make_project

    _make_project(tmp_path, monkeypatch)
    migrate_cmd(database="primary", force=True)
    with sqlite3.connect(tmp_path / "target.db") as connection:
        connection.execute(dependent)
    with pytest.raises(ValueError, match="dependent"):
        rollback_cmd(database="primary", count=1)
    with sqlite3.connect(tmp_path / "target.db") as connection:
        assert connection.execute("SELECT * FROM countries").fetchall() == [
            ("UY", "Uruguay")
        ]


def test_transition_revision_uses_preserved_source_and_fails_before_writes(
    tmp_path, monkeypatch
):
    previous = _transition_spec(tmp_path, monkeypatch)
    current = deepcopy(previous)
    item = current["transitions"][0]
    item["source_snapshot"]["snapshot_id"] = "primary__new_snapshot"
    item["transition_id"] = "revision"
    item["rollback"]["preservation_ref"] = "revision_preserved"
    current = _reseal_declarations(current)
    plan = _plan(current, previous, "revision")
    operation = plan["upgrade_ops"][0]
    prior_preserved = previous["transitions"][0]["rollback"]["preservation_ref"]
    assert operation["runtime_source"]["table"] == prior_preserved
    assert operation["data_upgrade"][0]["sql"] == []

    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE addresses(id INTEGER PRIMARY KEY, name TEXT)"
        )
        connection.commit()
        with pytest.raises(DataExecutionError, match="OperationalError"):
            execute_data_plan(connection, plan, migration_id="revision")
        assert connection.execute(text("SELECT COUNT(*) FROM people")).scalar_one() == 0
        assert (
            connection.execute(text("SELECT COUNT(*) FROM addresses")).scalar_one() == 0
        )

    unsafe = deepcopy(previous)
    unsafe["transitions"][0]["completion"]["policy"] = "drop"
    unsafe["transitions"][0]["rollback"]["preservation_ref"] = None
    unsafe = _reseal_declarations(unsafe)
    with pytest.raises(ValueError, match="prior source to be preserved"):
        plan_data(current, unsafe)


def test_dropped_source_convergence_uses_hmac_value_edges(tmp_path, monkeypatch):
    spec = _transition_spec(tmp_path, monkeypatch)
    item = spec["transitions"][0]
    item["completion"] = {"policy": "drop", "acknowledgement": True}
    item["rollback"]["policy"] = "irreversible"
    item["rollback"]["preservation_ref"] = None
    item["transition_id"] = "dropproof"
    spec = _reseal_declarations(spec)
    plan = _plan(spec, migration_id="dropproof")
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        for sql in (
            "CREATE TABLE legacy(id INTEGER, name TEXT, kind TEXT)",
            "CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT)",
            "CREATE TABLE addresses(id INTEGER PRIMARY KEY, name TEXT)",
            "INSERT INTO legacy VALUES(1,'One','person'),(2,'Two','address')",
        ):
            connection.exec_driver_sql(sql)
        connection.commit()
        execute_data_plan(connection, plan, migration_id="dropproof")
        assert not inspect(connection).has_table("legacy")
        assert check_convergence(connection, spec, plans=[plan]) == []
        connection.execute(text("UPDATE people SET name='changed' WHERE id=1"))
        connection.commit()
        assert (
            check_convergence(connection, spec, plans=[plan])[0]["kind"]
            == "unverifiable_transition"
        )
