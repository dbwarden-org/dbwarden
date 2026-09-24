from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import declarative_base

from dbwarden.data.compiler import DiscoveredData, compile_data
from dbwarden.data.declarations import (
    DataMeta,
    DataTransition,
    derive,
    historical_table,
    into,
    rows,
)
from dbwarden.data.expressions import case, cast, col, func


def _snapshot(columns, **extra):
    return {
        "snapshot_id": "s1",
        "database": "primary",
        "backend": "sqlite",
        "schema_checksum": "checksum",
        "data_checksum": None,
        "created_by_migration": "m1",
        "parent_snapshot_ids": [],
        "schema_state": {"tables": {"legacy": {"columns": columns}}},
        **extra,
    }


def _compile_transition(monkeypatch, models, transition, columns, **snapshot_extra):
    from dbwarden.data import snapshots

    monkeypatch.setattr(
        snapshots,
        "resolve_snapshot",
        lambda *_args, **_kwargs: _snapshot(columns, **snapshot_extra),
    )
    return compile_data(
        DiscoveredData(tuple(models), (transition,)),
        database="primary",
        backend="sqlite",
    )


def _target(value_type=String, *, nullable=False):
    base = declarative_base()

    class Target(base):
        __tablename__ = "targets"
        id = Column(Integer, primary_key=True)
        value = Column(value_type, nullable=nullable)

    return Target


def _transition(target, expression, *, identity=None, where=None):
    old = historical_table("legacy", snapshot="s1")
    identity_values = [old.id] if identity is None else identity

    class Move(DataTransition):
        declaration_id = "move-legacy"
        source = old
        source_identity = identity_values
        targets = (
            into(
                target,
                map={target.id: old.id, target.value: expression},
                key=[target.id],
                where=where,
            ),
        )

    return Move


def test_declaration_classes_reject_methods_unknown_attributes_and_callables():
    with pytest.raises(ValueError, match="Unknown Data attributes"):

        class BadData(DataMeta):
            def run(self):
                return None

    with pytest.raises(ValueError, match="Unknown DataTransition attributes"):

        class BadTransition(DataTransition):
            surprise = True

    with pytest.raises(TypeError, match="callable"):
        derive("value", lambda: 1, rollback="irreversible")


def test_compile_phase_rejects_untyped_input():
    with pytest.raises(TypeError, match="DiscoveredData"):
        compile_data([], database="primary", backend="sqlite")


def test_transition_requires_known_compatible_source_types(monkeypatch):
    target = _target(String)
    old = historical_table("legacy", snapshot="s1")
    transition = _transition(target, old.amount)
    columns = {
        "id": {"type": "integer", "nullable": False},
        "amount": {"type": "integer", "nullable": False},
    }

    with pytest.raises(ValueError, match="incompatible.*explicit cast"):
        _compile_transition(monkeypatch, [target], transition, columns)

    cast_transition = _transition(target, cast(old.amount, "TEXT"))
    spec = _compile_transition(monkeypatch, [target], cast_transition, columns)
    assert (
        spec["transitions"][0]["targets"][0]["mappings"]["value"]["canonical_ast"]["op"]
        == "cast"
    )


def test_transition_rejects_missing_source_type_and_computed_identity(monkeypatch):
    target = _target(Integer)
    old = historical_table("legacy", snapshot="s1")
    columns = {
        "id": {"type": "integer", "nullable": False},
        "amount": {},
    }

    with pytest.raises(ValueError, match="Missing type metadata"):
        _compile_transition(
            monkeypatch, [target], _transition(target, old.amount), columns
        )
    with pytest.raises(ValueError, match="direct column references"):
        _compile_transition(
            monkeypatch,
            [target],
            _transition(target, old.id, identity=[old.id + 1]),
            {"id": {"type": "integer", "nullable": False}},
        )


def test_transition_predicates_must_be_boolean(monkeypatch):
    target = _target(Integer)
    old = historical_table("legacy", snapshot="s1")

    with pytest.raises(ValueError, match="predicate.*boolean"):
        _compile_transition(
            monkeypatch,
            [target],
            _transition(target, old.amount, where=old.amount),
            {
                "id": {"type": "integer", "nullable": False},
                "amount": {"type": "integer", "nullable": False},
            },
        )


def test_derive_checks_type_nullability_and_boolean_domain():
    base = declarative_base()

    class Item(base):
        __tablename__ = "items"
        id = Column(Integer, primary_key=True)
        raw = Column(Integer, nullable=False)
        name = Column(String, nullable=True)
        result = Column(String, nullable=False)

    class WrongType(DataMeta):
        transformations = (derive("result", col("raw"), rollback="irreversible"),)

    Item.Data = WrongType
    with pytest.raises(ValueError, match="incompatible.*explicit cast"):
        compile_data(DiscoveredData((Item,), ()), database="primary", backend="sqlite")

    class WrongDomain(DataMeta):
        transformations = (
            derive(
                "result",
                cast(col("raw"), "TEXT"),
                rollback="irreversible",
                when=col("raw"),
                on_unmatched="keep",
            ),
        )

    Item.Data = WrongDomain
    with pytest.raises(ValueError, match="domain.*boolean"):
        compile_data(DiscoveredData((Item,), ()), database="primary", backend="sqlite")

    class NullableResult(DataMeta):
        transformations = (
            derive("result", func.lower(col("name")), rollback="irreversible"),
        )

    Item.Data = NullableResult
    with pytest.raises(ValueError, match="nullable expression"):
        compile_data(DiscoveredData((Item,), ()), database="primary", backend="sqlite")

    class GuardedResult(DataMeta):
        transformations = (
            derive(
                "result",
                func.lower(col("name")),
                rollback="irreversible",
                when=col("name").is_not(None),
                on_unmatched="keep",
            ),
        )

    Item.Data = GuardedResult
    assert compile_data(
        DiscoveredData((Item,), ()), database="primary", backend="sqlite"
    )["transformations"]


def test_partial_case_requires_domain_that_proves_coverage():
    base = declarative_base()
    condition = col("kind") == "x"

    class Item(base):
        __tablename__ = "case_items"
        id = Column(Integer, primary_key=True)
        kind = Column(String, nullable=False)
        result = Column(Integer, nullable=False)

    class Unproven(DataMeta):
        transformations = (
            derive(
                "result",
                case((condition, 1)),
                rollback="irreversible",
                when=col("kind").is_not(None),
                on_unmatched="keep",
            ),
        )

    Item.Data = Unproven
    with pytest.raises(ValueError, match="does not prove"):
        compile_data(DiscoveredData((Item,), ()), database="primary", backend="sqlite")

    class Proven(DataMeta):
        transformations = (
            derive(
                "result",
                case((condition, 1)),
                rollback="irreversible",
                when=condition,
                on_unmatched="keep",
            ),
        )

    Item.Data = Proven
    spec = compile_data(
        DiscoveredData((Item,), ()), database="primary", backend="sqlite"
    )
    assert spec["transformations"][0]["expression"]["total_domain_complete"] is False


def test_static_json_rejects_malformed_and_unicode_duplicate_keys(tmp_path):
    base = declarative_base()

    class Item(base):
        __tablename__ = "json_items"
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)

    class Data(DataMeta):
        managed_rows = rows(
            key="id",
            source="rows.json",
            owned_columns=["name"],
            rollback="irreversible",
        )

    Item.Data = Data
    for content in ('[{"id":1, "name":]', '[{"id":1, "é":"a", "é":"b"}]'):
        (tmp_path / "rows.json").write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid static JSON"):
            compile_data(
                DiscoveredData((Item,), ()),
                database="primary",
                backend="sqlite",
                project_root=tmp_path,
            )


def test_transition_id_ignores_snapshot_observation_timestamp(monkeypatch):
    target = _target(Integer)
    old = historical_table("legacy", snapshot="s1")
    transition = _transition(target, old.amount)
    columns = {
        "id": {"type": "integer", "nullable": False},
        "amount": {"type": "integer", "nullable": False},
    }

    first = _compile_transition(
        monkeypatch,
        [target],
        transition,
        columns,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
    )
    second = _compile_transition(
        monkeypatch,
        [target],
        transition,
        columns,
        created_at=datetime(2026, 2, 1, tzinfo=timezone.utc).isoformat(),
    )

    assert first == second
    assert "created_at" not in first["source_snapshots"][0]


def test_transition_targets_follow_foreign_key_dependencies(monkeypatch):
    base = declarative_base()

    class Parent(base):
        __tablename__ = "z_parents"
        id = Column(Integer, primary_key=True)

    class Child(base):
        __tablename__ = "a_children"
        id = Column(Integer, primary_key=True)
        parent_id = Column(Integer, ForeignKey("z_parents.id"), nullable=False)

    old = historical_table("legacy", snapshot="s1")

    class Split(DataTransition):
        declaration_id = "split-fk"
        source = old
        source_identity = (old.id,)
        targets = (
            into(
                Child,
                map={Child.id: old.id, Child.parent_id: old.parent_id},
                key=[Child.id],
            ),
            into(Parent, map={Parent.id: old.parent_id}, key=[Parent.id]),
        )

    spec = _compile_transition(
        monkeypatch,
        [Parent, Child],
        Split,
        {
            "id": {"type": "integer", "nullable": False},
            "parent_id": {"type": "integer", "nullable": False},
        },
    )
    targets = spec["transitions"][0]["targets"]
    assert [target["target_table"]["table"] for target in targets] == [
        "z_parents",
        "a_children",
    ]
    assert targets[1]["depends_on"] == [targets[0]["target_table"]]


def test_transition_target_cannot_reference_removed_source(monkeypatch):
    base = declarative_base()

    class Target(base):
        __tablename__ = "targets_with_old_fk"
        id = Column(Integer, ForeignKey("legacy.id"), primary_key=True)

    old = historical_table("legacy", snapshot="s1")

    class Move(DataTransition):
        declaration_id = "bad-source-fk"
        source = old
        source_identity = (old.id,)
        targets = (into(Target, map={Target.id: old.id}, key=[Target.id]),)

    with pytest.raises(ValueError, match="foreign key to removed historical source"):
        _compile_transition(
            monkeypatch,
            [Target],
            Move,
            {"id": {"type": "integer", "nullable": False}},
        )


def test_transition_target_foreign_key_cycle_is_rejected(monkeypatch):
    base = declarative_base()

    class Left(base):
        __tablename__ = "left_targets"
        id = Column(Integer, primary_key=True)
        right_id = Column(Integer, ForeignKey("right_targets.id"), nullable=False)

    class Right(base):
        __tablename__ = "right_targets"
        id = Column(Integer, primary_key=True)
        left_id = Column(Integer, ForeignKey("left_targets.id"), nullable=False)

    old = historical_table("legacy", snapshot="s1")

    class Split(DataTransition):
        declaration_id = "cyclic-targets"
        source = old
        source_identity = (old.id,)
        targets = (
            into(
                Left,
                map={Left.id: old.id, Left.right_id: old.right_id},
                key=[Left.id],
            ),
            into(
                Right,
                map={Right.id: old.right_id, Right.left_id: old.id},
                key=[Right.id],
            ),
        )

    with pytest.raises(ValueError, match="foreign-key cycle"):
        _compile_transition(
            monkeypatch,
            [Left, Right],
            Split,
            {
                "id": {"type": "integer", "nullable": False},
                "right_id": {"type": "integer", "nullable": False},
            },
        )
