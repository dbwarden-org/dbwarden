from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

from dbwarden.engine.core.snapshot_io import compute_checksum
from dbwarden.files import atomic_write_text

from .ir import parse_json


def _registry_path(path) -> Path:
    return (
        Path(path)
        if path is not None
        else Path.cwd() / ".dbwarden" / "snapshots" / "registry.json"
    )


def _schema_dir(path) -> Path:
    return Path(path) if path is not None else Path.cwd() / ".dbwarden" / "schemas"


def _validate_snapshot_id(identity) -> None:
    if not isinstance(identity, str) or not identity:
        raise ValueError("snapshot_id must be a non-empty string")
    if any(char in identity for char in "/\\\0") or identity in {".", ".."}:
        raise ValueError("snapshot_id must not contain a path")


@contextmanager
def _registry_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _validate_lineage(registry: dict, entry: dict) -> None:
    entries = {}
    for item in registry["snapshots"]:
        if item.get("database") != entry.get("database"):
            continue
        identity = item.get("snapshot_id")
        _validate_snapshot_id(identity)
        if identity in entries:
            raise ValueError(f"Ambiguous snapshot {identity}")
        entries[identity] = item
    pending = [(entry, frozenset())]
    visited = set()
    while pending:
        current, ancestors = pending.pop()
        identity = current["snapshot_id"]
        if identity in ancestors:
            raise ValueError(f"Cyclic snapshot lineage: {identity}")
        if identity in visited:
            continue
        if current.get("backend") != entry.get("backend"):
            raise ValueError(f"Snapshot lineage backend mismatch: {identity}")
        if current.get("format_version", 1) != 1:
            raise ValueError(f"Unsupported snapshot lineage version: {identity}")
        parents = current.get("parent_snapshot_ids", [])
        if (
            not isinstance(parents, list)
            or any(not isinstance(parent, str) or not parent for parent in parents)
            or len(parents) != len(set(parents))
            or parents != sorted(parents)
        ):
            raise ValueError(f"Invalid snapshot parent lineage: {identity}")
        for parent in parents:
            if parent not in entries:
                raise ValueError(f"Unknown parent snapshot: {parent}")
            pending.append((entries[parent], ancestors | {identity}))
        visited.add(identity)


def _read_registry(path: Path) -> dict:
    if not path.exists():
        return {"format_version": 1, "snapshots": []}
    try:
        value = parse_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid snapshot registry: {path}") from exc
    if (
        not isinstance(value, dict)
        or value.get("format_version") != 1
        or not isinstance(value.get("snapshots"), list)
    ):
        raise ValueError(f"Invalid snapshot registry: {path}")
    if any(not isinstance(item, dict) for item in value["snapshots"]):
        raise ValueError(f"Invalid snapshot registry: {path}")
    return value


def _checksum(value: dict) -> str:
    return compute_checksum(value)


def _normalized_checksum(value: str) -> str:
    return value.removeprefix("sha256:")


def _validated_state(
    state: dict, *, database: str, backend: str, expected_checksum: str | None = None
) -> tuple[dict, str]:
    if not isinstance(state, dict):
        raise TypeError("Snapshot schema state must be an object")
    actual = _checksum(state)
    stored = state.get("checksum")
    if stored and _normalized_checksum(str(stored)) != actual:
        raise ValueError("Snapshot checksum mismatch")
    if expected_checksum and _normalized_checksum(expected_checksum) != actual:
        raise ValueError("Snapshot registry checksum mismatch")
    state_database = state.get("database_name") or state.get("database")
    state_backend = state.get("database_type") or state.get("backend")
    if state_database is not None and state_database != database:
        raise ValueError(
            f"Snapshot belongs to database {state_database}, not {database}"
        )
    if state_backend is not None and state_backend != backend:
        raise ValueError(f"Snapshot uses backend {state_backend}, not {backend}")
    return state, actual


def _load_entry_state(entry: dict, registry_path: Path, schema_dir: Path) -> dict:
    state = entry.get("schema_state")
    if state is not None:
        return state
    location = entry.get("schema_file") or entry.get("storage_uri")
    if not isinstance(location, str) or not location:
        raise ValueError(f"Snapshot {entry.get('snapshot_id')} has no schema state")
    path = Path(location)
    if not path.is_absolute():
        path = schema_dir / path
        if not path.exists():
            path = registry_path.parent / location
    try:
        value = parse_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Pinned snapshot file is missing: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Pinned snapshot file is invalid: {path}") from exc
    return value


def _unregistered_candidates(
    snapshot_id: str, database: str, schema_dir: Path
) -> list[tuple[Path, dict]]:
    if not schema_dir.exists():
        return []
    candidates = []
    paths = {
        schema_dir / f"{snapshot_id}.schema.json",
        schema_dir / f"{database}__{snapshot_id}.schema.json",
    }
    for path in sorted(paths):
        if not path.is_file():
            continue
        try:
            value = parse_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Pinned snapshot file is invalid: {path}") from exc
        if not isinstance(value, dict):
            raise TypeError(f"Pinned snapshot file is invalid: {path}")
        internal_id = value.get("snapshot_id") or value.get("migration_id")
        if internal_id not in {
            None,
            snapshot_id,
            snapshot_id.removeprefix(database + "__"),
            database + "__" + snapshot_id,
        }:
            raise ValueError(f"Pinned snapshot identity does not match: {path}")
        candidates.append((path, value))
    return candidates


def resolve_snapshot(
    snapshot_id: str,
    *,
    database: str,
    backend: str,
    registry_path=None,
    schema_dir=None,
) -> dict:
    _validate_snapshot_id(snapshot_id)
    registry_file = _registry_path(registry_path)
    schemas = _schema_dir(schema_dir)
    registry = _read_registry(registry_file)
    matches = [
        item
        for item in registry["snapshots"]
        if item.get("snapshot_id") == snapshot_id and item.get("database") == database
    ]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous snapshot {snapshot_id} for database {database}")
    if matches:
        entry = matches[0]
        _validate_lineage(registry, entry)
        if (
            not isinstance(entry.get("schema_checksum"), str)
            or not entry["schema_checksum"]
        ):
            raise ValueError(f"Snapshot {snapshot_id} has no pinned schema checksum")
        if entry.get("backend") != backend:
            raise ValueError(
                f"Snapshot {snapshot_id} uses backend {entry.get('backend')}, not {backend}"
            )
        state = _load_entry_state(entry, registry_file, schemas)
        state, checksum = _validated_state(
            state,
            database=database,
            backend=backend,
            expected_checksum=entry.get("schema_checksum"),
        )
        result = {
            key: value
            for key, value in entry.items()
            if key not in {"schema_state", "schema_file", "storage_uri"}
        }
        result["schema_checksum"] = checksum
        result["schema_state"] = state
        result["format_version"] = entry.get("format_version", 1)
        return result
    candidates = _unregistered_candidates(snapshot_id, database, schemas)
    if len(candidates) > 1:
        raise ValueError(f"Ambiguous snapshot {snapshot_id} for database {database}")
    if not candidates:
        raise ValueError(
            f"Snapshot {snapshot_id} is not registered for database {database}"
        )
    _, state = candidates[0]
    if not isinstance(state, dict) or not state.get("checksum"):
        raise ValueError(f"Snapshot {snapshot_id} has no pinned schema checksum")
    state, checksum = _validated_state(state, database=database, backend=backend)
    return {
        "format_version": 1,
        "snapshot_id": snapshot_id,
        "database": database,
        "backend": backend,
        "schema_checksum": checksum,
        "data_checksum": None,
        "created_by_migration": state.get("migration_id"),
        "parent_snapshot_ids": [],
        "schema_state": state,
    }


def register_snapshot(
    snapshot_id,
    schema_state=None,
    *,
    database: str | None = None,
    backend: str | None = None,
    parent_snapshot_ids=(),
    data_checksum: str | None = None,
    created_by_migration: str | None = None,
    registry_path=None,
) -> dict:
    if isinstance(snapshot_id, dict):
        if schema_state is not None:
            raise ValueError(
                "schema_state must be omitted when the first argument is a snapshot"
            )
        schema_state = snapshot_id
        snapshot_id = schema_state.get("snapshot_id") or schema_state.get(
            "migration_id"
        )
        database = (
            database
            or schema_state.get("database_name")
            or schema_state.get("database")
        )
        backend = (
            backend or schema_state.get("database_type") or schema_state.get("backend")
        )
    if (
        not isinstance(snapshot_id, str)
        or not snapshot_id
        or not isinstance(database, str)
        or not isinstance(backend, str)
    ):
        raise ValueError("snapshot_id, database, and backend are required")
    _validate_snapshot_id(snapshot_id)
    state, checksum = _validated_state(schema_state, database=database, backend=backend)
    parents = list(parent_snapshot_ids)
    if any(not isinstance(parent, str) or not parent for parent in parents) or len(
        parents
    ) != len(set(parents)):
        raise ValueError("parent_snapshot_ids must contain unique non-empty strings")
    parents.sort()
    path = _registry_path(registry_path)
    entry = {
        "format_version": 1,
        "snapshot_id": snapshot_id,
        "database": database,
        "backend": backend,
        "schema_checksum": checksum,
        "data_checksum": data_checksum,
        "created_by_migration": created_by_migration or snapshot_id,
        "parent_snapshot_ids": parents,
        "schema_state": state,
    }
    with _registry_lock(path):
        registry = _read_registry(path)
        _validate_lineage(registry, entry)
        existing = [
            item
            for item in registry["snapshots"]
            if item.get("database") == database
            and item.get("snapshot_id") == snapshot_id
        ]
        if existing:
            if len(existing) == 1 and existing[0] == entry:
                return entry
            raise ValueError(
                f"Snapshot ID already registered: {database}:{snapshot_id}"
            )
        registry["snapshots"].append(entry)
        registry["snapshots"].sort(
            key=lambda item: (item.get("database", ""), item.get("snapshot_id", ""))
        )
        atomic_write_text(
            path,
            json.dumps(
                registry, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n",
        )
    return entry
