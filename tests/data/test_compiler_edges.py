from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import JSON, Column, DateTime, Integer, MetaData, Table
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from dbwarden.data.compiler import (
    DiscoveredData,
    _latest_compatible_snapshot,
    _load_rows,
    _value,
    compile_data,
    required_parameters,
)
from dbwarden.data.declarations import (
    DataMeta,
    DataTransition,
    archive_table,
    derive,
    historical_table,
    into,
    rows,
    validate,
)
from dbwarden.data.expressions import col, param
from dbwarden.data.planning import plan_data
from dbwarden.data.snapshots import register_snapshot


def test_managed_json_accepts_any_json_shape_and_datetime_requires_timezone():
    metadata = MetaData()
    table = Table(
        "items",
        metadata,
        Column("payload", JSON, nullable=False),
        Column("created_at", DateTime, nullable=False),
    )
    assert _value([1, {"ok": True}], table.c.payload) == [1, {"ok": True}]
    with pytest.raises(ValueError, match="timezone-aware"):
        _value(datetime(2026, 1, 1), table.c.created_at)  # noqa: DTZ001


def test_static_sources_reject_absolute_paths_and_normalized_duplicate_csv_headers(
    tmp_path,
):
    table = Table("items", MetaData(), Column("id", Integer, primary_key=True))
    source = tmp_path / "rows.csv"
    source.write_text("id,e\u0301,é\n1,a,b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="headers"):
        _load_rows(SimpleNamespace(values=None, source="rows.csv"), table, tmp_path)
    with pytest.raises(ValueError, match="inside the project"):
        _load_rows(SimpleNamespace(values=None, source=str(source)), table, tmp_path)


def test_compile_filters_parameters_per_expression_then_rejects_global_extras():
    class Base(DeclarativeBase):
        pass

    class Item(Base):
        __tablename__ = "items"
        id: Mapped[int] = mapped_column(primary_key=True)

        class Data(DataMeta):
            validations = (
                validate(param("first", bool), message="first"),
                validate(param("second", bool), message="second"),
            )

    discovered = DiscoveredData((Item,), ())
    assert required_parameters(discovered) == ("first", "second")
    spec = compile_data(
        discovered,
        database="primary",
        backend="sqlite",
        parameters={"first": True, "second": True},
    )
    assert len(spec["validations"][0]["validations"]) == 2
    with pytest.raises(ValueError, match="Unknown bound parameters: unused"):
        compile_data(
            discovered,
            database="primary",
            backend="sqlite",
            parameters={"first": True, "second": True, "unused": False},
        )


def test_compiler_freezes_target_cast_while_retaining_loss_guard_expression():
    class Base(DeclarativeBase):
        pass

    class Item(Base):
        __tablename__ = "cast_items"
        id: Mapped[int] = mapped_column(primary_key=True)
        value: Mapped[int] = mapped_column(Integer, nullable=False)

        class Data(DataMeta):
            transformations = (derive("value", col("value") + 1, rollback="capture"),)

    item = compile_data(
        DiscoveredData((Item,), ()), database="primary", backend="mariadb"
    )["transformations"][0]
    assert item["expression"]["canonical_ast"]["op"] == "cast"
    assert item["source_expression"]["canonical_ast"]["op"] == "add"
    assert "CAST(" in item["expression"]["normalized_sql"]
    assert " AS SIGNED)" in item["expression"]["normalized_sql"]


def test_self_referential_transformation_requires_capture():
    class Base(DeclarativeBase):
        pass

    class Item(Base):
        __tablename__ = "self_update_items"
        id: Mapped[int] = mapped_column(primary_key=True)
        value: Mapped[int] = mapped_column(Integer, nullable=False)

        class Data(DataMeta):
            transformations = (
                derive("value", col("value") + 1, rollback="irreversible"),
            )

    with pytest.raises(ValueError, match="requires rollback=capture"):
        compile_data(DiscoveredData((Item,), ()), database="primary", backend="sqlite")


def test_archive_destination_cannot_overlap_a_desired_model():
    class Base(DeclarativeBase):
        pass

    class Archive(Base):
        __tablename__ = "items_archive"
        id: Mapped[int] = mapped_column(primary_key=True)

    class Item(Base):
        __tablename__ = "archive_items"
        id: Mapped[int] = mapped_column(primary_key=True)

        class Data(DataMeta):
            managed_rows = rows(
                rows=[{"id": 1}],
                on_missing="archive",
                scope=col("id") > 0,
                archive_to=archive_table("items_archive"),
                acknowledge_archive=True,
                rollback="restore_previous",
            )

    with pytest.raises(ValueError, match="overlaps a desired model"):
        compile_data(
            DiscoveredData((Archive, Item), ()),
            database="primary",
            backend="sqlite",
        )


def _legacy_state():
    return {
        "tables": {
            "legacy": {
                "columns": {
                    "id": {"type": "INTEGER", "nullable": False},
                    "name": {"type": "VARCHAR", "nullable": False},
                }
            }
        }
    }


def test_unpinned_historical_source_freezes_unique_latest_compatible_snapshot(tmp_path):
    registry = tmp_path / "registry.json"
    register_snapshot(
        "first",
        _legacy_state(),
        database="primary",
        backend="sqlite",
        registry_path=registry,
    )
    register_snapshot(
        "second",
        _legacy_state(),
        database="primary",
        backend="sqlite",
        parent_snapshot_ids=["first"],
        registry_path=registry,
    )

    class Base(DeclarativeBase):
        pass

    class Target(Base):
        __tablename__ = "target_latest"
        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str]

    old = historical_table("legacy")

    class Move(DataTransition):
        source = old
        source_identity = (old.id,)
        targets = (into(Target, map={"id": old.id, "name": old.name}),)

    spec = compile_data(
        DiscoveredData((Target,), (Move,)),
        database="primary",
        backend="sqlite",
        registry_path=registry,
    )
    assert spec["transitions"][0]["source_snapshot"]["snapshot_id"] == "second"


def test_unpinned_historical_source_rejects_branch_ambiguity(tmp_path):
    registry = tmp_path / "registry.json"
    register_snapshot(
        "root",
        _legacy_state(),
        database="primary",
        backend="sqlite",
        registry_path=registry,
    )
    for identity in ("left", "right"):
        register_snapshot(
            identity,
            _legacy_state(),
            database="primary",
            backend="sqlite",
            parent_snapshot_ids=["root"],
            registry_path=registry,
        )
    with pytest.raises(ValueError, match="ambiguous"):
        _latest_compatible_snapshot(
            historical_table("legacy"),
            database="primary",
            backend="sqlite",
            registry_path=registry,
        )


@pytest.mark.parametrize("backend", ["sqlite", "mysql"])
def test_current_model_source_keep_never_retires_source(tmp_path, backend):
    registry = tmp_path / "registry.json"
    register_snapshot(
        "current",
        _legacy_state(),
        database="primary",
        backend=backend,
        registry_path=registry,
    )

    class Base(DeclarativeBase):
        pass

    class Source(Base):
        __tablename__ = "legacy"
        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str]

    class Target(Base):
        __tablename__ = "current_target"
        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str]

    class Copy(DataTransition):
        source = Source
        source_snapshot = "current"
        source_identity = (Source.id,)
        on_complete = "keep"
        targets = (into(Target, map={"id": Source.id, "name": Source.name}),)

    spec = compile_data(
        DiscoveredData((Source, Target), (Copy,)),
        database="primary",
        backend=backend,
        registry_path=registry,
    )
    operations = plan_data(spec)
    sql = [
        statement for step in operations[0]["data_upgrade"] for statement in step["sql"]
    ]
    assert not any(
        statement.startswith(("DROP TABLE", "ALTER TABLE")) for statement in sql
    )
    assert operations[0]["guard_source"]["table"] == "legacy"
    if backend == "mysql":
        assert sum(statement.startswith("RENAME TABLE") for statement in sql) == 2
