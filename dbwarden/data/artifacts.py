from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from .frozen import _read_frozen_record
from .ir import canonical_bytes, digest, parse_json, validate_spec


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _plan_payload(plan: dict) -> dict:
    if not isinstance(plan, dict):
        raise TypeError("Data migration plan must be an object")
    payload = deepcopy(plan)
    payload.pop("data_bundle", None)
    return payload


def build_manifest(sql_content: str, plan: dict, frozen_content: str) -> dict:
    if not isinstance(sql_content, str) or not isinstance(frozen_content, str):
        raise TypeError("SQL and frozen artifact content must be text")
    payload = _plan_payload(plan)
    migration_id = payload.get("migration_id")
    if (
        not isinstance(migration_id, str)
        or not migration_id
        or any(char in migration_id for char in "/\\\0")
        or migration_id in {".", ".."}
    ):
        raise ValueError("Artifact migration ID must be a filename stem")
    members = [
        ("sql", ".sql", "application/sql", sql_content.encode("utf-8")),
        ("plan", ".plan.json", "application/json", canonical_bytes(payload)),
        ("frozen", ".data.py", "text/x-python", frozen_content.encode("utf-8")),
    ]
    manifest = {
        "format_version": 1,
        "migration_id": migration_id,
        "semantic_checksum": payload.get("data_spec", {}).get("canonical_checksum"),
        "components": {
            key: {"sha256": _sha256(content)} for key, _, _, content in members
        },
        "members": [
            {
                "name": migration_id + suffix,
                "media_type": media_type,
                "byte_length": len(content),
                "sha256": _sha256(content),
            }
            for _, suffix, media_type, content in members
        ],
    }
    manifest["checksum"] = digest(manifest, "artifact-manifest")
    return manifest


def verify_bundle(sql_path, plan=None) -> dict:
    sql_path = Path(sql_path)
    plan_path = sql_path.with_suffix(".plan.json")
    frozen_path = sql_path.with_suffix(".data.py")
    try:
        sql_content = sql_path.read_text(encoding="utf-8")
        frozen_content = frozen_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"Data migration artifact is missing: {exc.filename}") from exc
    except (OSError, UnicodeError) as exc:
        raise ValueError("Data migration artifact could not be read") from exc
    if plan is None:
        try:
            plan = parse_json(plan_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(
                f"Data migration artifact is missing: {plan_path}"
            ) from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid data migration plan: {plan_path}") from exc
    if not isinstance(plan, dict):
        raise TypeError("Data migration plan must be an object")
    if plan.get("migration_id") != sql_path.stem:
        raise ValueError(
            "Data migration artifact filename does not match its migration ID"
        )
    bundle = plan.get("data_bundle")
    if bundle is None:
        raise ValueError("Data migration plan has no artifact manifest")
    if not isinstance(bundle, dict):
        raise TypeError("Data migration artifact manifest must be an object")
    expected = build_manifest(sql_content, plan, frozen_content)
    if canonical_bytes(expected) != canonical_bytes(bundle):
        raise ValueError("Data migration artifact checksum mismatch")
    frozen_migration_id, frozen_spec = _read_frozen_record(frozen_path)
    if frozen_migration_id != plan.get("migration_id"):
        raise ValueError("Frozen data artifact migration ID does not match the plan")
    if "data_spec" not in plan or canonical_bytes(frozen_spec) != canonical_bytes(
        plan["data_spec"]
    ):
        raise ValueError("Frozen data specification does not match the migration plan")
    validate_spec(plan["data_spec"])
    return expected
