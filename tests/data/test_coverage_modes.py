import uuid

import pytest
from sqlalchemy import Column, ForeignKey, Integer, String, create_engine, text
from sqlalchemy.orm import declarative_base

from dbwarden.data.compiler import DiscoveredData, compile_data
from dbwarden.data.declarations import (
    DataMeta,
    DataTransition,
    _normalize_transition_coverage,
    historical_table,
    into,
    rows,
)
from dbwarden.data.planning import plan_data
from dbwarden.data.snapshots import register_snapshot


def _spec(tmp_path, monkeypatch, coverage, overlap, *, force_priorities=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    snapshot_id = "primary__" + uuid.uuid4().hex
    register_snapshot(
        snapshot_id,
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

    class Left(base):
        __tablename__ = "left_rows"
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)

    class Right(base):
        __tablename__ = "right_rows"
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)

    Left.declaration_id = "coverage.left"
    Right.declaration_id = "coverage.right"
    old = historical_table("legacy", snapshot=snapshot_id)
    priority = overlap == "priority" or force_priorities
    split = type(
        "Split",
        (DataTransition,),
        {
            "__module__": __name__,
            "declaration_id": "coverage.split",
            "source": old,
            "source_identity": (old.id,),
            "coverage": coverage,
            "on_unmatched": "ignore" if coverage == "subset" else "error",
            "overlap": overlap,
            "targets": (
                into(
                    Left,
                    map={Left.id: old.id, Left.name: old.name},
                    where=old.kind.in_(("left", "both")),
                    priority=0 if priority else None,
                ),
                into(
                    Right,
                    map={Right.id: old.id, Right.name: old.name},
                    where=old.kind.in_(("right", "both")),
                    priority=1 if priority else None,
                ),
            ),
        },
    )
    return compile_data(
        DiscoveredData((Left, Right), (split,)),
        database="primary",
        backend="sqlite",
    )


def _execute(spec, kind):
    operations = plan_data(spec)
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE legacy(id INTEGER PRIMARY KEY, name TEXT, kind TEXT)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE left_rows(id INTEGER PRIMARY KEY, name TEXT)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE right_rows(id INTEGER PRIMARY KEY, name TEXT)"
        )
        connection.execute(
            text("INSERT INTO legacy VALUES(1, 'One', :kind)"), {"kind": kind}
        )
        for operation in operations:
            for step in operation["data_upgrade"]:
                for timing in ("before", "after"):
                    if timing == "after":
                        for statement in step["sql"]:
                            connection.execute(text(statement))
                    for guard in step["guards"]:
                        if guard.get("timing", "before") != timing:
                            continue
                        value = connection.execute(text(guard["query"])).scalar_one()
                        minimum, maximum = guard["minimum"], guard["maximum"]
                        if minimum is not None and value < minimum or value > maximum:
                            raise ValueError(guard["message"])
        return (
            connection.exec_driver_sql("SELECT COUNT(*) FROM left_rows").scalar_one(),
            connection.exec_driver_sql("SELECT COUNT(*) FROM right_rows").scalar_one(),
        )


def test_coverage_spellings_are_canonicalized(tmp_path, monkeypatch):
    exact = _spec(tmp_path / "exact", monkeypatch, "exactly_once", "error")
    assert exact["transitions"][0]["coverage"]["mode"] == "exactly_once_match"

    assigned = _spec(tmp_path / "assigned", monkeypatch, "all", "priority")
    assert assigned["transitions"][0]["coverage"]["mode"] == "all_assigned_once"


def test_priority_and_assignment_mode_require_each_other():
    with pytest.raises(ValueError, match="priority overlap requires"):
        _normalize_transition_coverage("subset", "priority")
    with pytest.raises(ValueError, match="requires priority overlap"):
        _normalize_transition_coverage("all_assigned_once", "error")


def test_target_priorities_require_priority_assignment(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="Target priorities require"):
        _spec(
            tmp_path,
            monkeypatch,
            "all",
            "fan_out",
            force_priorities=True,
        )


@pytest.mark.parametrize("coverage", ["exactly_once", "exactly_once_match"])
def test_exactly_once_counts_raw_overlapping_matches(tmp_path, monkeypatch, coverage):
    spec = _spec(tmp_path, monkeypatch, coverage, "fan_out")
    with pytest.raises(ValueError, match="multiple targets"):
        _execute(spec, "both")


@pytest.mark.parametrize(
    ("coverage", "overlap"),
    [
        ("exactly_once_match", "error"),
        ("all_assigned_once", "priority"),
        ("all", "fan_out"),
    ],
)
def test_total_coverage_rejects_missing_rows(tmp_path, monkeypatch, coverage, overlap):
    spec = _spec(tmp_path, monkeypatch, coverage, overlap)
    with pytest.raises(ValueError, match="not covered"):
        _execute(spec, "none")


def test_priority_assigns_overlap_to_one_target(tmp_path, monkeypatch):
    spec = _spec(tmp_path, monkeypatch, "all_assigned_once", "priority")
    assert _execute(spec, "both") == (1, 0)


def test_explicit_fanout_writes_each_matching_target(tmp_path, monkeypatch):
    spec = _spec(tmp_path, monkeypatch, "all", "fan_out")
    assert _execute(spec, "both") == (1, 1)


def test_subset_fanout_permits_unmatched_rows(tmp_path, monkeypatch):
    spec = _spec(tmp_path, monkeypatch, "subset", "fan_out")
    assert _execute(spec, "none") == (0, 0)


def test_removed_source_foreign_key_fails_before_model_schema_freeze(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    snapshot_id = "primary__" + uuid.uuid4().hex
    register_snapshot(
        snapshot_id,
        {
            "tables": {
                "legacy": {"columns": {"id": {"type": "INTEGER", "nullable": False}}}
            }
        },
        database="primary",
        backend="sqlite",
    )
    base = declarative_base()

    class Target(base):
        __tablename__ = "targets"
        id = Column(Integer, primary_key=True)
        legacy_id = Column(Integer, ForeignKey("legacy.id"), nullable=False)

    Target.declaration_id = "coverage.target"

    old = historical_table("legacy", snapshot=snapshot_id)

    class Split(DataTransition):
        declaration_id = "coverage.removed_source"
        source = old
        source_identity = (old.id,)
        targets = (into(Target, map={Target.id: old.id, Target.legacy_id: old.id}),)

    with pytest.raises(ValueError, match="foreign key to removed historical source"):
        compile_data(
            DiscoveredData((Target,), (Split,)),
            database="primary",
            backend="sqlite",
        )


def test_static_row_checksum_uses_canonical_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    base = declarative_base()

    class Item(base):
        __tablename__ = "items"
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)

    Item.declaration_id = "coverage.item"

    class Data(DataMeta):
        managed_rows = rows(source="rows.json", rollback="irreversible")

    Item.Data = Data
    source = tmp_path / "rows.json"
    source.write_text('[{"id":2,"name":"Two"},{"id":1,"name":"One"}]')
    first = compile_data(
        DiscoveredData((Item,), ()), database="primary", backend="sqlite"
    )
    source.write_text('[\n  {"name": "One", "id": 1},\n  {"name": "Two", "id": 2}\n]\n')
    second = compile_data(
        DiscoveredData((Item,), ()), database="primary", backend="sqlite"
    )
    assert first == second
