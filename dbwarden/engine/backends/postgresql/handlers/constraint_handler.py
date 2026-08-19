from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.snapshot import (
    MigrationStatement,
    StatementOrder,
)


def _quote_constraint_name(name: str) -> str:
    """Quote PostgreSQL constraint identifiers that cannot be emitted bare."""
    from dbwarden.engine.backends.postgresql.render import _POSTGRES_RESERVED_WORDS

    if re.fullmatch(r"[a-z_][a-z0-9_$]*", name) and name.upper() not in _POSTGRES_RESERVED_WORDS:
        return name
    return '"' + name.replace('"', '""') + '"'


def _quote_relation(name: str) -> str:
    return ".".join(_quote_constraint_name(part) for part in name.split("."))


class ConstraintHandler(ObjectHandler):
    object_type: str = "constraint"
    op_types: tuple[str, ...] = (
        "add_unique_constraint",
        "drop_unique_constraint",
        "rename_unique_constraint",
        "add_check_constraint",
        "drop_check_constraint",
        "add_foreign_key",
        "drop_foreign_key",
    )
    run_phase: RunPhase = RunPhase.DIFF
    statement_order: StatementOrder = StatementOrder.ALTER_TABLE_CONSTRAINT

    def __init__(self) -> None:
        self._fk_name_cache: dict[str, int] = {}

    def _build_fk_name(self, table: str, columns: list[str]) -> str:
        cols = "_".join(columns)[:60]
        base = f"fk_{table}_{cols}"
        key = base.lower()
        cnt = self._fk_name_cache.get(key, 0)
        self._fk_name_cache[key] = cnt + 1
        if cnt:
            return f"{base}_{cnt}"
        return base

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        constraints = snapshot.get("constraints", {})
        result: dict[str, Any] = {}
        for name, c in constraints.items():
            table = c.get("table", "")
            if not table:
                continue
            if table not in result:
                result[table] = {"uniques": {}, "checks": {}, "fks": []}
            ctype = c.get("type")
            if ctype == "unique":
                result[table]["uniques"][name] = c
            elif ctype == "check":
                result[table]["checks"][name] = c
            elif ctype == "foreign_key":
                fk = dict(c)
                fk.setdefault("name", str(name).rsplit(".", 1)[-1])
                result[table]["fks"].append(fk)
        return result

    _snapshot: dict[str, Any] | None = None
    _view_tables: set[str] | None = None
    _model_table_names: set[str] | None = None

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        self._model_table_names = {getattr(t, "name", None) for t in model_tables}
        self._model_table_names.discard(None)
        result: dict[str, Any] = {}
        for table in model_tables:
            uniques: dict[str, Any] = {}
            for u in (table.uniques or []):
                name = u.get("name") or f"uq_{table.name}_{'_'.join(u.get('columns', []))}"
                uniques[name] = u
            checks: dict[str, Any] = {}
            for i, c in enumerate(table.checks or []):
                name = c.get("name") or f"ck_{table.name}_{i}"
                checks[name] = c
            fks = table.foreign_keys or []
            if uniques or checks or fks:
                result[table.name] = {"uniques": uniques, "checks": checks, "fks": fks}
        return result

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        return {}

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        if not spec:
            return {}
        for entry in spec.values():
            for key in ("uniques", "checks"):
                normalized: dict[str, Any] = {}
                for _name, item in entry.get(key, {}).items():
                    if not isinstance(item, dict):
                        continue
                    clean = {
                        k: v for k, v in item.items()
                        if k not in {"type", "table", "validated", "deferrable", "initially_deferred", "columns"}
                        or (k == "columns" and key == "uniques")
                        or (k in {"deferrable", "initially_deferred"} and v)
                    }
                    name = clean.get("name") or str(_name).split(".")[-1]
                    normalized[name] = clean
                entry[key] = normalized
            for fk in entry.get("fks", []):
                m = fk.get("match")
                if m is None or m == "SIMPLE":
                    fk.pop("match", None)
                fk.pop("type", None)
                fk.pop("table", None)
                fk.pop("validated", None)
                if not fk.get("deferrable"):
                    fk.pop("deferrable", None)
                fk.pop("initially_deferred", None)
                if "referenced_table" in fk and "referred_table" not in fk:
                    fk["referred_table"] = fk.pop("referenced_table")
                if "referenced_columns" in fk and "referred_columns" not in fk:
                    fk["referred_columns"] = fk.pop("referenced_columns")
        return spec

    def diff(
        self,
        snap_spec: dict[str, Any],
        model_spec: dict[str, Any],
    ) -> Tuple[List[Op], List[Op]]:
        upgrade_ops: list[Op] = []
        rollback_ops: list[Op] = []
        _view_tables = self._view_tables or set()
        all_tables = sorted(set(snap_spec.keys()) | set(model_spec.keys()))
        for tname in all_tables:
            if tname in _view_tables:
                continue
            snap_entry = snap_spec.get(tname, {"uniques": {}, "checks": {}, "fks": []})
            model_entry = model_spec.get(tname, {"uniques": {}, "checks": {}, "fks": []})

            snap_uniques = snap_entry.get("uniques", {})
            model_uniques = model_entry.get("uniques", {})

            snap_by_cols: dict[frozenset, tuple[str, dict]] = {}
            model_by_cols: dict[frozenset, tuple[str, dict]] = {}
            for n, u in snap_uniques.items():
                snap_by_cols[frozenset(u.get("columns", []))] = (n, u)
            for n, u in model_uniques.items():
                model_by_cols[frozenset(u.get("columns", []))] = (n, u)
            handled_snap: set[str] = set()
            handled_model: set[str] = set()
            for cols_sig, (snap_name, snap_entry_uq) in snap_by_cols.items():
                model_match = model_by_cols.get(cols_sig)
                if model_match is None:
                    continue
                model_name, model_entry_uq = model_match
                if snap_name == model_name:
                    handled_snap.add(snap_name)
                    handled_model.add(model_name)
                elif snap_entry_uq.get("columns") == model_entry_uq.get("columns"):
                    upgrade_ops.append(Op(
                        object_type="rename_unique_constraint",
                        upgrade_attrs={
                            "table": tname, "old_name": snap_name, "new_name": model_name,
                            "columns": list(cols_sig),
                        },
                        rollback_attrs={
                            "table": tname, "old_name": model_name, "new_name": snap_name,
                            "columns": list(cols_sig),
                        },
                    ))
                    rollback_ops.append(Op(
                        object_type="rename_unique_constraint",
                        upgrade_attrs={
                            "table": tname, "old_name": model_name, "new_name": snap_name,
                            "columns": list(cols_sig),
                        },
                        rollback_attrs={
                            "table": tname, "old_name": snap_name, "new_name": model_name,
                            "columns": list(cols_sig),
                        },
                    ))
                    handled_snap.add(snap_name)
                    handled_model.add(model_name)
            for name, uq in snap_uniques.items():
                if name in handled_snap:
                    continue
                if name not in model_uniques or snap_uniques[name] != model_uniques[name]:
                    payload = {k: v for k, v in uq.items() if k not in {"type", "table"}}
                    upgrade_ops.append(Op(
                        object_type="drop_unique_constraint",
                        upgrade_attrs={"table": tname, "name": name, **payload},
                        rollback_attrs={"table": tname, "name": name, **payload},
                    ))
                    rollback_ops.append(Op(
                        object_type="add_unique_constraint",
                        upgrade_attrs={"table": tname, "name": name, **payload},
                        rollback_attrs={"table": tname, "name": name, **payload},
                    ))
            for name, uq in model_uniques.items():
                if name in handled_model:
                    continue
                if name not in snap_uniques:
                    payload = {k: v for k, v in uq.items() if k not in {"type", "table"}}
                    upgrade_ops.append(Op(
                        object_type="add_unique_constraint",
                        upgrade_attrs={"table": tname, "name": name, **payload},
                        rollback_attrs={"table": tname, "name": name, **payload},
                    ))
                    rollback_ops.append(Op(
                        object_type="drop_unique_constraint",
                        upgrade_attrs={"table": tname, "name": name, **payload},
                        rollback_attrs={"table": tname, "name": name, **payload},
                    ))

            snap_checks = snap_entry.get("checks", {})
            model_checks = model_entry.get("checks", {})
            for name, ck in snap_checks.items():
                snap_sig = {k: v for k, v in ck.items() if k not in {"type", "table", "columns"}}
                model_sig = model_checks.get(name, {})
                model_sig_filtered = {k: v for k, v in model_sig.items() if k not in {"type", "table", "columns"}}
                if name not in model_checks or snap_sig != model_sig_filtered:
                    payload = {k: v for k, v in ck.items() if k not in {"type", "table"}}
                    upgrade_ops.append(Op(
                        object_type="drop_check_constraint",
                        upgrade_attrs={"table": tname, "name": name, **payload},
                        rollback_attrs={"table": tname, "name": name, **payload},
                    ))
                    rollback_ops.append(Op(
                        object_type="add_check_constraint",
                        upgrade_attrs={"table": tname, "name": name, **payload},
                        rollback_attrs={"table": tname, "name": name, **payload},
                    ))
            for name, ck in model_checks.items():
                if name not in snap_checks:
                    upgrade_ops.append(Op(
                        object_type="add_check_constraint",
                        upgrade_attrs={"table": tname, "name": name, **ck},
                        rollback_attrs={"table": tname, "name": name, **ck},
                    ))
                    rollback_ops.append(Op(
                        object_type="drop_check_constraint",
                        upgrade_attrs={"table": tname, "name": name, **ck},
                        rollback_attrs={"table": tname, "name": name, **ck},
                    ))

            snap_fks = snap_entry.get("fks", [])
            model_fks = model_entry.get("fks", [])

            def _fk_sig(fk: dict) -> tuple:
                return (
                    frozenset(fk.get("columns", [])),
                    fk.get("referenced_table") or fk.get("referred_table", ""),
                    frozenset(fk.get("referenced_columns", fk.get("referred_columns", []))),
                    fk.get("on_delete", "NO ACTION"),
                    fk.get("on_update", "NO ACTION"),
                    bool(fk.get("deferrable", False)),
                    fk.get("match"),
                )

            snap_fk_sigs = {_fk_sig(fk) for fk in snap_fks}
            model_fk_sigs = {_fk_sig(fk) for fk in model_fks}
            for fk in snap_fks:
                if _fk_sig(fk) not in model_fk_sigs:
                    _ref_table = fk.get("referenced_table") or fk.get("referred_table", "")
                    _ref_cols = fk.get("referenced_columns") or fk.get("referred_columns", [])
                    upgrade_ops.append(Op(
                        object_type="drop_foreign_key",
                        upgrade_attrs={
                            "table": tname,
                            "name": fk.get("name"),
                            "columns": fk["columns"],
                            "referenced_table": _ref_table,
                            "referenced_columns": _ref_cols,
                        },
                        rollback_attrs={
                            "table": tname,
                            "name": fk.get("name"),
                            "columns": fk["columns"],
                            "referenced_table": _ref_table,
                            "referenced_columns": _ref_cols,
                            "on_delete": fk.get("on_delete", "NO ACTION"),
                            "on_update": fk.get("on_update", "NO ACTION"),
                            "deferrable": bool(fk.get("deferrable", False)),
                            "initially_deferred": bool(fk.get("initially_deferred", False)),
                            "match": fk.get("match"),
                        },
                    ))
                    rollback_ops.append(Op(
                        object_type="add_foreign_key",
                        upgrade_attrs={
                            "table": tname,
                            "name": fk.get("name"),
                            "columns": fk["columns"],
                            "referenced_table": _ref_table,
                            "referenced_columns": _ref_cols,
                            "on_delete": fk.get("on_delete", "NO ACTION"),
                            "on_update": fk.get("on_update", "NO ACTION"),
                            "deferrable": bool(fk.get("deferrable", False)),
                            "initially_deferred": bool(fk.get("initially_deferred", False)),
                            "match": fk.get("match"),
                        },
                        rollback_attrs={
                            "table": tname,
                            "name": fk.get("name"),
                            "columns": fk["columns"],
                            "referenced_table": _ref_table,
                            "referenced_columns": _ref_cols,
                        },
                    ))
            for fk in model_fks:
                if _fk_sig(fk) not in snap_fk_sigs:
                    _ref_table = fk.get("referenced_table") or fk.get("referred_table", "")
                    _snap_tables = (self._snapshot or {}).get("tables", {})
                    _snap_tbl = _snap_tables.get(_ref_table)
                    _ref_cols_check = fk.get("referred_columns", fk.get("referenced_columns", []))
                    if _snap_tbl is None:
                        if self._model_table_names is not None and _ref_table not in self._model_table_names:
                            continue
                        _snap_ref_cols = set(_ref_cols_check)
                    else:
                        _snap_ref_cols = _snap_tbl.get("columns", {})
                    if not all(c in _snap_ref_cols for c in _ref_cols_check):
                        continue
                    _ref_cols = fk.get("referenced_columns", fk.get("referred_columns", []))
                    upgrade_ops.append(Op(
                        object_type="add_foreign_key",
                        upgrade_attrs={
                            "table": tname,
                            "columns": fk["columns"],
                            "referenced_table": _ref_table,
                            "referenced_columns": _ref_cols,
                            "on_delete": fk.get("on_delete", "NO ACTION"),
                            "on_update": fk.get("on_update", "NO ACTION"),
                            "deferrable": fk.get("deferrable", False),
                            "initially_deferred": fk.get("initially_deferred", False),
                            "match": fk.get("match"),
                        },
                        rollback_attrs={
                            "table": tname,
                            "columns": fk["columns"],
                            "referenced_table": _ref_table,
                            "referenced_columns": _ref_cols,
                        },
                    ))
                    rollback_ops.append(Op(
                        object_type="drop_foreign_key",
                        upgrade_attrs={
                            "table": tname,
                            "columns": fk["columns"],
                            "referenced_table": _ref_table,
                            "referenced_columns": _ref_cols,
                        },
                        rollback_attrs={
                            "table": tname,
                            "columns": fk["columns"],
                            "referenced_table": _ref_table,
                            "referenced_columns": _ref_cols,
                            "on_delete": fk.get("on_delete", "NO ACTION"),
                            "on_update": fk.get("on_update", "NO ACTION"),
                            "deferrable": fk.get("deferrable", False),
                            "match": fk.get("match"),
                        },
                    ))

        return upgrade_ops, rollback_ops

    def emit(
        self, op: Op, db_name: Optional[str] = None
    , **kwargs: Any) -> List[MigrationStatement]:
        from dbwarden.engine.snapshot import (
            _get_backend,
        )

        backend = _get_backend(db_name)
        stmts: list[MigrationStatement] = []
        ot = op.object_type
        attrs = op.upgrade_attrs
        table = attrs["table"]

        if ot in ("add_unique_constraint", "drop_unique_constraint"):
            name = attrs["name"]
            using = attrs.get("using")
            using_clause = f" USING INDEX {using}" if using else ""
            if ot == "add_unique_constraint":
                cols = ", ".join(attrs.get("columns", []))
                defer_clause = ""
                if backend == "postgresql" and attrs.get("deferrable"):
                    if attrs.get("initially_deferred"):
                        defer_clause = " DEFERRABLE INITIALLY DEFERRED"
                    else:
                        defer_clause = " DEFERRABLE INITIALLY IMMEDIATE"
                up = f"ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE ({cols}){using_clause}{defer_clause};"
                rb = f"ALTER TABLE {table} DROP CONSTRAINT {name};"
            else:
                up = f"ALTER TABLE {table} DROP CONSTRAINT {name};"
                cols = ", ".join(attrs.get("columns", []))
                defer_clause = ""
                if backend == "postgresql" and attrs.get("deferrable"):
                    if attrs.get("initially_deferred"):
                        defer_clause = " DEFERRABLE INITIALLY DEFERRED"
                    else:
                        defer_clause = " DEFERRABLE INITIALLY IMMEDIATE"
                rb = f"ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE ({cols}){using_clause}{defer_clause};"
            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_TABLE_CONSTRAINT,
                upgrade_sql=up, rollback_sql=rb,
            ))

        elif ot == "rename_unique_constraint":
            old_name = _quote_constraint_name(attrs["old_name"])
            new_name = _quote_constraint_name(attrs["new_name"])
            qtable = _quote_relation(table)
            up = f"ALTER TABLE {qtable} RENAME CONSTRAINT {old_name} TO {new_name};"
            rb = f"ALTER TABLE {qtable} RENAME CONSTRAINT {new_name} TO {old_name};"
            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_TABLE_CONSTRAINT,
                upgrade_sql=up, rollback_sql=rb,
            ))

        elif ot in ("add_check_constraint", "drop_check_constraint"):
            name = attrs["name"]
            no_inherit = " NO INHERIT" if (attrs.get("no_inherit") and backend == "postgresql") else ""
            not_valid = backend == "postgresql" and attrs.get("validated") is False
            nv = " NOT VALID" if not_valid else ""
            if ot == "add_check_constraint":
                expr = attrs.get("expression", "")
                up = f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expr}){no_inherit}{nv};"
                rb = f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name};"
                stmts.append(MigrationStatement(
                    order=StatementOrder.ALTER_TABLE_CONSTRAINT,
                    upgrade_sql=up, rollback_sql=rb,
                ))
                if not_valid:
                    from dbwarden.engine.file_parser import DBWARDEN_AUTOCOMMIT_MARKER
                    validate_up = f"{DBWARDEN_AUTOCOMMIT_MARKER}\nALTER TABLE {table} VALIDATE CONSTRAINT {name};"
                    validate_rb = f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name};"
                    stmts.append(MigrationStatement(
                        order=StatementOrder.VALIDATE_CONSTRAINT,
                        upgrade_sql=validate_up, rollback_sql=validate_rb,
                    ))
            else:
                up = f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name};"
                rollback_attrs = op.rollback_attrs or attrs
                rb_no_inherit = " NO INHERIT" if (rollback_attrs.get("no_inherit") and backend == "postgresql") else ""
                rb_nv = " NOT VALID" if backend == "postgresql" and rollback_attrs.get("validated") is False else ""
                expr = rollback_attrs.get("expression", "")
                rb = f"ALTER TABLE {table} ADD CONSTRAINT {rollback_attrs.get('name', name)} CHECK ({expr}){rb_no_inherit}{rb_nv};"
                stmts.append(MigrationStatement(
                    order=StatementOrder.ALTER_TABLE_CONSTRAINT,
                    upgrade_sql=up, rollback_sql=rb,
                ))

        elif ot in ("add_foreign_key", "drop_foreign_key"):
            columns = attrs.get("columns", [])
            ref_table = attrs.get("referenced_table", "")
            ref_columns = attrs.get("referenced_columns", [])
            fk_name = attrs.get("name") or self._build_fk_name(table, columns)
            not_valid = backend == "postgresql" and attrs.get("validated") is False

            if ot == "add_foreign_key":
                cols = ", ".join(columns)
                ref_cols = ", ".join(ref_columns)
                if backend == "sqlite":
                    stmts.append(MigrationStatement(
                        order=StatementOrder.ALTER_FOREIGN_KEY,
                        upgrade_sql=(
                            f"-- SQLite: ADD CONSTRAINT {fk_name} FOREIGN KEY ({cols}) "
                            f"REFERENCES {ref_table}({ref_cols}) (not supported)\n"
                            f"-- Recreate the table with the constraint included."
                        ),
                        rollback_sql="-- (inverse requires manual migration)",
                    ))
                else:
                    upgrade = (
                        f"ALTER TABLE {table} ADD CONSTRAINT {fk_name} "
                        f"FOREIGN KEY ({cols}) REFERENCES {ref_table}({ref_cols})"
                    )
                    on_delete = attrs.get("on_delete")
                    on_update = attrs.get("on_update")
                    if on_delete and on_delete != "NO ACTION":
                        upgrade += f" ON DELETE {on_delete}"
                    if on_update and on_update != "NO ACTION":
                        upgrade += f" ON UPDATE {on_update}"
                    match = attrs.get("match")
                    if backend == "postgresql" and match and match != "SIMPLE":
                        upgrade += f" MATCH {match}"
                    if backend == "postgresql" and attrs.get("deferrable"):
                        upgrade += " DEFERRABLE INITIALLY DEFERRED"
                    if not_valid:
                        upgrade += " NOT VALID"
                    upgrade += ";"
                    rollback = f"ALTER TABLE {table} DROP CONSTRAINT {fk_name};"
                    stmts.append(MigrationStatement(
                        order=StatementOrder.ALTER_FOREIGN_KEY,
                        upgrade_sql=upgrade, rollback_sql=rollback,
                    ))
                    if not_valid:
                        from dbwarden.engine.file_parser import DBWARDEN_AUTOCOMMIT_MARKER
                        validate_up = f"{DBWARDEN_AUTOCOMMIT_MARKER}\nALTER TABLE {table} VALIDATE CONSTRAINT {fk_name};"
                        validate_rb = f"ALTER TABLE {table} DROP CONSTRAINT {fk_name};"
                        stmts.append(MigrationStatement(
                            order=StatementOrder.VALIDATE_CONSTRAINT,
                            upgrade_sql=validate_up, rollback_sql=validate_rb,
                        ))
            else:
                cols = ", ".join(columns)
                ref_cols = ", ".join(ref_columns)
                fk_sql = f"ALTER TABLE {table} ADD CONSTRAINT {fk_name} FOREIGN KEY ({cols}) REFERENCES {ref_table}({ref_cols})"
                on_delete = attrs.get("on_delete")
                on_update = attrs.get("on_update")
                if on_delete and on_delete != "NO ACTION":
                    fk_sql += f" ON DELETE {on_delete}"
                if on_update and on_update != "NO ACTION":
                    fk_sql += f" ON UPDATE {on_update}"
                match = attrs.get("match")
                if backend == "postgresql" and match and match != "SIMPLE":
                    fk_sql += f" MATCH {match}"
                if backend == "postgresql" and attrs.get("deferrable"):
                    fk_sql += " DEFERRABLE INITIALLY DEFERRED"
                if not_valid:
                    fk_sql += " NOT VALID"
                fk_sql += ";"

                if backend in ("mysql", "mariadb"):
                    upgrade = f"ALTER TABLE {table} DROP FOREIGN KEY {fk_name};"
                elif backend == "sqlite":
                    stmts.append(MigrationStatement(
                        order=StatementOrder.ALTER_FOREIGN_KEY,
                        upgrade_sql=(
                            f"-- SQLite: DROP CONSTRAINT {fk_name} (not supported)\n"
                            f"-- Recreate the table without the constraint."
                        ),
                        rollback_sql="-- (inverse requires manual migration)",
                    ))
                    return stmts
                else:
                    upgrade = f"ALTER TABLE {table} DROP CONSTRAINT {fk_name};"
                stmts.append(MigrationStatement(
                    order=StatementOrder.ALTER_FOREIGN_KEY,
                    upgrade_sql=upgrade, rollback_sql=fk_sql,
                ))

        return stmts
