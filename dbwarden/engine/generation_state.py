from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from dbwarden.engine.core.model_state import normalize_model_state
from dbwarden.engine.safety.plans import read_trusted_plan
from dbwarden.merge.marker import is_superseded
from dbwarden.output import warning

_METADATA = {
    "checksum",
    "exported_at",
    "applied_at",
    "dbwarden_version",
    "migration_id",
    "database_name",
    "database_type",
    "database",
    "_warning",
    "generation_base",
    "generation_applied",
    "generation_versions",
    "source",
    "applied_versions",
    "model_state",
}


def schema_state(state: dict) -> dict:
    from dbwarden.constants import INTERNAL_TABLE_PREFIXES

    normalized = normalize_model_state(deepcopy(state))
    normalized["tables"] = {name: entry for name, entry in normalized.get("tables", {}).items() if not name.startswith(INTERNAL_TABLE_PREFIXES)}
    for kind in ("indexes", "constraints"):
        if kind in normalized:
            normalized[kind] = {name: entry for name, entry in normalized[kind].items() if not entry.get("table", "").startswith(INTERNAL_TABLE_PREFIXES)}
    if int(state.get("format_version", 1)) < 2:
        from dbwarden.engine.core.model_state import (
            model_state_to_dict,
            reconstruct_model_table,
        )

        tables = []
        for name, entry in normalized.get("tables", {}).items():
            raw = state["tables"][name]
            spec = dict(raw.get("backend_table_spec") or {})
            for key in ("pg_table", "my_table", "sq_table", "ch_options"):
                spec.update(raw.get(key) or {})
            if spec:
                spec.setdefault(
                    "backend", raw.get("database_type", state.get("database_type"))
                )
            entry["backend_table_spec"] = spec
            for key in (
                "schema",
                "pg_view_definition",
                "pg_view_materialized",
                "pg_view_auto_refresh",
                "pg_policies",
                "pg_grants",
            ):
                if key in raw:
                    entry[key] = deepcopy(raw[key])
            for key, kind in (
                ("foreign_keys", "foreign_key"),
                ("uniques", "unique"),
                ("checks", "check"),
            ):
                entry[key] = [
                    dict(value)
                    for value in state.get("constraints", {}).values()
                    if value.get("table") == name and value.get("type") == kind
                ] or deepcopy(raw.get(key, []))
            entry["indexes"] = [
                dict(value)
                for value in state.get("indexes", {}).values()
                if value.get("table") == name
            ] or deepcopy(raw.get("indexes", []))
            tables.append(reconstruct_model_table(entry))
        normalized = model_state_to_dict(tables)
        if "enums" in state:
            normalized["enums"] = deepcopy(state["enums"])
    for entry in normalized.get("tables", {}).values():
        for key in ("indexes", "foreign_keys", "uniques", "checks"):
            entry.pop(key, None)
    if "configured_objects" in state:
        normalized["configured_objects"] = deepcopy(state["configured_objects"])
    return {key: value for key, value in normalized.items() if key not in _METADATA}


def configuration_state(state: dict, config, *, desired: bool = False) -> dict:
    from dbwarden.engine.core.protocol import RunPhase
    from dbwarden.plugin import ObjectPluginRegistry

    result = schema_state(state)
    specs = dict(result.get("configured_objects", {}))
    for registration in ObjectPluginRegistry.handlers().values():
        handler = registration.handler
        if handler.run_phase != RunPhase.PREAMBLE:
            continue
        if not desired and handler.object_type in specs:
            continue
        spec = (
            handler.model_spec_from_config(config)
            if desired
            else handler.extract(state)
        )
        specs[handler.object_type] = handler.canonicalize(spec)
    if specs:
        result["configured_objects"] = specs
    return result


def state_checksum(state: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            schema_state(state),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def state_changes(before: dict, after: dict, path: tuple[str, ...] = ()) -> list[dict]:
    changes = []
    for key in sorted(before.keys() | after.keys()):
        location = [*path, key]
        if (
            key in before
            and key in after
            and isinstance(before[key], dict)
            and isinstance(after[key], dict)
        ):
            changes.extend(state_changes(before[key], after[key], tuple(location)))
        elif key not in before or key not in after or before[key] != after[key]:
            change: dict[str, Any] = {
                "path": location,
                "before_exists": key in before,
                "after_exists": key in after,
            }
            if key in before:
                change["before"] = before[key]
            if key in after:
                change["after"] = after[key]
            changes.append(change)
    return changes


def apply_changes(state: dict, changes: list[dict]) -> None:
    for change in changes:
        path = change["path"]
        if (
            not isinstance(path, list)
            or not path
            or not all(isinstance(k, str) for k in path)
        ):
            raise ValueError(
                "Invalid typed state path. hint: regenerate the migration plan"
            )
        parent = state
        for key in path[:-1]:
            parent = parent[key]
        key = path[-1]
        if (key in parent) != change["before_exists"] or (
            key in parent and parent[key] != change.get("before")
        ):
            raise ValueError(
                f"Stale plan at {'.'.join(path)}. hint: apply or supersede the pending migration"
            )
        if change["after_exists"]:
            parent[key] = deepcopy(change["after"])
        else:
            del parent[key]


def attach_state_changes(ops: list[dict], before: dict, after: dict) -> None:
    for op in ops:
        op["state_changes"] = []
    for change in state_changes(schema_state(before), schema_state(after)):
        path = change["path"]
        table = path[1] if path[0] == "tables" and len(path) > 1 else None
        candidates = []
        for index, op in enumerate(ops):
            kind = op["type"]
            op_table = op.get("state_table_name", op.get("table", op.get("old_table")))
            if kind == "declarative_data":
                declaration = op["data_declaration"]
                op_table = declaration.get("target_table", declaration.get("source"))["table"]
                if any(
                    table in (source["source"]["table"], ".".join(
                        part for part in (source["source"].get("schema"), source["source"]["table"]) if part
                    ))
                    for source in declaration.get("merge", {}).get("inputs", [])
                ):
                    op_table = table
            score = 0
            if table and table in (op_table, op.get("new_table")):
                if kind in {
                    "create_table",
                    "drop_table",
                    "rename_table",
                    "recreate_sq_table",
                    "recreate_ch_table",
                    "declarative_data",
                }:
                    score = 4
                if (
                    len(path) > 3
                    and path[2] == "columns"
                    and path[3]
                    in (op.get("column"), op.get("old_name"), op.get("new_name"))
                ):
                    if kind in {
                        "add_column",
                        "drop_column",
                        "rename_column",
                        "safe_type_contract",
                    }:
                        score = 5
                    field = path[4] if len(path) > 4 else ""
                    if field and field in kind:
                        score = 6
                    if kind == "alter_column_type" and path[4:] == [
                        "ch_column",
                        "ch_type",
                    ]:
                        score = 6
                if (
                    len(path) > 2
                    and path[2] == "backend_table_spec"
                    and kind
                    in {
                        "alter_pg_table",
                        "alter_my_table",
                        "alter_sq_table",
                        "alter_ch_options",
                    }
                ):
                    key = op.get("key")
                    if (
                        len(path) == 3
                        or path[3] == key
                        or path[3] in op.get("changes", {})
                    ):
                        score = 6
                if path[2:] == ["comment"] and kind == "alter_table_comment":
                    score = 6
                if len(path) > 2 and path[2].startswith("pg_view_") and "view" in kind:
                    score = 6
                if op.get("safe_type_source"):
                    score = 0
            elif path[0] in {"indexes", "constraints"}:
                value = change.get("after", change.get("before", {}))
                if not isinstance(value, dict):
                    value = after.get(path[0], {}).get(
                        path[1], before.get(path[0], {}).get(path[1], {})
                    )
                if value.get("table") == op_table:
                    if (
                        "index" in kind
                        and path[0] == "indexes"
                        or "constraint" in kind
                        or "foreign_key" in kind
                    ) and op.get("columns") == value.get("columns"):
                        score = 3
                    if kind in {
                        "create_table",
                        "drop_table",
                        "rename_table",
                        "recreate_sq_table",
                        "declarative_data",
                    }:
                        score = 2
                    name = op.get("name") or op.get("index_name")
                    if name and name in (path[1], value.get("name")):
                        score = 7
            elif (
                path[0] == "configured_objects"
                and len(path) > 2
                and path[1] == op.get("handler")
            ):
                if path[2] in [
                    value
                    for key, value in op.items()
                    if key.endswith("_name") or key == "name"
                ]:
                    score = 8
            elif len(path) > 1 and path[1] in (
                op.get("name"),
                op.get("enum_name"),
                op.get("type_name"),
            ):
                score = 7
            if score:
                candidates.append((score, index, op))
        if not candidates:
            raise ValueError(
                f"Unrepresented schema change {'.'.join(path)}. hint: update the object handler before generating"
            )
        max(candidates, key=lambda candidate: candidate[:2])[2]["state_changes"].append(
            change
        )
    for op in ops:
        if op.get("safe_type_source"):
            op["state_changes"] = [
                {
                    "path": ["tables", op["table"], "columns", op["column"]],
                    "before_exists": False,
                    "after_exists": True,
                    "after": deepcopy(op["definition"]),
                }
            ]
        elif op["type"] == "safe_type_contract":
            op["state_changes"].insert(
                0,
                {
                    "path": ["tables", op["table"], "columns", op["temporary"]],
                    "before_exists": True,
                    "after_exists": False,
                    "before": deepcopy(op["definition"]),
                },
            )


def effective_state(
    baseline: dict,
    files: dict[str, str],
    *,
    applied: set[str] | None = None,
    strict: bool = False,
) -> tuple[dict, list[tuple[str, dict]]]:
    state = schema_state(baseline.get("generation_base", baseline))
    pending = []
    for version, filename in sorted(files.items()):
        if version in (applied or set()) or is_superseded(filename):
            continue
        plan, reason = read_trusted_plan(filename)
        ops = plan.get("upgrade_ops") if plan else None
        if (
            plan is None
            or not isinstance(ops, list)
            or not all(
                isinstance(op, dict) and isinstance(op.get("state_changes"), list)
                for op in ops
            )
        ):
            message = f"Pending migration {version} has no composable plan ({reason or 'typed state unavailable'}); effective state may be incomplete. hint: apply or regenerate it with a plan before splitting."
            if strict:
                raise ValueError(message)
            warning(message)
            continue
        if plan.get("base_checksum") != state_checksum(state):
            raise ValueError(
                f"Stale pending plan {version}: base checksum mismatch. hint: apply or supersede it before generating"
            )
        for op in ops:
            apply_changes(state, op["state_changes"])
        if plan.get("target_checksum") != state_checksum(state):
            raise ValueError(
                f"Invalid pending plan {version}: target checksum mismatch. hint: regenerate its typed plan"
            )
        pending.append((version, plan))
    return state, pending


def check_deferred_conflicts(
    pending: list[tuple[str, dict]], effective: dict, target: dict
) -> None:
    edits = state_changes(schema_state(effective), schema_state(target))
    for version, plan in pending:
        if plan["severity"].get("split", {}).get("role", "unsplit") in {
            "base",
            "unsplit",
        }:
            continue
        for op in plan["upgrade_ops"]:
            for change in op["state_changes"]:
                path = change["path"]
                if any(
                    edit["path"][: len(path)] == path
                    or path[: len(edit["path"])] == edit["path"]
                    for edit in edits
                ):
                    raise ValueError(
                        f"Models contradict pending deferred migration {version} at {'.'.join(path)}. hint: apply or supersede that file before regenerating"
                    )
