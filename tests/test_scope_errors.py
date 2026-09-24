import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from dbwarden.commands.make_migrations.generation import generate_files
from dbwarden.engine.safety.partition import partition_operations
from dbwarden.engine.safety.plans import bind_plan, file_severity
from dbwarden.engine.safety.scope import severity_prefix


def test_failed_restore_still_restores_other_files(tmp_path, monkeypatch):
    from dbwarden import files

    first, second = tmp_path / "first", tmp_path / "second"
    for path in (first, second):
        path.write_bytes(b"before\r\n")
    original_write = files._atomic_write_bytes

    def restore(path, content):
        if path == second:
            raise PermissionError("locked")
        original_write(path, content)

    monkeypatch.setattr(files, "_atomic_write_bytes", restore)
    with pytest.raises(OSError, match="Could not restore files.*second") as caught, files.preserve_files_on_error([first, second]):
        first.write_bytes(b"after")
        second.write_bytes(b"after")
        raise RuntimeError("generation failed")
    assert str(caught.value.__cause__) == "generation failed"
    assert first.read_bytes() == b"before\r\n"
    assert second.read_bytes() == b"after"


@pytest.mark.parametrize("split", [None, [], False, 2, "base"])
def test_malformed_split_stops_scope_without_crashing(tmp_path, split):
    path = tmp_path / "primary__0001_test.sql"
    sql = "-- dbwarden: category base\n-- upgrade\nSELECT 1;\n"
    path.write_text(sql, encoding="utf-8")
    plan = bind_plan({}, sql, [{"type": "create_table"}], "sqlite")
    plan["category"] = {"name": "base", "order": 0, "plugin": None}
    plan["severity"]["split"] = split
    path.with_suffix(".plan.json").write_text(json.dumps(plan), encoding="utf-8")
    assert file_severity(path) == ("UNKNOWN", "invalid split metadata")
    allowed, stop = severity_prefix({"0001": str(path), "0002": "missing.sql"}, "WARN")
    assert allowed == {}
    assert (stop.version, stop.remaining) == ("0001", 2)


@pytest.mark.parametrize(
    "ops,reason",
    [
        ([{"id": "a"}, {"id": "a"}], "Duplicate"),
        ([{"id": "a", "depends_on": ["missing"]}], "Unknown"),
        ([{"id": "a", "depends_on": ["a"]}], "cycle"),
        ([{"id": "a", "depends_on": ["b"]}, {"id": "b"}], "ordering"),
    ],
)
def test_invalid_dependencies_refuse_partition(ops, reason):
    with pytest.raises(ValueError, match=reason):
        partition_operations(
            [dict(type="add_column", **op) for op in ops], "WARN", "sqlite"
        )


@pytest.fixture
def generation(monkeypatch):
    monkeypatch.setattr(
        "dbwarden.config.get_database",
        lambda _: SimpleNamespace(database_type="postgresql"),
    )
    monkeypatch.setattr(
        "dbwarden.engine.snapshot._get_backend", lambda db_name=None: "postgresql"
    )
    before = {
        "format_version": 2,
        "tables": {
            "items": {
                "name": "items",
                "columns": {"old": {"name": "old", "type": "TEXT", "nullable": True}},
            }
        },
    }
    after = deepcopy(before)
    after["tables"]["items"]["columns"] = {
        "new": {"name": "new", "type": "TEXT", "nullable": True}
    }
    from dbwarden.engine.offline import diff_model_states

    up, down = diff_model_states(before, after)
    return {
        "upgrade_ops": up,
        "rollback_ops": down,
        "database": "primary",
        "db_name": "primary",
        "baseline": before,
        "target": after,
        "threshold": "WARN",
    }


@pytest.mark.parametrize("failure_at", [1, 2, 3, 4])
def test_split_write_failure_leaves_no_partial_migrations(
    tmp_path, monkeypatch, generation, failure_at
):
    from dbwarden import files

    original = files._atomic_write_bytes
    calls = 0

    def fail_once(path, content):
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError("injected split write failure")
        return original(path, content)

    with monkeypatch.context() as scoped:
        scoped.setattr(files, "_atomic_write_bytes", fail_once)
        with pytest.raises(OSError, match="injected split"):
            generate_files(**generation, migrations_dir=str(tmp_path))
    assert list(tmp_path.iterdir()) == []
    artifacts = generate_files(**generation, migrations_dir=str(tmp_path))
    assert len(artifacts) == 2
    assert len(list(tmp_path.iterdir())) == 4


def test_orphan_plan_is_not_overwritten(tmp_path, generation):
    artifacts = generate_files(**generation, migrations_dir=str(tmp_path), write=False)
    plan = (tmp_path / artifacts[1]["filename"]).with_suffix(".plan.json")
    plan.write_bytes(b"existing plan\r\n")
    with pytest.raises(ValueError, match="already exists"):
        generate_files(**generation, migrations_dir=str(tmp_path))
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == {
        plan.name: b"existing plan\r\n"
    }


def test_repeatable_overwrite_failure_restores_exact_bytes(
    tmp_path, monkeypatch, generation
):
    from dbwarden import files

    generation.update(threshold=None, migration_type="ra")
    artifacts = generate_files(**generation, migrations_dir=str(tmp_path))
    path = tmp_path / artifacts[0]["filename"]
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    write = files._atomic_write_bytes

    def fail_sql(destination, content):
        if Path(destination) == path:
            raise OSError("repeatable write failed")
        return write(destination, content)

    with monkeypatch.context() as scoped:
        scoped.setattr(files, "_atomic_write_bytes", fail_sql)
        with pytest.raises(OSError, match="repeatable write failed"):
            generate_files(**generation, migrations_dir=str(tmp_path))
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before
