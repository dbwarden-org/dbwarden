from __future__ import annotations

from typing import Any

from dbwarden.engine.core.protocol import op_to_dict


def _diff_constraints(
    prev_tables: dict[str, Any],
    curr_tables: dict[str, Any],
    prev_constraints: dict[str, Any],
    curr_constraints: dict[str, Any],
    upgrade_ops: list[dict[str, Any]],
    rollback_ops: list[dict[str, Any]],
) -> None:
    all_tables = sorted(set(prev_tables.keys()) | set(curr_tables.keys()))
    for table_name in all_tables:
        prev_uniques = {c["name"]: c for c in prev_constraints.values() if c.get("type") == "unique" and c.get("table") == table_name and c.get("name")}
        curr_uniques = {c["name"]: c for c in curr_constraints.values() if c.get("type") == "unique" and c.get("table") == table_name and c.get("name")}

        prev_by_cols = {frozenset(c.get("columns", [])): (name, c) for name, c in prev_uniques.items()}
        curr_by_cols = {frozenset(c.get("columns", [])): (name, c) for name, c in curr_uniques.items()}
        handled_prev: set[str] = set()
        handled_curr: set[str] = set()
        for cols_sig, (prev_name, prev_entry) in prev_by_cols.items():
            curr_match = curr_by_cols.get(cols_sig)
            if curr_match is None:
                continue
            curr_name, curr_entry = curr_match
            if prev_name == curr_name:
                handled_prev.add(prev_name)
                handled_curr.add(curr_name)
            elif prev_entry.get("columns") == curr_entry.get("columns"):
                upgrade_ops.append({"type": "rename_unique_constraint", "table": table_name, "old_name": prev_name, "new_name": curr_name, "columns": list(cols_sig)})
                rollback_ops.insert(0, {"type": "rename_unique_constraint", "table": table_name, "old_name": curr_name, "new_name": prev_name, "columns": list(cols_sig)})
                handled_prev.add(prev_name)
                handled_curr.add(curr_name)

        for name, uq in prev_uniques.items():
            if name in handled_prev:
                continue
            if name not in curr_uniques or prev_uniques[name] != curr_uniques[name]:
                payload = {k: v for k, v in uq.items() if k not in {"type", "table"}}
                upgrade_ops.append({"type": "drop_unique_constraint", "table": table_name, "name": name, **payload})
                rollback_ops.insert(0, {"type": "add_unique_constraint", "table": table_name, "name": name, **payload})
        for name, uq in curr_uniques.items():
            if name in handled_curr:
                continue
            if name not in prev_uniques:
                payload = {k: v for k, v in uq.items() if k not in {"type", "table"}}
                upgrade_ops.append({"type": "add_unique_constraint", "table": table_name, "name": name, **payload})
                rollback_ops.insert(0, {"type": "drop_unique_constraint", "table": table_name, "name": name, **payload})

        prev_checks = {c["name"]: c for c in prev_constraints.values() if c.get("type") == "check" and c.get("table") == table_name and c.get("name")}
        curr_checks = {c["name"]: c for c in curr_constraints.values() if c.get("type") == "check" and c.get("table") == table_name and c.get("name")}
        for name, ck in prev_checks.items():
            prev_sig = {k: v for k, v in ck.items() if k not in {"type", "table", "columns"}}
            curr_sig = {k: v for k, v in curr_checks.get(name, {}).items() if k not in {"type", "table", "columns"}}
            if name not in curr_checks or prev_sig != curr_sig:
                payload = {k: v for k, v in ck.items() if k not in {"type", "table"}}
                upgrade_ops.append({"type": "drop_check_constraint", "table": table_name, "name": name, **payload})
                rollback_ops.insert(0, {"type": "add_check_constraint", "table": table_name, "name": name, **payload})
        for name, ck in curr_checks.items():
            if name not in prev_checks:
                payload = {k: v for k, v in ck.items() if k not in {"type", "table"}}
                upgrade_ops.append({"type": "add_check_constraint", "table": table_name, "name": name, **payload})
                rollback_ops.insert(0, {"type": "drop_check_constraint", "table": table_name, "name": name, **payload})

        prev_excludes = {c["name"]: c for c in prev_constraints.values() if c.get("type") == "exclude" and c.get("table") == table_name and c.get("name")}
        curr_excludes = {c["name"]: c for c in curr_constraints.values() if c.get("type") == "exclude" and c.get("table") == table_name and c.get("name")}
        for name, ex in prev_excludes.items():
            if name not in curr_excludes or prev_excludes[name] != curr_excludes.get(name):
                upgrade_ops.append({"type": "drop_exclude_constraint", "table": table_name, "name": name, "expression": ex.get("expression", "")})
                rollback_ops.insert(0, {"type": "add_exclude_constraint", "table": table_name, "name": name, "expression": ex.get("expression", "")})
        for name, ex in curr_excludes.items():
            if name not in prev_excludes:
                upgrade_ops.append({"type": "add_exclude_constraint", "table": table_name, "name": name, "expression": ex.get("expression", "")})
                rollback_ops.insert(0, {"type": "drop_exclude_constraint", "table": table_name, "name": name, "expression": ex.get("expression", "")})

        prev_fks = [c for c in prev_constraints.values() if c.get("type") == "foreign_key" and c.get("table") == table_name]
        curr_fks = [c for c in curr_constraints.values() if c.get("type") == "foreign_key" and c.get("table") == table_name]

        def _fk_sig(fk: dict[str, Any]) -> tuple:
            return (
                frozenset(fk.get("columns", [])),
                fk.get("referenced_table") or fk.get("referred_table", ""),
                frozenset(fk.get("referenced_columns", fk.get("referred_columns", []))),
                fk.get("on_delete", "NO ACTION"),
                fk.get("on_update", "NO ACTION"),
                bool(fk.get("deferrable", False)),
                fk.get("match"),
            )

        prev_fk_sigs = {_fk_sig(fk) for fk in prev_fks}
        curr_fk_sigs = {_fk_sig(fk) for fk in curr_fks}
        for fk in prev_fks:
            if _fk_sig(fk) not in curr_fk_sigs:
                upgrade_ops.append({
                    "type": "drop_foreign_key", "table": table_name,
                    **({"name": fk["name"]} if fk.get("name") else {}),
                    "columns": fk["columns"], "referenced_table": fk["referenced_table"], "referenced_columns": fk["referenced_columns"],
                })
                rollback_ops.insert(0, {
                    "type": "add_foreign_key", "table": table_name,
                    **({"name": fk["name"]} if fk.get("name") else {}),
                    "columns": fk["columns"], "referenced_table": fk["referenced_table"], "referenced_columns": fk["referenced_columns"],
                    "on_delete": fk.get("on_delete", "NO ACTION"), "on_update": fk.get("on_update", "NO ACTION"), "deferrable": bool(fk.get("deferrable", False)),
                })
        for fk in curr_fks:
            if _fk_sig(fk) not in prev_fk_sigs:
                upgrade_ops.append({
                    "type": "add_foreign_key", "table": table_name,
                    **({"name": fk["name"]} if fk.get("name") else {}),
                    "columns": fk["columns"], "referenced_table": fk["referenced_table"], "referenced_columns": fk["referenced_columns"],
                    "on_delete": fk.get("on_delete", "NO ACTION"), "on_update": fk.get("on_update", "NO ACTION"), "deferrable": bool(fk.get("deferrable", False)),
                })
                rollback_ops.insert(0, {
                    "type": "drop_foreign_key", "table": table_name,
                    **({"name": fk["name"]} if fk.get("name") else {}),
                    "columns": fk["columns"], "referenced_table": fk["referenced_table"], "referenced_columns": fk["referenced_columns"],
                })


def _diff_enums(prev_enums: dict[str, list[str]], curr_enums: dict[str, list[str]], upgrade_ops: list[dict[str, Any]], rollback_ops: list[dict[str, Any]]) -> None:
    from dbwarden.plugin import ObjectPluginRegistry

    if ObjectPluginRegistry.has_handler("enum"):
        _handler = ObjectPluginRegistry.handlers()["enum"].handler
        _snap = _handler.canonicalize(prev_enums)
        _model = _handler.canonicalize(curr_enums)
        _up, _rb = _handler.diff(_snap, _model)
        for op in _up:
            upgrade_ops.append(op_to_dict(op))
        for op in _rb:
            rollback_ops.append(op_to_dict(op))
