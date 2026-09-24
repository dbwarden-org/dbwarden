from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from dbwarden.engine.safety.classifiers import (
    LEVELS,
    Safety,
    classify_operation,
    required_flags,
)


def content_hash(content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def severity_metadata(
    ops: list[dict[str, Any]], backend: str, provenance: str = "generated"
) -> dict:
    entries = []
    for index, op in enumerate(ops):
        entry = {
            "id": op.get("id", f"op:{index}"),
            "kind": op["type"],
            "severity": classify_operation(op, backend).value,
        }
        for key in ("table", "column"):
            if isinstance(op.get(key), str):
                entry[key] = op[key]
        entries.append(entry)
    maximum = max(
        (Safety(op["severity"]) for op in entries),
        default=Safety.SAFE,
        key=LEVELS.index,
    )
    return {"file": maximum.value, "provenance": provenance, "ops": entries}


def bind_plan(
    plan: dict,
    content: str,
    ops: list[dict],
    backend: str,
    *,
    provenance: str = "generated",
) -> dict:
    plan.update(
        schema_version="1.1",
        backend=backend,
        content_hash=content_hash(content),
        severity=severity_metadata(ops, backend, provenance),
        required_flags=required_flags(ops, backend),
    )
    return plan


def read_trusted_plan(path: str | Path) -> tuple[dict | None, str]:
    path = Path(path)
    try:
        from dbwarden.data.ir import parse_json
        plan = parse_json(path.with_suffix(".plan.json").read_text(encoding="utf-8"))
        if not isinstance(plan, dict):
            return None, "invalid plan object"
        severity = plan.get("severity")
        if not isinstance(severity, dict) or severity.get("provenance") not in (
            "generated",
            "static",
        ):
            return None, "no trusted provenance"
        file_level = Safety(severity.get("file"))
        ops = severity.get("ops")
        if not isinstance(ops, list):
            return None, "invalid operation list"
        ids = set()
        levels = []
        for op in ops:
            if (
                not isinstance(op, dict)
                or not isinstance(op.get("id"), str)
                or not isinstance(op.get("kind"), str)
            ):
                return None, "invalid operation metadata"
            if op["id"] in ids:
                return None, "duplicate operation ID"
            ids.add(op["id"])
            levels.append(Safety(op.get("severity")))
        if file_level != max(levels, default=Safety.SAFE, key=LEVELS.index):
            return None, "file severity does not match operations"
        if file_level == Safety.UNKNOWN:
            return None, "unclassified operations"
        if not isinstance(plan.get("required_flags"), list) or any(
            flag != "--force" for flag in plan["required_flags"]
        ):
            return None, "invalid required flags"
        typed = (
            plan.get("upgrade_ops")
            if severity["provenance"] == "generated"
            else plan.get("operations")
        )
        if typed is not None:
            if not isinstance(typed, list) or any(
                not isinstance(op, dict) or not isinstance(op.get("type"), str)
                for op in typed
            ):
                return None, "invalid typed operations"
            if (
                severity_metadata(
                    typed, plan.get("backend", ""), severity["provenance"]
                )["ops"]
                != ops
            ):
                return None, "typed operations do not match severity metadata"
            if required_flags(typed, plan.get("backend", "")) != plan["required_flags"]:
                return None, "required flags do not match operations"
        content = path.read_text(encoding="utf-8")
        category = plan.get("category")
        split = severity.get("split")
        if "split" in severity and not isinstance(split, dict):
            return None, "invalid split metadata"
        if category is not None:
            if not isinstance(category, dict):
                return None, "invalid migration category"
            name, order, plugin = (
                category.get("name"),
                category.get("order"),
                category.get("plugin"),
            )
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", name)
                or type(order) is not int
            ):
                return None, "invalid migration category"
            if name in {"base", "deferred"}:
                if order != {"base": 0, "deferred": 100}[name] or plugin is not None:
                    return None, "invalid built-in migration category"
            elif (
                name == "unsplit"
                or order <= 0
                or order == 100
                or not isinstance(plugin, str)
                or not plugin
            ):
                return None, "invalid custom migration category"
            if (split or {}).get(
                "role"
            ) != name or f"-- dbwarden: category {name}" not in content.splitlines():
                return None, "migration category does not match SQL or split metadata"
        if plan.get("content_hash") != content_hash(content):
            return None, "SQL content hash mismatch"
        if "data_spec" in plan or "-- dbwarden: data-bundle" in content.splitlines():
            from dbwarden.data.artifacts import verify_bundle
            from dbwarden.data.ir import validate_spec
            verify_bundle(path, plan)
            validate_spec(plan["data_spec"])
        return plan, ""
    except FileNotFoundError:
        return None, "no plan"
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        return None, "malformed plan"


def file_severity(path: str | Path) -> tuple[Safety, str]:
    plan, reason = read_trusted_plan(path)
    return (Safety(plan["severity"]["file"]), "") if plan else (Safety.UNKNOWN, reason)
