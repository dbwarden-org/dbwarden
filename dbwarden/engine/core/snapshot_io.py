from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Any

from dbwarden.logging import get_component_logger

_snapshot_logger = get_component_logger("snapshot")


def compute_checksum(snapshot: dict[str, Any]) -> str:
    snapshot_copy = {k: v for k, v in snapshot.items() if k != "checksum"}
    raw = json.dumps(snapshot_copy, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_schemas_directory(database: str | None = None) -> str:
    base_dir = os.path.join(os.getcwd(), ".dbwarden", "schemas")
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


def write_snapshot(
    snapshot: dict[str, Any],
    database: str | None = None,
    migration_id: str = "",
) -> str:
    if not migration_id or migration_id in {".", ".."}:
        raise ValueError("migration_id must be a non-empty filename-safe value")
    if (
        os.path.basename(migration_id) != migration_id
        or "/" in migration_id
        or "\\" in migration_id
        or any(ord(char) < 32 for char in migration_id)
    ):
        raise ValueError("migration_id must not contain path separators")

    from dbwarden.config import get_database
    from sqlalchemy.engine import make_url

    config = get_database(database)
    if database:
        db_name = database
    else:
        parsed = make_url(config.sqlalchemy_url)
        db_name = parsed.database or "default"

    snapshot["migration_id"] = migration_id
    snapshot["database_name"] = db_name
    snapshot["database_type"] = config.database_type
    snapshot["applied_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    checksum = compute_checksum(snapshot)
    snapshot["checksum"] = checksum

    schemas_dir = get_schemas_directory(database)
    filename = f"{db_name}__{migration_id}.schema.json"
    filepath = os.path.join(schemas_dir, filename)

    fd, tmp_path = tempfile.mkstemp(dir=schemas_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False, default=str)
            f.write("\n")
        os.replace(tmp_path, filepath)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise

    return filepath


def read_snapshot(filepath: str) -> dict[str, Any] | None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    stored_checksum = snapshot.pop("checksum", None)
    if stored_checksum is not None:
        actual = compute_checksum(snapshot)
        snapshot["checksum"] = stored_checksum
        if actual != stored_checksum:
            _snapshot_logger.warning(
                "Snapshot checksum mismatch for %s (expected %s, got %s)",
                filepath, stored_checksum, actual,
            )
            return None
    else:
        snapshot["checksum"] = ""

    return snapshot


def find_latest_snapshot(database: str | None = None) -> dict[str, Any] | None:
    schemas_dir = get_schemas_directory(database)

    if not os.path.isdir(schemas_dir):
        return None

    if database is None:
        try:
            from dbwarden.config import get_multi_db_config
            db_name = get_multi_db_config().default
        except Exception:
            db_name = "default"
    else:
        db_name = database

    prefix = f"{db_name}__"
    candidates: list[tuple[str, float, str]] = []

    for fname in os.listdir(schemas_dir):
        if not fname.endswith(".schema.json"):
            continue
        if not fname.startswith(prefix):
            continue
        stem = fname[: -len(".schema.json")]
        migration_id = stem
        version_match = re.search(r"__(\d{4})_", migration_id)
        if version_match:
            version = version_match.group(1)
            path = os.path.join(schemas_dir, fname)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0.0
            candidates.append((version, mtime, path))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[1]))
    latest_path = candidates[-1][2]
    return read_snapshot(latest_path)


def extract_snapshot_tables(snapshot: dict[str, Any]) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for table_name, table_def in snapshot.get("tables", {}).items():
        tables[table_name] = set(table_def.get("columns", {}).keys())
    return tables
