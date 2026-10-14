from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.snapshot import (
    MigrationStatement,
    StatementOrder,
    _normalize_mysql_table_value,
)


class MyTableHandler(ObjectHandler):
    object_type: str = "my_table"
    op_types: tuple[str, ...] = ("alter_my_table",)
    run_phase: RunPhase = RunPhase.DIFF
    statement_order: StatementOrder = StatementOrder.ALTER_TABLE_OPTIONS

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for tname, tdata in snapshot.get("tables", {}).items():
            entry: dict[str, Any] = {}
            my_table = tdata.get("my_table") or {}
            if my_table:
                entry.update(my_table)
            backend_spec = tdata.get("backend_table_spec") or {}
            if not my_table and backend_spec.get("backend") == "mysql":
                entry = {k: v for k, v in backend_spec.items() if k != "backend"}
            if entry:
                result[tname] = entry
        return result

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table in model_tables:
            my_table = table.my_table or {}
            if my_table:
                result[table.name] = dict(my_table)
        return result

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        return {}

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        if not spec:
            return {}
        result: dict[str, dict[str, Any]] = {}
        for tname, entry in spec.items():
            result[tname] = {
                k: _normalize_mysql_table_value(k, v)
                for k, v in entry.items()
            }
        return result

    def diff(
        self,
        snap_spec: dict[str, Any],
        model_spec: dict[str, Any],
    ) -> Tuple[List[Op], List[Op]]:
        upgrade_ops: list[Op] = []
        rollback_ops: list[Op] = []

        all_tables = set(snap_spec.keys()) | set(model_spec.keys())
        for tname in sorted(all_tables):
            snap_entry = snap_spec.get(tname, {})
            model_entry = model_spec.get(tname, {})
            all_keys = set(snap_entry.keys()) | set(model_entry.keys())
            for key in sorted(all_keys):
                snap_val = snap_entry.get(key)
                model_val = model_entry.get(key)
                # AUTO_INCREMENT is a runtime counter, not schema: it advances
                # with every insert and would otherwise produce a spurious
                # ALTER on each diff (and never converge). Never diff it.
                if key == "my_auto_increment":
                    continue
                if snap_val != model_val:
                    upgrade_ops.append(Op(
                        object_type="alter_my_table",
                        upgrade_attrs={
                            "table": tname, "key": key,
                            "to_value": model_val, "from_value": snap_val,
                        },
                        rollback_attrs={
                            "table": tname, "key": key,
                            "to_value": snap_val, "from_value": model_val,
                        },
                    ))
                    rollback_ops.append(Op(
                        object_type="alter_my_table",
                        upgrade_attrs={
                            "table": tname, "key": key,
                            "to_value": snap_val, "from_value": model_val,
                        },
                        rollback_attrs={
                            "table": tname, "key": key,
                            "to_value": model_val, "from_value": snap_val,
                        },
                    ))

        return upgrade_ops, rollback_ops

    def emit(
        self, op: Op, db_name: Optional[str] = None
    , **kwargs: Any) -> List[MigrationStatement]:
        from dbwarden.engine.snapshot import _get_backend

        stmts: list[MigrationStatement] = []
        backend = _get_backend(db_name)

        if backend not in ("mysql", "mariadb"):
            return stmts

        table = op.upgrade_attrs["table"]
        key = op.upgrade_attrs["key"]
        to_val = op.upgrade_attrs.get("to_value")
        from_val = op.upgrade_attrs.get("from_value")

        up: str
        rb: str
        if key == "my_engine":
            up = f"ALTER TABLE {table} ENGINE={to_val};" if to_val else f"-- Cannot unset engine for {table}"
            rb = f"ALTER TABLE {table} ENGINE={from_val};" if from_val else f"-- Cannot restore unset engine for {table}"
        elif key == "my_charset":
            up = f"ALTER TABLE {table} DEFAULT CHARACTER SET {to_val};" if to_val else f"-- Cannot unset character set for {table}"
            rb = f"ALTER TABLE {table} DEFAULT CHARACTER SET {from_val};" if from_val else f"-- Cannot restore unset character set for {table}"
        elif key == "my_collate":
            up = f"ALTER TABLE {table} COLLATE={to_val};" if to_val else f"-- Cannot unset collation for {table}"
            rb = f"ALTER TABLE {table} COLLATE={from_val};" if from_val else f"-- Cannot restore unset collation for {table}"
        elif key == "my_row_format":
            up = f"ALTER TABLE {table} ROW_FORMAT={to_val};" if to_val else f"-- Cannot unset row format for {table}"
            rb = f"ALTER TABLE {table} ROW_FORMAT={from_val};" if from_val else f"-- Cannot restore unset row format for {table}"
        elif key == "my_auto_increment":
            up = f"ALTER TABLE {table} AUTO_INCREMENT={to_val};" if to_val is not None else f"-- Cannot unset auto_increment for {table}"
            rb = f"ALTER TABLE {table} AUTO_INCREMENT={from_val};" if from_val is not None else f"-- Cannot restore unset auto_increment for {table}"
        else:
            up = f"-- Unsupported MySQL table option change {key} on {table}"
            rb = up

        stmts.append(MigrationStatement(
            order=StatementOrder.ALTER_TABLE_OPTIONS,
            upgrade_sql=up,
            rollback_sql=rb,
        ))
        return stmts
