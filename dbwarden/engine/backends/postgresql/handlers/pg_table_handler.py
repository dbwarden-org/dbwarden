from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from dbwarden.engine.core.ordering import OrderingConstraint
from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.snapshot import MigrationStatement, StatementOrder


class PgTableHandler(ObjectHandler):
    ordering = OrderingConstraint()
    object_type: str = "pg_table"
    op_types: tuple[str, ...] = (
        "alter_pg_table",
        "alter_pg_storage_param",
        "alter_pg_rls",
        "add_exclude_constraint",
        "drop_exclude_constraint",
    )
    run_phase: RunPhase = RunPhase.DIFF
    statement_order: StatementOrder = StatementOrder.ALTER_TABLE_OPTIONS

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for tname, tdata in snapshot.get("tables", {}).items():
            pg_table = tdata.get("pg_table") or tdata.get("backend_table_spec") or {}
            if pg_table.get("backend") is not None and pg_table.get("backend") != "postgresql":
                pg_table = {}
            if pg_table:
                result[tname] = dict(pg_table)
        return result

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table in model_tables:
            pg_table = table.pg_table or {}
            if pg_table:
                result[table.name] = dict(pg_table)
        return result

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        return {}

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        for tname, pg_table in list(spec.items()):
            pg_table.pop("backend", None)
            if pg_table.get("pg_storage_params") is None:
                pg_table.pop("pg_storage_params", None)
            pg_table.pop("pg_partitions", None)
            if pg_table.get("pg_partition_of"):
                pg_table.pop("pg_inherits", None)
            if not pg_table:
                spec.pop(tname, None)
        return spec

    def diff(
        self,
        snap_spec: dict[str, Any],
        model_spec: dict[str, Any],
    ) -> Tuple[List[Op], List[Op]]:
        upgrade_ops: list[Op] = []
        rollback_ops: list[Op] = []

        all_tables = set(snap_spec.keys()) | set(model_spec.keys())
        for tname in sorted(all_tables):
            snap_pg_table = snap_spec.get(tname, {})
            model_pg_table = model_spec.get(tname, {})
            if not snap_pg_table and not model_pg_table:
                continue

            # Determine which scalar keys to exclude because they are
            # already represented in pg_storage_params (prevents SET/RESET
            # oscillation between the scalar and nested paths).
            _snap_storage_ks = set((snap_pg_table.get("pg_storage_params") or {}).keys())
            _model_storage_ks = set((model_pg_table.get("pg_storage_params") or {}).keys())
            _combined_storage_ks = _snap_storage_ks | _model_storage_ks

            all_keys = set(snap_pg_table.keys()) | set(model_pg_table.keys())
            scalar_keys = {
                k for k in all_keys
                if k not in ("pg_excludes", "pg_policies", "backend")
                and not (k.startswith("pg_") and k[3:] in _combined_storage_ks)
            }
            for key in sorted(scalar_keys):
                snap_val = snap_pg_table.get(key)
                model_val = model_pg_table.get(key)
                if key == "pg_inherits":
                    if isinstance(snap_val, str):
                        snap_val = [snap_val]
                    if isinstance(model_val, str):
                        model_val = [model_val]
                    snap_val = list(snap_val or [])
                    model_val = list(model_val or [])
                if snap_val != model_val:
                    upgrade_ops.append(Op(
                        object_type="alter_pg_table",
                        upgrade_attrs={
                            "table": tname, "key": key,
                            "from_value": snap_val,
                            "to_value": model_val,
                        },
                        rollback_attrs={
                            "table": tname, "key": key,
                            "from_value": model_val,
                            "to_value": snap_val,
                        },
                    ))
                    rollback_ops.append(Op(
                        object_type="alter_pg_table",
                        upgrade_attrs={
                            "table": tname, "key": key,
                            "from_value": model_val,
                            "to_value": snap_val,
                        },
                        rollback_attrs={
                            "table": tname, "key": key,
                            "from_value": snap_val,
                            "to_value": model_val,
                        },
                    ))

            # Exclude constraints
            snap_excl_list = snap_pg_table.get("pg_excludes", []) or []
            model_excl_list = model_pg_table.get("pg_excludes", []) or []
            snap_excludes = {e["name"]: e for e in snap_excl_list}
            model_excludes = {e["name"]: e for e in model_excl_list}

            for name, ex in snap_excludes.items():
                if name not in model_excludes or snap_excludes[name] != model_excludes.get(name):
                    upgrade_ops.append(Op(
                        object_type="drop_exclude_constraint",
                        upgrade_attrs={
                            "table": tname, "name": name,
                            "expression": ex.get("expression", ""),
                        },
                        rollback_attrs={
                            "table": tname, "name": name,
                            "expression": ex.get("expression", ""),
                        },
                    ))
                    rollback_ops.append(Op(
                        object_type="add_exclude_constraint",
                        upgrade_attrs={
                            "table": tname, "name": name,
                            "expression": ex.get("expression", ""),
                        },
                        rollback_attrs={
                            "table": tname, "name": name,
                            "expression": ex.get("expression", ""),
                        },
                    ))
            for name, ex in model_excludes.items():
                if name not in snap_excludes or snap_excludes[name] != ex:
                    upgrade_ops.append(Op(
                        object_type="add_exclude_constraint",
                        upgrade_attrs={
                            "table": tname, "name": name,
                            "expression": ex.get("expression", ""),
                        },
                        rollback_attrs={
                            "table": tname, "name": name,
                            "expression": ex.get("expression", ""),
                        },
                    ))
                    rollback_ops.append(Op(
                        object_type="drop_exclude_constraint",
                        upgrade_attrs={
                            "table": tname, "name": name,
                            "expression": ex.get("expression", ""),
                        },
                        rollback_attrs={
                            "table": tname, "name": name,
                            "expression": ex.get("expression", ""),
                        },
                    ))

        return upgrade_ops, rollback_ops

    def emit(
        self, op: Op, db_name: Optional[str] = None
    , **kwargs: Any) -> List[MigrationStatement]:
        from dbwarden.engine.snapshot import _get_backend

        backend = _get_backend(db_name)
        stmts: list[MigrationStatement] = []

        ot = op.object_type
        table = op.upgrade_attrs["table"]
        attrs = op.upgrade_attrs
        if ot == "alter_pg_storage_param":
            attrs = {**attrs, "key": "pg_storage_params",
                     "from_value": {attrs["param"]: attrs.get("from_value")},
                     "to_value": {attrs["param"]: attrs.get("to_value")}}
            ot = "alter_pg_table"
        elif ot == "alter_pg_rls":
            attrs = {**attrs, "key": "pg_rls", "to_value": attrs["enabled"],
                     "from_value": attrs.get("from_value", op.rollback_attrs.get("enabled", not attrs["enabled"]))}
            ot = "alter_pg_table"

        if ot == "alter_pg_table":
            if backend != "postgresql":
                return stmts
            key = attrs["key"]
            to_val = attrs.get("to_value")
            from_val = attrs.get("from_value")
            up: str
            rb: str
            if key in ("pg_rls", "pg_rls_force"):
                def rls(value):
                    action = ("FORCE" if value else "NO FORCE") if key == "pg_rls_force" else ("ENABLE" if value else "DISABLE")
                    return f"ALTER TABLE {table} {action} ROW LEVEL SECURITY;"
                up, rb = rls(to_val), rls(from_val)
            elif key == "pg_storage_params":
                def storage(before, after):
                    before, after = before or {}, after or {}
                    return "\n".join(
                        f"ALTER TABLE {table} SET ({param} = {after[param]});" if after.get(param) is not None
                        else f"ALTER TABLE {table} RESET ({param});"
                        for param in sorted(before.keys() | after.keys()) if before.get(param) != after.get(param)
                    )
                up, rb = storage(from_val, to_val), storage(to_val, from_val)
            elif key in ("pg_fillfactor", "fillfactor"):
                if to_val is not None:
                    up = f"ALTER TABLE {table} SET (fillfactor = {to_val});"
                else:
                    up = f"ALTER TABLE {table} RESET (fillfactor);"
                if from_val is not None:
                    rb = f"ALTER TABLE {table} SET (fillfactor = {from_val});"
                else:
                    rb = f"ALTER TABLE {table} RESET (fillfactor);"
            elif key == "pg_tablespace":
                if to_val:
                    up = f"ALTER TABLE {table} SET TABLESPACE {to_val};"
                else:
                    up = f"-- Cannot unset tablespace for {table}; move to default manually"
                if from_val:
                    rb = f"ALTER TABLE {table} SET TABLESPACE {from_val};"
                else:
                    rb = f"-- Cannot restore tablespace for {table}; move to default manually"
            elif key == "pg_inherits":
                if to_val:
                    parents = ", ".join(to_val)
                    up = f"ALTER TABLE {table} INHERIT {parents};"
                else:
                    up = f"-- Cannot remove all inheritance for {table} via ALTER"
                if from_val:
                    parents = ", ".join(from_val)
                    rb = f"ALTER TABLE {table} INHERIT {parents};"
                else:
                    rb = f"-- Cannot restore inheritance for {table} via ALTER"
            elif key == "pg_unlogged":
                if to_val:
                    up = f"ALTER TABLE {table} SET UNLOGGED;"
                else:
                    up = f"ALTER TABLE {table} SET LOGGED;"
                if from_val:
                    rb = f"ALTER TABLE {table} SET UNLOGGED;"
                else:
                    rb = f"ALTER TABLE {table} SET LOGGED;"
            elif key == "pg_partition":
                up = f"-- Partition strategy changed for {table}; requires table rebuild"
                rb = f"-- Cannot revert partition change for {table}; requires table rebuild"
            else:
                up = f"-- Unsupported pg_table key {key} for {table}"
                rb = up
            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_TABLE_OPTIONS,
                upgrade_sql=up, rollback_sql=rb,
            ))
        elif ot in ("add_exclude_constraint", "drop_exclude_constraint"):
            name = op.upgrade_attrs["name"]
            if ot == "add_exclude_constraint":
                expr = op.upgrade_attrs.get("expression", "")
                clause = expr if str(expr).lstrip().upper().startswith("EXCLUDE") else f"EXCLUDE {expr}"
                up = f"ALTER TABLE {table} ADD CONSTRAINT {name} {clause};"
                rb = f"ALTER TABLE {table} DROP CONSTRAINT {name};"
            else:
                up = f"ALTER TABLE {table} DROP CONSTRAINT {name};"
                expr = op.upgrade_attrs.get("expression", "")
                clause = expr if str(expr).lstrip().upper().startswith("EXCLUDE") else f"EXCLUDE {expr}"
                rb = f"ALTER TABLE {table} ADD CONSTRAINT {name} {clause};"
            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_TABLE_CONSTRAINT,
                upgrade_sql=up, rollback_sql=rb,
            ))

        return stmts
