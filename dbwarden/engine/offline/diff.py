from __future__ import annotations

import json
from typing import Any

from dbwarden.engine.core.model_state import (
    _normalize_mysql_table_value,
    _normalize_type,
    normalize_model_state,
    reconstruct_model_column,
    reconstruct_model_table,
)
from dbwarden.engine.core.models import IndexInfo
from dbwarden.engine.core.protocol import op_to_dict
from dbwarden.engine.snapshot import (
    _check_ch_engine_recreate_allowed,
    _diff_ch_column_extras,
    _normalize_default,
    detect_renames,
    normalize_type,
    snap_to_model_key,
)
from dbwarden.engine.backends.clickhouse.handlers.ch_table_handler import _CH_OPTION_KEYS

from .constraints import _diff_constraints, _diff_enums


def diff_model_states(
    prev_state: dict, curr_state: dict, db_name: str | None = None,
) -> tuple[list[dict], list[dict]]:
    prev_state = normalize_model_state(prev_state)
    curr_state = normalize_model_state(curr_state)

    prev_tables = prev_state.get("tables", {})
    curr_tables = curr_state.get("tables", {})
    prev_constraints = prev_state.get("constraints", {}) or {}
    curr_constraints = curr_state.get("constraints", {}) or {}
    prev_indexes = prev_state.get("indexes", {}) or {}
    curr_indexes = curr_state.get("indexes", {}) or {}
    prev_enums = prev_state.get("enums", {}) or {}
    curr_enums = curr_state.get("enums", {}) or {}

    upgrade_ops: list[dict] = []
    rollback_ops: list[dict] = []

    all_table_names = set(prev_tables.keys()) | set(curr_tables.keys())

    for table_name in sorted(all_table_names):
        prev_entry = prev_tables.get(table_name)
        curr_entry = curr_tables.get(table_name)

        if prev_entry is None and curr_entry is not None:
            upgrade_ops.append({
                "type": "create_table",
                "table": table_name,
                "object_type": curr_entry.get("object_type", "table"),
                "state_table": curr_entry,
                "severity": "INFO",
            })
            rollback_ops.append({
                "type": "drop_table",
                "table": table_name,
                "object_type": curr_entry.get("object_type", "table"),
                "state_table": curr_entry,
                "severity": "WARNING",
            })
            continue

        if curr_entry is None and prev_entry is not None:
            upgrade_ops.append({
                "type": "drop_table",
                "table": table_name,
                "object_type": prev_entry.get("object_type", "table"),
                "state_table": prev_entry,
                "severity": "WARNING",
            })
            rollback_ops.insert(0, {
                "type": "create_table",
                "table": table_name,
                "object_type": prev_entry.get("object_type", "table"),
                "state_table": prev_entry,
                "severity": "INFO",
            })
            continue

        prev_cols = prev_entry.get("columns", {})
        curr_cols = curr_entry.get("columns", {})
        prev_model_cols = {name: reconstruct_model_column(col) for name, col in prev_cols.items()}
        curr_model_cols = {name: reconstruct_model_column(col) for name, col in curr_cols.items()}

        dropped_cols = [(col_name, prev_cols[col_name]) for col_name in prev_cols if col_name not in curr_cols]
        added_cols = [(col_name, curr_model_cols[col_name]) for col_name in curr_model_cols if col_name not in prev_cols]
        renames = detect_renames(table_name, dropped_cols, added_cols)
        renamed_old = {old for old, _ in renames}
        renamed_new = {new for _, new in renames}

        for old_name, new_name in renames:
            upgrade_ops.append({"type": "rename_column", "table": table_name, "old_name": old_name, "new_name": new_name})
            rollback_ops.insert(0, {"type": "rename_column", "table": table_name, "old_name": new_name, "new_name": old_name})

        all_col_names = set(prev_cols.keys()) | set(curr_cols.keys())
        for col_name in sorted(all_col_names):
            if col_name in renamed_old or col_name in renamed_new:
                continue

            prev_col = prev_cols.get(col_name)
            curr_col = curr_cols.get(col_name)

            if prev_col is None and curr_col is not None:
                col_type = curr_col.get("type") or "???"
                upgrade_ops.append({
                    "type": "add_column", "table": table_name, "column": col_name,
                    "definition": {"type": col_type, "nullable": curr_col.get("nullable", True), "default": curr_col.get("default")},
                    "model_column": reconstruct_model_column(curr_col),
                    "severity": "INFO",
                })
                rollback_ops.insert(0, {
                    "type": "drop_column", "table": table_name, "column": col_name,
                    "definition": curr_col,
                    "severity": "WARNING",
                })
                continue

            if curr_col is None and prev_col is not None:
                col_type = prev_col.get("type") or "???"
                upgrade_ops.append({
                    "type": "drop_column", "table": table_name, "column": col_name,
                    "definition": prev_col,
                    "severity": "WARNING",
                })
                rollback_ops.insert(0, {
                    "type": "add_column", "table": table_name, "column": col_name,
                    "definition": {"type": col_type, "nullable": prev_col.get("nullable", True), "default": prev_col.get("default")},
                    "model_column": reconstruct_model_column(prev_col),
                    "severity": "INFO",
                })
                continue

            prev_type = prev_col.get("type", "")
            curr_type = curr_col.get("type", "")
            if _normalize_type(prev_type) != _normalize_type(curr_type):
                upgrade_ops.append({
                    "type": "alter_column_type", "table": table_name, "column": col_name,
                    "model_type": curr_type, "snap_type": prev_type,
                    "severity": "WARNING",
                })
                rollback_ops.insert(0, {
                    "type": "alter_column_type", "table": table_name, "column": col_name,
                    "model_type": prev_type, "snap_type": curr_type,
                    "severity": "WARNING",
                })

            if prev_col.get("nullable") != curr_col.get("nullable"):
                upgrade_ops.append({
                    "type": "alter_column_nullable", "table": table_name, "column": col_name,
                    "nullable": curr_col.get("nullable", True),
                    "col_type": curr_type,
                    "severity": "WARNING",
                })
                rollback_ops.insert(0, {
                    "type": "alter_column_nullable", "table": table_name, "column": col_name,
                    "nullable": prev_col.get("nullable", True),
                    "col_type": prev_type,
                    "severity": "WARNING",
                })

            if prev_col.get("autoincrement") is not None and bool(prev_col.get("autoincrement")) != bool(curr_col.get("autoincrement")):
                upgrade_ops.append({
                    "type": "alter_column_autoincrement", "table": table_name, "column": col_name,
                    "autoincrement": bool(curr_col.get("autoincrement")), "col_type": curr_type,
                    "nullable": curr_col.get("nullable"),
                })
                rollback_ops.insert(0, {
                    "type": "alter_column_autoincrement", "table": table_name, "column": col_name,
                    "autoincrement": bool(prev_col.get("autoincrement")), "col_type": prev_type,
                    "nullable": prev_col.get("nullable"),
                })

            if _normalize_default(prev_col.get("default")) != _normalize_default(curr_col.get("default")):
                upgrade_ops.append({
                    "type": "alter_column_default", "table": table_name, "column": col_name,
                    "default": curr_col.get("default"),
                    "severity": "INFO",
                })
                rollback_ops.insert(0, {
                    "type": "alter_column_default", "table": table_name, "column": col_name,
                    "default": prev_col.get("default"),
                    "severity": "INFO",
                })

            if prev_col.get("comment") != curr_col.get("comment"):
                upgrade_ops.append({
                    "type": "alter_column_comment", "table": table_name, "column": col_name,
                    "comment": curr_col.get("comment"),
                    "previous_comment": prev_col.get("comment"),
                    "col_type": curr_type,
                    "nullable": curr_col.get("nullable"),
                    "autoincrement": curr_col.get("autoincrement"),
                    "my_meta": curr_col.get("my_column", {}),
                    "severity": "INFO",
                })
                rollback_ops.insert(0, {
                    "type": "alter_column_comment", "table": table_name, "column": col_name,
                    "comment": prev_col.get("comment"),
                    "previous_comment": curr_col.get("comment"),
                    "col_type": prev_type,
                    "nullable": prev_col.get("nullable"),
                    "autoincrement": prev_col.get("autoincrement"),
                    "my_meta": prev_col.get("my_column", {}),
                    "severity": "INFO",
                })

            prev_pg_col = prev_col.get("pg_column", {}) or {}
            curr_pg_col = curr_col.get("pg_column", {}) or {}
            norm_prev_pg_col = {snap_to_model_key(k): v for k, v in prev_pg_col.items() if snap_to_model_key(k) not in ("pg_type",)}
            norm_curr_pg_col = {k: v for k, v in curr_pg_col.items() if k not in ("pg_type", "pg_enum_name", "pg_enum_values")}
            if norm_prev_pg_col != norm_curr_pg_col:
                upgrade_ops.append({
                    "type": "alter_pg_column_meta", "table": table_name, "column": col_name,
                    "col_type": curr_type, "snap_type": prev_type,
                    "from_pg_column": prev_pg_col, "to_pg_column": curr_pg_col,
                })
                rollback_ops.insert(0, {
                    "type": "alter_pg_column_meta", "table": table_name, "column": col_name,
                    "col_type": prev_type, "snap_type": curr_type,
                    "from_pg_column": curr_pg_col, "to_pg_column": prev_pg_col,
                })

            _diff_ch_column_extras(
                prev_col.get("ch_column", {}) or {},
                curr_col.get("ch_column", {}) or {},
                table_name,
                col_name,
                upgrade_ops,
                rollback_ops,
            )

            prev_sq_col = prev_col.get("sq_column", {}) or {}
            curr_sq_col = curr_col.get("sq_column", {}) or {}
            if prev_sq_col != curr_sq_col:
                upgrade_ops.append({
                    "type": "alter_sq_column_meta", "table": table_name, "column": col_name,
                    "from_value": prev_sq_col, "to_value": curr_sq_col,
                })
                rollback_ops.insert(0, {
                    "type": "alter_sq_column_meta", "table": table_name, "column": col_name,
                    "from_value": curr_sq_col, "to_value": prev_sq_col,
                })

            prev_my_col = prev_col.get("my_column", {}) or {}
            curr_my_col = curr_col.get("my_column", {}) or {}
            if prev_my_col != curr_my_col:
                upgrade_ops.append({
                    "type": "alter_my_column_meta", "table": table_name, "column": col_name,
                    "col_type": curr_type, "snap_type": prev_type,
                    "from_my_column": prev_my_col, "to_my_column": curr_my_col,
                    "nullable": curr_col.get("nullable", True),
                    "default": curr_col.get("default"),
                    "comment": curr_col.get("comment"),
                    "autoincrement": curr_col.get("autoincrement"),
                    "snap_nullable": prev_col.get("nullable", True),
                    "snap_default": prev_col.get("default"),
                    "snap_comment": prev_col.get("comment"),
                })
                rollback_ops.insert(0, {
                    "type": "alter_my_column_meta", "table": table_name, "column": col_name,
                    "col_type": prev_type, "snap_type": curr_type,
                    "from_my_column": curr_my_col, "to_my_column": prev_my_col,
                    "nullable": prev_col.get("nullable", True),
                    "default": prev_col.get("default"),
                    "comment": prev_col.get("comment"),
                    "autoincrement": prev_col.get("autoincrement"),
                    "snap_nullable": curr_col.get("nullable", True),
                    "snap_default": curr_col.get("default"),
                    "snap_comment": curr_col.get("comment"),
                })

        prev_spec = prev_entry.get("backend_table_spec", {}) or {}
        curr_spec = curr_entry.get("backend_table_spec", {}) or {}
        prev_ch_spec = prev_spec if prev_spec.get("backend") == "clickhouse" else {}
        curr_ch_spec = curr_spec if curr_spec.get("backend") == "clickhouse" else {}
        if prev_ch_spec.get("ch_engine") != curr_ch_spec.get("ch_engine") and prev_ch_spec.get("ch_engine") is not None and curr_ch_spec.get("ch_engine") is not None:
            _check_ch_engine_recreate_allowed(prev_ch_spec, curr_ch_spec, table_name)
            from dbwarden.engine.snapshot.ch_utils import classify_clickhouse_recreate_rollback
            rollback_kind, rollback_reason = classify_clickhouse_recreate_rollback(
                prev_ch_spec.get("ch_engine"),
                curr_ch_spec.get("ch_engine"),
            )
            upgrade_ops.append({
                "type": "recreate_ch_table",
                "table": table_name,
                "reason": "ch_engine",
                "from_table": prev_entry,
                "to_table": curr_entry,
                "drop_old_after_swap": False,
                "preserve_old_suffix": "__dbw_old",
                "failed_suffix": "__dbw_failed",
                "rollback_kind": rollback_kind,
                "rollback_reason": rollback_reason,
                "__irreversible": rollback_kind == "irreversible",
            })
            rollback_ops.insert(0, {
                "type": "recreate_ch_table",
                "table": table_name,
                "reason": "ch_engine",
                "from_table": curr_entry,
                "to_table": prev_entry,
                "drop_old_after_swap": False,
                "preserve_old_suffix": "__dbw_failed",
                "failed_suffix": "__dbw_old",
                "rollback_kind": rollback_kind,
                "rollback_reason": rollback_reason,
                "__irreversible": rollback_kind == "irreversible",
            })
        else:
            ch_changes: dict[str, dict[str, Any]] = {}
            for key in _CH_OPTION_KEYS:
                snap_val = prev_ch_spec.get(key)
                model_val = curr_ch_spec.get(key)
                if json.dumps(snap_val, sort_keys=True, default=str) != json.dumps(model_val, sort_keys=True, default=str):
                    if snap_val is None and model_val is None:
                        continue
                    ch_changes[key] = {"from": snap_val, "to": model_val}
            if ch_changes:
                upgrade_ops.append({
                    "type": "alter_ch_options",
                    "table": table_name,
                    "changes": ch_changes,
                })
                rollback_ops.append({
                    "type": "alter_ch_options",
                    "table": table_name,
                    "changes": {k: {"from": v["to"], "to": v["from"]} for k, v in ch_changes.items()},
                })

        prev_pg_table = prev_spec if prev_spec.get("backend") == "postgresql" else {}
        curr_pg_table = curr_spec if curr_spec.get("backend") == "postgresql" else {}
        scalar_keys = {k for k in set(prev_pg_table.keys()) | set(curr_pg_table.keys()) if k != "pg_excludes"}
        for key in sorted(scalar_keys):
            if prev_pg_table.get(key) != curr_pg_table.get(key):
                upgrade_ops.append({
                    "type": "alter_pg_table", "table": table_name, "key": key,
                    "from_value": prev_pg_table.get(key), "to_value": curr_pg_table.get(key),
                })
                rollback_ops.insert(0, {
                    "type": "alter_pg_table", "table": table_name, "key": key,
                    "from_value": curr_pg_table.get(key), "to_value": prev_pg_table.get(key),
                })

        prev_my_table = prev_spec if prev_spec.get("backend") == "mysql" else {}
        curr_my_table = curr_spec if curr_spec.get("backend") == "mysql" else {}
        scalar_keys = {k for k in set(prev_my_table.keys()) | set(curr_my_table.keys()) if k != "backend"}
        for key in sorted(scalar_keys):
            prev_val = _normalize_mysql_table_value(key, prev_my_table.get(key))
            curr_val = _normalize_mysql_table_value(key, curr_my_table.get(key))
            if key == "my_auto_increment" and curr_val is None:
                continue
            if prev_val != curr_val:
                upgrade_ops.append({
                    "type": "alter_my_table", "table": table_name, "key": key,
                    "from_value": prev_my_table.get(key), "to_value": curr_my_table.get(key),
                })
                rollback_ops.insert(0, {
                    "type": "alter_my_table", "table": table_name, "key": key,
                    "from_value": curr_my_table.get(key), "to_value": prev_my_table.get(key),
                })

        prev_sq_table = prev_spec if prev_spec.get("backend") == "sqlite" else {}
        curr_sq_table = curr_spec if curr_spec.get("backend") == "sqlite" else {}
        for key in ("sq_without_rowid", "sq_strict"):
            prev_val = bool(prev_sq_table.get(key, False))
            curr_val = bool(curr_sq_table.get(key, False))
            if prev_val != curr_val:
                upgrade_ops.append({
                    "type": "alter_sq_table", "table": table_name, "key": key,
                    "from_value": prev_val, "to_value": curr_val,
                })
                rollback_ops.insert(0, {
                    "type": "alter_sq_table", "table": table_name, "key": key,
                    "from_value": curr_val, "to_value": prev_val,
                })

        if prev_entry.get("comment") != curr_entry.get("comment"):
            upgrade_ops.append({
                "type": "alter_table_comment", "table": table_name,
                "comment": curr_entry.get("comment"),
                "previous_comment": prev_entry.get("comment"),
                "severity": "INFO",
            })
            rollback_ops.insert(0, {
                "type": "alter_table_comment", "table": table_name,
                "comment": prev_entry.get("comment"),
                "previous_comment": curr_entry.get("comment"),
                "severity": "INFO",
            })

    _diff_constraints(prev_tables, curr_tables, prev_constraints, curr_constraints, upgrade_ops, rollback_ops)

    new_tables = {op["table"] for op in upgrade_ops if op["type"] == "create_table"}

    from dbwarden.engine.backends.postgresql.handlers import IndexHandler
    _idx_handler = IndexHandler()

    def _group_indexes_by_table(flat: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for _name, _idx in flat.items():
            _t = _idx.get("table")
            if _t:
                result.setdefault(_t, []).append(dict(_idx))
        return result

    _prev_grouped = _group_indexes_by_table(prev_indexes)
    _curr_grouped = _group_indexes_by_table({
        k: v for k, v in curr_indexes.items()
        if v.get("table") not in new_tables
    })

    _snap_idx_spec = _idx_handler.canonicalize({
        "indexes": _prev_grouped,
        "constraints": {},
        "snapshot_tables": set(prev_state.get("tables", {})),
    })
    _model_idx_spec = _idx_handler.canonicalize({
        "indexes": {
            _t: [IndexInfo.from_dict(_d) for _d in _idxs]
            for _t, _idxs in _curr_grouped.items()
        },
        "view_tables": set(),
    })
    _idx_up, _idx_rb = _idx_handler.diff(_snap_idx_spec, _model_idx_spec)
    for _op in _idx_up:
        upgrade_ops.append(op_to_dict(_op, skip_none=True))
    for _op in _idx_rb:
        rollback_ops.insert(0, op_to_dict(_op, skip_none=True))

    _diff_enums(prev_enums, curr_enums, upgrade_ops, rollback_ops)

    from dbwarden.engine.core.protocol import RunPhase as _RunPhase
    from dbwarden.plugin import ObjectPluginRegistry as _ObjectPluginRegistry

    _state_handlers: list[Any] = []
    # Plugin handlers that diff model state (policies, grants, ...) participate
    # here too. PREAMBLE handlers are skipped: they diff against the config,
    # which offline state does not carry. "enum" is already handled above.
    for _registration in _ObjectPluginRegistry.handlers().values():
        _plugin_handler = _registration.handler
        if _plugin_handler.run_phase == _RunPhase.PREAMBLE:
            continue
        if _plugin_handler.object_type == "enum":
            continue
        _state_handlers.append(_plugin_handler)

    for _handler in _state_handlers:
        _snap_spec = _handler.extract(prev_state)
        _model_spec = _handler.extract(curr_state)
        _up, _rb = _handler.diff(
            _handler.canonicalize(_snap_spec),
            _handler.canonicalize(_model_spec),
        )
        for op in _up:
            upgrade_ops.append(op_to_dict(op))
        for op in _rb:
            rollback_ops.append(op_to_dict(op))

    common_tables = set(prev_tables.keys()) & set(curr_tables.keys())
    for op in upgrade_ops + rollback_ops:
        if op.get("type") == "recreate_ch_table":
            tname = op["table"]
            mvs = sorted(
                en for en in common_tables
                if (prev_tables[en].get("backend_table_spec", {}) or {}).get("ch_to_table") == tname
                and (curr_tables[en].get("backend_table_spec", {}) or {}).get("ch_to_table") == tname
            )
            if mvs:
                op["dependent_mvs"] = mvs

    recreate_tables = {op["table"] for op in upgrade_ops if op.get("type") == "recreate_ch_table"}
    if recreate_tables:
        allowed = {"recreate_ch_table", "drop_table", "create_table", "rename_table", "alter_enum_add_value"}
        upgrade_ops = [op for op in upgrade_ops if op.get("table") not in recreate_tables or op.get("type") in allowed]
        rollback_ops = [op for op in rollback_ops if op.get("table") not in recreate_tables or op.get("type") in allowed]

    from dbwarden.engine.model_discovery.type_mapping import is_sqlite_target

    if is_sqlite_target(db_name):
        from dbwarden.engine.backends.sqlite.collapse import (
            collapse_sqlite_ops,
            snapshot_state_entries,
        )

        upgrade_ops, rollback_ops = collapse_sqlite_ops(
            upgrade_ops,
            rollback_ops,
            from_entries=snapshot_state_entries(prev_state),
            to_entries=snapshot_state_entries(curr_state),
        )

    return upgrade_ops, rollback_ops
