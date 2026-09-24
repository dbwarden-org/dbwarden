import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from dbwarden.data.snapshots import register_snapshot, resolve_snapshot
from tests.data.test_artifacts import _snapshot


def _resolve(path, identity):
    return resolve_snapshot(
        identity, database="primary", backend="sqlite", registry_path=path
    )


def test_import_branch_merge_and_squash_lineage(tmp_path):
    path = tmp_path / "registry.json"
    register_snapshot(_snapshot("root"), registry_path=path)
    for identity in ("left", "right"):
        register_snapshot(
            _snapshot(identity), parent_snapshot_ids=["root"], registry_path=path
        )
    register_snapshot(
        _snapshot("merge"), parent_snapshot_ids=["right", "left"], registry_path=path
    )
    register_snapshot(
        _snapshot("squash"), parent_snapshot_ids=["merge"], registry_path=path
    )
    assert _resolve(path, "merge")["parent_snapshot_ids"] == ["left", "right"]
    assert _resolve(path, "squash")["parent_snapshot_ids"] == ["merge"]
    assert _resolve(path, "squash")["format_version"] == 1
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unknown parent"):
        register_snapshot(
            _snapshot("bad"), parent_snapshot_ids=["missing"], registry_path=path
        )
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ("missing", "Unknown parent"),
        ("cycle", "Cyclic snapshot"),
        ("duplicate", "Ambiguous snapshot"),
        ("backend", "backend mismatch"),
        ("version", "Unsupported snapshot lineage version"),
    ],
)
def test_resolution_rejects_corrupt_lineage(tmp_path, fault, message):
    path = tmp_path / "registry.json"
    register_snapshot(_snapshot("root"), registry_path=path)
    register_snapshot(
        _snapshot("child"), parent_snapshot_ids=["root"], registry_path=path
    )
    registry = json.loads(path.read_text())
    root = next(item for item in registry["snapshots"] if item["snapshot_id"] == "root")
    if fault == "missing":
        registry["snapshots"].remove(root)
    elif fault == "cycle":
        root["parent_snapshot_ids"] = ["child"]
    elif fault == "duplicate":
        registry["snapshots"].append(root)
    elif fault == "backend":
        root["backend"] = "postgresql"
    else:
        root["format_version"] = 2
    path.write_text(json.dumps(registry))
    before = path.read_bytes()
    with pytest.raises(ValueError, match=message):
        _resolve(path, "child")
    assert path.read_bytes() == before


def test_concurrent_offline_imports_preserve_all_registry_entries(tmp_path):
    path = tmp_path / "registry.json"
    barrier = Barrier(4)

    def register(index):
        barrier.wait(timeout=10)
        return register_snapshot(_snapshot(str(index)), registry_path=path)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(register, range(4)))
    assert {
        item["snapshot_id"] for item in json.loads(path.read_text())["snapshots"]
    } == {"0", "1", "2", "3"}
    for identity in range(4):
        assert _resolve(path, str(identity))["snapshot_id"] == str(identity)


def test_snapshot_file_identity_must_match_requested_pin(tmp_path):
    schema = tmp_path / "wanted.schema.json"
    schema.write_text(json.dumps(_snapshot("different")))
    with pytest.raises(ValueError, match="identity does not match"):
        resolve_snapshot(
            "wanted",
            database="primary",
            backend="sqlite",
            registry_path=tmp_path / "registry.json",
            schema_dir=tmp_path,
        )


def test_duplicate_fallback_files_are_ambiguous(tmp_path):
    for name in ("snapshot.schema.json", "primary__snapshot.schema.json"):
        (tmp_path / name).write_text(json.dumps(_snapshot("snapshot")))
    with pytest.raises(ValueError, match="Ambiguous snapshot"):
        resolve_snapshot(
            "snapshot",
            database="primary",
            backend="sqlite",
            registry_path=tmp_path / "registry.json",
            schema_dir=tmp_path,
        )


def test_unsafe_snapshot_id_cannot_be_registered(tmp_path):
    registry = tmp_path / "registry.json"
    with pytest.raises(ValueError, match="must not contain a path"):
        register_snapshot(_snapshot("../escape"), registry_path=registry)
    assert not registry.exists()
