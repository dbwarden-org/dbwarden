from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

SPEC_VERSION = 1
EXPRESSION_LANGUAGE_VERSION = 1
BACKEND_SEMANTICS_VERSION = 1

_DECLARATION_KINDS = (
    "managed_rows",
    "transformations",
    "validations",
    "transitions",
    "merges",
)


def canonical_value(value):
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Data literals must be finite")
        return {"$float": repr(value)}
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Data decimals must be finite")
        return {"$decimal": str(value.normalize())}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, time):
        return {"$time": value.isoformat()}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$bytes": bytes(value).hex()}
    if isinstance(value, UUID):
        return {"$uuid": str(value)}
    if isinstance(value, timedelta):
        return {
            "$duration_microseconds": (value.days * 86400 + value.seconds) * 1000000
            + value.microseconds
        }
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Canonical data object keys must be strings")
            key = canonical_value(key)
            if key in result:
                raise ValueError(f"Unicode-normalized duplicate key: {key}")
            result[key] = canonical_value(item)
        return result
    raise ValueError(f"Unsupported data literal: {type(value).__name__}")


def canonical_bytes(value) -> bytes:
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def parse_json(content):
    def pairs(values):
        result, normalized = {}, set()
        for key, value in values:
            name = unicodedata.normalize("NFC", key)
            if name in normalized:
                raise ValueError(f"Duplicate JSON key: {key}")
            normalized.add(name)
            result[key] = value
        return result

    def invalid_number(value):
        raise ValueError(f"Invalid JSON number: {value}")

    return json.loads(content, object_pairs_hook=pairs, parse_constant=invalid_number)


def digest(value, domain="data") -> str:
    return hashlib.sha256(
        canonical_bytes(["dbwarden", domain, SPEC_VERSION, value])
    ).hexdigest()


def seal_spec(spec: dict) -> dict:
    result = canonical_value(spec)
    result.pop("canonical_checksum", None)
    result["canonical_checksum"] = digest(result, "spec")
    return result


def validate_spec(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise TypeError("Data spec must be an object")
    if (
        type(spec.get("spec_version")) is not int
        or spec["spec_version"] != SPEC_VERSION
    ):
        raise ValueError(
            "Unsupported data spec version; use the matching DBWarden release"
        )
    if (
        type(spec.get("expression_language_version")) is not int
        or spec["expression_language_version"] != EXPRESSION_LANGUAGE_VERSION
    ):
        raise ValueError("Unsupported data expression language version")
    if (
        type(spec.get("backend_semantics_version")) is not int
        or spec["backend_semantics_version"] != BACKEND_SEMANTICS_VERSION
    ):
        raise ValueError("Unsupported data backend semantics version")
    _validate_structure(spec)
    if seal_spec(spec)["canonical_checksum"] != spec.get("canonical_checksum"):
        raise ValueError("Data spec checksum mismatch")
    return spec


def _validate_structure(spec: dict) -> None:
    required = {
        "database",
        "declaration_set",
        "source_snapshots",
        "managed_rows",
        "transformations",
        "transitions",
        "canonical_checksum",
    }
    missing = sorted(required - spec.keys())
    if missing:
        raise ValueError(f"Data spec is missing required fields: {', '.join(missing)}")

    database = spec["database"]
    if not isinstance(database, dict):
        raise TypeError("Data spec database must be an object")
    for field in ("name", "backend"):
        if not isinstance(database.get(field), str) or not database[field]:
            raise ValueError(f"Data spec database.{field} must be a non-empty string")

    snapshots = spec["source_snapshots"]
    if not isinstance(snapshots, list):
        raise TypeError("Data spec source_snapshots must be an array")
    for snapshot in snapshots:
        _validate_snapshot(snapshot, database)

    declarations = []
    for kind in _DECLARATION_KINDS:
        records = spec.get(kind, [])
        if not isinstance(records, list):
            raise TypeError(f"Data spec {kind} must be an array")
        for record in records:
            _validate_declaration(kind, record, database)
            declarations.append(record)

    declaration_set = spec["declaration_set"]
    if not isinstance(declaration_set, dict):
        raise TypeError("Data spec declaration_set must be an object")
    ids = declaration_set.get("declaration_ids")
    if not isinstance(ids, list) or any(
        not isinstance(identity, str) or not identity for identity in ids
    ):
        raise ValueError("Data spec declaration_ids must contain non-empty strings")
    actual_ids = sorted(record["declaration_id"] for record in declarations)
    if ids != sorted(set(ids)) or ids != actual_ids:
        raise ValueError("Data spec declaration_ids do not match declaration records")
    expected = digest(
        sorted(declarations, key=lambda item: item["declaration_id"]),
        "declarations",
    )
    if declaration_set.get("declaration_checksum") != expected:
        raise ValueError("Data spec declaration checksum mismatch")


def _validate_snapshot(snapshot, database) -> None:
    if not isinstance(snapshot, dict):
        raise TypeError("Data source snapshot must be an object")
    for field in ("snapshot_id", "database", "backend", "schema_checksum"):
        if not isinstance(snapshot.get(field), str) or not snapshot[field]:
            raise ValueError(f"Data source snapshot {field} must be a non-empty string")
    if (
        snapshot["database"] != database["name"]
        or snapshot["backend"] != database["backend"]
    ):
        raise ValueError(
            "Data source snapshot database/backend does not match the spec"
        )
    parents = snapshot.get("parent_snapshot_ids")
    if (
        not isinstance(parents, list)
        or parents != sorted(set(parents))
        or any(not isinstance(parent, str) or not parent for parent in parents)
    ):
        raise ValueError("Data source snapshot parents must be sorted unique strings")
    if type(snapshot.get("format_version")) is not int:
        raise ValueError("Data source snapshot format_version must be an integer")


def _validate_declaration(kind, record, database) -> None:
    if not isinstance(record, dict):
        raise TypeError(f"Data spec {kind} entries must be objects")
    identity = record.get("declaration_id")
    if not isinstance(identity, str) or not identity:
        raise ValueError(f"Data spec {kind} entries require declaration_id")

    if kind in {"managed_rows", "transformations", "validations"}:
        _validate_table_ref(record.get("target_table"), database)
    if kind == "managed_rows":
        _require_string_list(record, "key_columns", nonempty=True)
        _require_string_list(record, "owned_columns")
        _require_policy(record, "on_missing", {"keep", "delete", "archive"})
        _require_rollback(
            record, {"irreversible", "restore_previous", "restore_captured"}
        )
        if not isinstance(record.get("row_source"), dict):
            raise TypeError("Managed rows require a row_source object")
    elif kind == "transformations":
        _require_string_list(record, "target_columns", nonempty=True)
        _require_policy(record, "on_unmatched", {"error", "keep"})
        _require_rollback(
            record,
            {"irreversible", "clear", "recompute", "capture", "restore_captured"},
        )
        if not isinstance(record.get("expression"), dict):
            raise TypeError("Transformations require an expression object")
    elif kind == "validations":
        values = record.get("validations")
        if not isinstance(values, list) or not values:
            raise ValueError("Validation declarations require validations")
    elif kind == "transitions":
        _validate_table_ref(record.get("source"), database)
        if not isinstance(record.get("source_snapshot"), dict):
            raise TypeError("Transitions require source_snapshot")
        if not isinstance(record.get("identity"), dict):
            raise TypeError("Transitions require identity metadata")
        coverage = record.get("coverage")
        if not isinstance(coverage, dict):
            raise TypeError("Transitions require coverage metadata")
        _require_policy(
            coverage,
            "mode",
            {"all", "subset", "exactly_once_match", "all_assigned_once"},
        )
        _require_policy(coverage, "on_unmatched", {"error", "ignore", "archive"})
        _require_policy(coverage, "overlap_policy", {"error", "fan_out", "priority"})
        targets = record.get("targets")
        if not isinstance(targets, list) or not targets:
            raise ValueError("Transitions require at least one target")
        for target in targets:
            if not isinstance(target, dict):
                raise TypeError("Transition targets must be objects")
            _validate_table_ref(target.get("target_table"), database)
            conflict = target.get("conflict")
            if not isinstance(conflict, dict):
                raise TypeError("Transition targets require conflict metadata")
            _require_policy(
                conflict,
                "policy",
                {"error", "ignore_if_equivalent", "overwrite"},
            )
        completion = record.get("completion")
        if not isinstance(completion, dict):
            raise TypeError("Transitions require completion metadata")
        _require_policy(completion, "policy", {"preserve", "drop", "archive", "keep"})
        _require_rollback(
            record,
            {
                "irreversible",
                "restore_preserved_source",
                "restore_captured_source",
                "capture",
            },
        )


def _validate_table_ref(value, database) -> None:
    if not isinstance(value, dict):
        raise TypeError("Data table reference must be an object")
    if value.get("database") != database["name"]:
        raise ValueError("Data table reference database does not match the spec")
    if not isinstance(value.get("table"), str) or not value["table"]:
        raise ValueError("Data table reference requires a table name")
    if value.get("schema") is not None and (
        not isinstance(value["schema"], str) or not value["schema"]
    ):
        raise ValueError("Data table reference schema must be null or non-empty")


def _require_string_list(value, field, *, nonempty=False) -> None:
    items = value.get(field)
    if (
        not isinstance(items, list)
        or (nonempty and not items)
        or any(not isinstance(item, str) or not item for item in items)
    ):
        qualifier = "non-empty " if nonempty else ""
        raise ValueError(f"Data declaration {field} must be a {qualifier}string array")


def _require_policy(value, field, allowed) -> None:
    if value.get(field) not in allowed:
        raise ValueError(f"Unsupported data {field} policy: {value.get(field)!r}")


def _require_rollback(record, allowed) -> None:
    rollback = record.get("rollback")
    if not isinstance(rollback, dict):
        raise TypeError("Data declaration requires rollback metadata")
    _require_policy(rollback, "policy", allowed)


def empty_spec(database: str, backend: str) -> dict:
    return seal_spec(
        {
            "spec_version": SPEC_VERSION,
            "database": {"name": database, "backend": backend},
            "declaration_set": {
                "declaration_ids": [],
                "declaration_checksum": digest([], "declarations"),
            },
            "source_snapshots": [],
            "managed_rows": [],
            "transformations": [],
            "transitions": [],
            "expression_language_version": EXPRESSION_LANGUAGE_VERSION,
            "backend_semantics_version": BACKEND_SEMANTICS_VERSION,
        }
    )
