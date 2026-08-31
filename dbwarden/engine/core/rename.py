from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dbwarden.engine.core.models import ModelColumn, ModelTable
from dbwarden.logging import get_component_logger

_snapshot_logger = get_component_logger("snapshot")


@dataclass
class TableRenameIntent:
    old_table: str
    new_table: str


RENAME_TABLE_OVERLAP_THRESHOLD = 0.6


def _get_normalize_type():
    from dbwarden.engine.snapshot import normalize_type
    return normalize_type


def detect_renames(
    table_name: str,
    dropped_columns: list[tuple[str, dict[str, Any]]],
    added_columns: list[tuple[str, ModelColumn]],
) -> list[tuple[str, str]]:
    normalize_type = _get_normalize_type()
    renames: list[tuple[str, str]] = []

    if not dropped_columns or not added_columns:
        return renames

    dropped_by_type: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for col_name, col_def in dropped_columns:
        col_type = col_def.get("type", "")
        dropped_by_type.setdefault(col_type, []).append((col_name, col_def))

    added_by_type: dict[str, list[tuple[str, ModelColumn]]] = {}
    for col_name, model_col in added_columns:
        col_type = normalize_type(model_col.type)["type"]
        added_by_type.setdefault(col_type, []).append((col_name, model_col))

    used_dropped: set[str] = set()
    used_added: set[str] = set()

    for col_type in list(dropped_by_type.keys()):
        if col_type not in added_by_type:
            continue

        drops = dropped_by_type[col_type]
        adds = added_by_type[col_type]

        available_drops = [(n, d) for n, d in drops if n not in used_dropped]
        available_adds = [(n, m) for n, m in adds if n not in used_added]

        if not available_drops or not available_adds:
            continue

        if len(available_drops) == 1 and len(available_adds) == 1:
            drop_name = available_drops[0][0]
            add_name = available_adds[0][0]
            if drop_name != add_name:
                renames.append((drop_name, add_name))
                used_dropped.add(drop_name)
                used_added.add(add_name)
        elif len(available_drops) == len(available_adds):
            for drop_name, _ in available_drops:
                if drop_name not in used_dropped:
                    for add_name, _ in available_adds:
                        if add_name not in used_added:
                            renames.append((drop_name, add_name))
                            used_dropped.add(drop_name)
                            used_added.add(add_name)
                            break

    return renames


def _compute_table_overlap(
    dropped_table: str,
    added_table: str,
    snapshot: dict[str, Any],
    model_tables: list[ModelTable],
) -> float:
    normalize_type = _get_normalize_type()
    snap_cols = snapshot["tables"][dropped_table]["columns"]
    model_table = next((t for t in model_tables if t.name == added_table), None)
    if model_table is None:
        return 0.0

    model_cols = {
        col.name.lower(): normalize_type(col.type)["type"]
        for col in model_table.columns
    }
    snap_cols_normalized = {
        name: col.get("type", "")
        for name, col in snap_cols.items()
    }

    matches = sum(
        1 for name, typ in model_cols.items()
        if snap_cols_normalized.get(name) == typ
    )
    max_cols = max(len(model_cols), len(snap_cols_normalized))
    return matches / max_cols if max_cols > 0 else 0.0


def _apply_rename_intents(
    upgrade_ops: list[dict[str, Any]],
    rollback_ops: list[dict[str, Any]],
    confirmed_renames: set[tuple[str, str, str]],
    resolved_from_map: dict[tuple[str, str, str], str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    confirmed_set: set[tuple[str, str, str]] = set()
    for table, old, new in confirmed_renames:
        confirmed_set.add((table, old, new))
    origin = resolved_from_map or {}

    rename_ops_by_key: dict[tuple[str, str, str], int] = {}
    for i, op in enumerate(upgrade_ops):
        if op["type"] == "rename_column":
            key = (op["table"], op["old_name"], op["new_name"])
            rename_ops_by_key[key] = i

    table_adds: dict[str, list[tuple[int, str]]] = {}
    table_drops: dict[str, list[tuple[int, str]]] = {}
    for i, op in enumerate(upgrade_ops):
        if op["type"] == "add_column":
            table_adds.setdefault(op["table"], []).append((i, op["column"]))
        elif op["type"] == "drop_column":
            table_drops.setdefault(op["table"], []).append((i, op["column"]))

    added_used: set[int] = set()
    dropped_used: set[int] = set()

    for table, old, new in sorted(confirmed_set, key=lambda x: (x[0], x[1], x[2])):
        key = (table, old, new)
        if key not in rename_ops_by_key:
            found = False
            for drop_i, drop_col in table_drops.get(table, []):
                if drop_i in dropped_used or drop_col != old:
                    continue
                for add_i, add_col in table_adds.get(table, []):
                    if add_i in added_used or add_col != new:
                        continue
                    upgrade_ops[drop_i] = {
                        "type": "rename_column", "table": table,
                        "old_name": old, "new_name": new,
                        "resolved_from": origin.get(key),
                    }
                    upgrade_ops[add_i] = None
                    rollback_ops[drop_i] = {
                        "type": "rename_column", "table": table,
                        "old_name": new, "new_name": old,
                    }
                    rollback_ops[add_i] = None
                    added_used.add(add_i)
                    dropped_used.add(drop_i)
                    found = True
                    break
                break
            if not found:
                _snapshot_logger.warning(
                    "Confirmed rename %s.%s -> %s could not be applied: "
                    "no matching drop+add pair found.",
                    table, old, new,
                )

    for key, op_i in rename_ops_by_key.items():
        table, old, new = key
        if (table, old, new) in confirmed_set:
            upgrade_ops[op_i]["resolved_from"] = origin.get(key)
        else:
            upgrade_ops[op_i] = {
                "type": "drop_column", "table": table,
                "column": old,
            }
            rollback_ops[op_i] = {
                "type": "add_column", "table": table,
                "column": old, "definition": {},
            }

    upgrade_ops = [op for op in upgrade_ops if op is not None]
    rollback_ops = [op for op in rollback_ops if op is not None]

    return upgrade_ops, rollback_ops
