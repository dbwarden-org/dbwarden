from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from typing import Any

from dbwarden.engine.safety.classifiers import (
    LEVELS,
    Safety,
    classify_operation,
    required_flags,
)

#: ``file_severity``/``read_trusted_plan`` reason when a migration file has no
#: plan sidecar at all. Plan-less files are governed by the missing_plan
#: preflight policy, unlike files whose plan resolves to explicit UNKNOWN
#: (parse failure, tamper evidence, tagged UNKNOWN).
MISSING_PLAN_REASON = "no plan"

#: ``read_trusted_plan`` reasons for plan integrity hardening (findings 1+2).
#: A trusted plan must carry its typed operations (so the severity block can be
#: re-derived from classified operations) and a keyed content hash (so file
#: integrity cannot be reforged by recomputing a public digest).
NO_TYPED_OPS_ANCHOR_REASON = "no typed operations anchor"
LEGACY_HASH_REASON = "legacy plan hash — regenerate"
MISSING_KEY_REASON = "plan key missing — regenerate plans with check --write-plan"

#: Project-root markers used to locate the project-local plan key. These match
#: the discovery rules elsewhere in dbwarden (dbwarden.py config discovery and
#: VCS/project markers).
_PROJECT_ROOT_MARKERS = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    ".git",
    ".hg",
    ".svn",
    "dbwarden.py",
)

_PLAN_SECRET_RELATIVE_PATH = Path(".dbwarden") / "plan.secret"
_PLAN_SECRET_BYTES = 32

#: Process-local fallback key. Only used when no project root can be resolved
#: from the calling context (in-memory ``bind_plan``/``content_hash`` calls in
#: tests and scripts with no project on disk). Real project flows always bind
#: and verify against the on-disk key so reads can fail closed.
_ephemeral_plan_key: bytes | None = None


def _ephemeral_key() -> bytes:
    global _ephemeral_plan_key
    if _ephemeral_plan_key is None:
        _ephemeral_plan_key = os.urandom(_PLAN_SECRET_BYTES)
    return _ephemeral_plan_key


def _find_project_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if any((candidate / marker).exists() for marker in _PROJECT_ROOT_MARKERS):
            return candidate
    return None


def _load_plan_key(root: Path) -> bytes | None:
    try:
        key = (root / _PLAN_SECRET_RELATIVE_PATH).read_bytes()
    except OSError:
        return None
    return key if len(key) == _PLAN_SECRET_BYTES else None


def _create_plan_key(root: Path) -> bytes:
    path = root / _PLAN_SECRET_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(_PLAN_SECRET_BYTES)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # A concurrent bind created the key first; reuse it rather than
        # desynchronizing bind/verify within the project.
        existing = _load_plan_key(root)
        if existing is None:
            raise ValueError(f"Plan key exists but is unreadable: {path}")
        return existing
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


def _resolve_bind_key(project_root: str | Path | None) -> bytes:
    """Key used when *writing* plan integrity metadata.

    An explicit ``project_root`` pins the key to that project's on-disk secret
    (created on demand). Without one — in-memory binds with no project
    context — an ephemeral per-process key keeps bind/verify round-trips
    working; reads of on-disk plans in real projects always resolve the key
    from the plan's own location instead, so they fail closed without it.
    """
    if project_root is not None:
        root = _find_project_root(Path(project_root))
        if root is not None:
            key = _load_plan_key(root)
            if key is None:
                key = _create_plan_key(root)
            return key
    return _ephemeral_key()


def _resolve_verify_key(plan_path: Path) -> tuple[bytes | None, str]:
    """Key used when *reading* plan integrity metadata.

    Resolution is anchored at the plan file's own location so verification
    does not silently follow an unrelated working directory. Real projects
    (a project root discoverable from the plan path) must have the on-disk
    key; anything else fails closed. Context-free locations (temporary
    directories in tests, in-memory round-trips) fall back to the ephemeral
    per-process key.
    """
    root = _find_project_root(plan_path.parent)
    if root is None:
        return _ephemeral_key(), ""
    key = _load_plan_key(root)
    if key is None:
        return None, MISSING_KEY_REASON
    return key, ""


def _keyed_hash(key: bytes, content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    return "hmac-sha256:" + hmac.new(
        key, normalized.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def content_hash(content: str, *, project_root: str | Path | None = None) -> str:
    """Compute the plan content integrity hash.

    The hash is an HMAC-SHA256 keyed by the project-local secret
    ``.dbwarden/plan.secret`` (created on demand, mode 0o600) so that file
    integrity cannot be reforged by recomputing a public digest. When no
    project root can be resolved — and no explicit ``project_root`` is given —
    an ephemeral per-process key is used; ``read_trusted_plan`` fails closed
    for on-disk plans in real projects, so the ephemeral fallback only covers
    context-free in-memory flows.
    """
    if project_root is not None:
        return _keyed_hash(_resolve_bind_key(project_root), content)
    root = _find_project_root(Path.cwd())
    if root is None:
        return _keyed_hash(_ephemeral_key(), content)
    key = _load_plan_key(root)
    if key is None:
        key = _create_plan_key(root)
    return _keyed_hash(key, content)


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
    project_root: str | Path | None = None,
) -> dict:
    plan.update(
        schema_version="1.1",
        backend=backend,
        content_hash=_keyed_hash(_resolve_bind_key(project_root), content),
        severity=severity_metadata(ops, backend, provenance),
        required_flags=required_flags(ops, backend),
    )
    # Persist the typed operations under the recognized anchor key so
    # ``read_trusted_plan`` can re-derive the severity block from real
    # classifications. An absent or empty anchor fails verification closed.
    plan["upgrade_ops" if provenance == "generated" else "operations"] = list(ops)
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
        is_data_plan = any(
            key in plan for key in ("data_spec", "data_execution", "data_bundle")
        )
        if not is_data_plan and not typed:
            # Hardening (findings 1+2): trusted provenance must carry the
            # typed operations the generator classified (under the recognized
            # key for its provenance) so the severity block can be re-derived
            # from real classifications. Without that anchor a fabricated,
            # self-consistent severity block plus a recomputed hash would
            # pass every check. Data-bundle plans are instead anchored by the
            # frozen-bundle verification below (manifest + frozen DATA_SPEC).
            return None, NO_TYPED_OPS_ANCHOR_REASON
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
        stored_hash = plan.get("content_hash")
        if not isinstance(stored_hash, str) or stored_hash.startswith("sha256:"):
            return None, LEGACY_HASH_REASON
        key, key_reason = _resolve_verify_key(path)
        if key is None:
            return None, key_reason
        if stored_hash != _keyed_hash(key, content):
            return None, "SQL content hash mismatch"
        if "data_spec" in plan or "-- dbwarden: data-bundle" in content.splitlines():
            from dbwarden.data.artifacts import verify_bundle
            from dbwarden.data.ir import validate_spec
            verify_bundle(path, plan)
            validate_spec(plan["data_spec"])
        return plan, ""
    except FileNotFoundError:
        return None, MISSING_PLAN_REASON
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        return None, "malformed plan"


def file_severity(path: str | Path) -> tuple[Safety, str]:
    plan, reason = read_trusted_plan(path)
    return (Safety(plan["severity"]["file"]), "") if plan else (Safety.UNKNOWN, reason)
