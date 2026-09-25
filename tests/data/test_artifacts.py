import importlib.util
import json

import pytest

from dbwarden.data.artifacts import build_manifest, verify_bundle
from dbwarden.data.frozen import (
    FrozenDataArtifactImportError,
    allow_frozen_data_imports,
    read_frozen,
    render_frozen,
)
from dbwarden.data.ir import empty_spec
from dbwarden.data.snapshots import register_snapshot, resolve_snapshot
from dbwarden.engine.core.snapshot_io import compute_checksum


def _snapshot(snapshot_id="primary__0001"):
    value = {
        "format_version": 1,
        "migration_id": snapshot_id,
        "database_name": "primary",
        "database_type": "sqlite",
        "tables": {"legacy": {"columns": {"id": {"type": "integer"}}}},
    }
    value["checksum"] = compute_checksum(value)
    return value


def test_snapshot_registry_round_trip_and_exact_integrity(tmp_path):
    registry = tmp_path / "registry.json"
    snapshot = _snapshot()
    registered = register_snapshot(snapshot, registry_path=registry)
    resolved = resolve_snapshot(
        "primary__0001",
        database="primary",
        backend="sqlite",
        registry_path=registry,
        schema_dir=tmp_path / "missing",
    )
    assert resolved["schema_checksum"] == registered["schema_checksum"]
    assert resolved["schema_state"] == snapshot
    saved = json.loads(registry.read_text(encoding="utf-8"))
    saved["snapshots"][0]["schema_state"]["tables"] = {}
    registry.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        resolve_snapshot(
            "primary__0001",
            database="primary",
            backend="sqlite",
            registry_path=registry,
            schema_dir=tmp_path / "missing",
        )


def test_snapshot_read_does_not_create_directories(tmp_path):
    registry = tmp_path / "absent" / "registry.json"
    schemas = tmp_path / "absent" / "schemas"
    with pytest.raises(ValueError, match="not registered"):
        resolve_snapshot(
            "missing",
            database="primary",
            backend="sqlite",
            registry_path=registry,
            schema_dir=schemas,
        )
    assert not registry.parent.exists()


def test_snapshot_without_pinned_checksum_is_rejected(tmp_path):
    state = _snapshot()
    del state["checksum"]
    (tmp_path / "primary__0001.schema.json").write_text(json.dumps(state))
    with pytest.raises(ValueError, match="pinned schema checksum"):
        resolve_snapshot(
            "primary__0001",
            database="primary",
            backend="sqlite",
            registry_path=tmp_path / "registry.json",
            schema_dir=tmp_path,
        )


def test_canonical_json_rejects_duplicate_fields_and_nonfinite_values():
    from dbwarden.data.ir import parse_json

    for content in (
        '{"a":1,"a":2}',
        '{"é":1,"e\\u0301":2}',
        '{"x":NaN}',
        '{"x":Infinity}',
    ):
        with pytest.raises(ValueError):
            parse_json(content)


def test_snapshot_registry_rejects_duplicate_identity(tmp_path):
    registry = tmp_path / "registry.json"
    register_snapshot(_snapshot(), registry_path=registry)
    changed = _snapshot()
    changed["tables"]["other"] = {"columns": {}}
    changed["checksum"] = compute_checksum(changed)
    with pytest.raises(ValueError, match="already registered"):
        register_snapshot(changed, registry_path=registry)


def test_frozen_artifact_is_guarded_and_read_statically(tmp_path):
    spec = empty_spec("primary", "sqlite")
    path = tmp_path / "primary__0001.data.py"
    path.write_text(render_frozen(spec, "primary__0001"), encoding="utf-8")
    assert read_frozen(path) == spec
    module_spec = importlib.util.spec_from_file_location("frozen_test", path)
    module = importlib.util.module_from_spec(module_spec)
    with pytest.raises(FrozenDataArtifactImportError):
        module_spec.loader.exec_module(module)
    with allow_frozen_data_imports():
        module_spec.loader.exec_module(module)


def test_static_reader_rejects_executable_frozen_content(tmp_path):
    path = tmp_path / "bad.data.py"
    path.write_text(
        "raise AssertionError('executed')\nDATA_SPEC = {}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unsupported statements"):
        read_frozen(path)


def test_bundle_verifies_all_components_and_frozen_semantics(tmp_path):
    sql_path = tmp_path / "primary__0001_test.sql"
    frozen_path = sql_path.with_suffix(".data.py")
    plan_path = sql_path.with_suffix(".plan.json")
    spec = empty_spec("primary", "sqlite")
    sql = "-- upgrade\nSELECT 1;\n"
    frozen = render_frozen(spec, "primary__0001_test")
    plan = {"migration_id": "primary__0001_test", "data_spec": spec}
    plan["data_bundle"] = build_manifest(sql, plan, frozen)
    sql_path.write_text(sql, encoding="utf-8")
    frozen_path.write_text(frozen, encoding="utf-8")
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    assert verify_bundle(sql_path) == plan["data_bundle"]
    renamed = tmp_path / "primary__0002_renamed.sql"
    renamed.write_text(sql, encoding="utf-8")
    renamed.with_suffix(".data.py").write_text(frozen, encoding="utf-8")
    renamed.with_suffix(".plan.json").write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="filename does not match"):
        verify_bundle(renamed)
    sql_path.write_text(sql + "SELECT 2;\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        verify_bundle(sql_path)


def test_bundle_rejects_frozen_spec_different_from_plan(tmp_path):
    sql_path = tmp_path / "primary__0001_test.sql"
    frozen_path = sql_path.with_suffix(".data.py")
    plan_path = sql_path.with_suffix(".plan.json")
    spec = empty_spec("primary", "sqlite")
    other = empty_spec("other", "sqlite")
    sql = "-- upgrade\nSELECT 1;\n"
    frozen = render_frozen(other, "primary__0001_test")
    plan = {"migration_id": "primary__0001_test", "data_spec": spec}
    plan["data_bundle"] = build_manifest(sql, plan, frozen)
    sql_path.write_text(sql, encoding="utf-8")
    frozen_path.write_text(frozen, encoding="utf-8")
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        verify_bundle(sql_path)


def test_sealed_frozen_record_binds_data_execution(tmp_path):
    """Finding 5: the frozen record seals data_execution with a keyed HMAC, so
    edited batch rows cannot survive a recomputed manifest. Legacy records
    without the seal still verify through the manifest alone."""
    sql_path = tmp_path / "primary__0001_sealed.sql"
    frozen_path = sql_path.with_suffix(".data.py")
    plan_path = sql_path.with_suffix(".plan.json")
    spec = empty_spec("primary", "sqlite")
    execution = {
        "upgrade": [
            {
                "operation_id": "op1",
                "sql": ["CREATE TABLE bundle_t (id INTEGER);", "INSERT INTO bundle_t (id) VALUES (1);"],
                "guards": [],
            }
        ],
        "rollback": [],
    }
    frozen = render_frozen(
        spec, "primary__0001_sealed", execution=execution, project_root=tmp_path
    )
    assert "DATA_EXECUTION_HMAC" in frozen
    sql = "-- upgrade\n-- dbwarden: data-bundle\n"
    plan = {
        "migration_id": "primary__0001_sealed",
        "data_spec": spec,
        "data_execution": execution,
    }
    plan["data_bundle"] = build_manifest(sql, plan, frozen)
    sql_path.write_text(sql, encoding="utf-8")
    frozen_path.write_text(frozen, encoding="utf-8")
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    assert verify_bundle(sql_path) == plan["data_bundle"]

    # Attack: edit the batch row and recompute the (public) manifest. The
    # manifest still matches, but the frozen seal no longer does.
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["data_execution"]["upgrade"][0]["sql"][1] = (
        "INSERT INTO bundle_t (id) VALUES (999);"
    )
    plan["data_bundle"] = build_manifest(
        sql, plan, frozen_path.read_text(encoding="utf-8")
    )
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="sealed bundle"):
        verify_bundle(sql_path)

    # A forged seal value cannot be recomputed without the project key, and a
    # record with the seal stripped is a legacy bundle whose every byte is
    # still manifest-bound; either way the tampered rows do not verify.
    forged = frozen.replace(
        frozen.splitlines()[-1],
        "DATA_EXECUTION_HMAC = 'hmac-sha256:" + "0" * 64 + "'",
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["data_execution"]["upgrade"][0]["sql"][1] = (
        "INSERT INTO bundle_t (id) VALUES (999);"
    )
    plan["data_bundle"] = build_manifest(sql, plan, forged)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    frozen_path.write_text(forged, encoding="utf-8")
    with pytest.raises(ValueError, match="sealed bundle"):
        verify_bundle(sql_path)


def test_frozen_execution_seal_survives_static_read(tmp_path):
    from dbwarden.data.frozen import read_frozen

    spec = empty_spec("primary", "sqlite")
    execution = {"upgrade": [], "rollback": []}
    frozen = render_frozen(
        spec, "primary__0001", execution=execution, project_root=tmp_path
    )
    path = tmp_path / "primary__0001.data.py"
    path.write_text(frozen, encoding="utf-8")
    assert read_frozen(path) == spec
