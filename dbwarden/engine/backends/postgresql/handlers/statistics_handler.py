from __future__ import annotations

from typing import Any, List, Optional, Tuple

from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.snapshot import MigrationStatement, StatementOrder


class StatisticsHandler(ObjectHandler):
    object_type: str = "statistics"
    op_types: tuple[str, ...] = (
        "alter_column_statistics",
    )
    run_phase: RunPhase = RunPhase.DIFF
    statement_order: StatementOrder = StatementOrder.ALTER_TABLE_OPTIONS

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for tname, tdata in snapshot.get("tables", {}).items():
            cols = tdata.get("columns", {})
            for cname, cinfo in cols.items():
                pg_col = cinfo.get("pg_column", {})
                if isinstance(pg_col, dict) and pg_col.get("statistics") is not None:
                    if tname not in result:
                        result[tname] = {}
                    result[tname][cname] = pg_col["statistics"]
        return result

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table in model_tables:
            pg_table = table.pg_table or {}
            for col in table.columns:
                pg_col = col.pg_meta.get("pg_column", {})
                if isinstance(pg_col, dict) and pg_col.get("statistics") is not None:
                    if table.name not in result:
                        result[table.name] = {}
                    result[table.name][col.name] = pg_col["statistics"]
        return result

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        return {}

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        if not spec:
            return {}
        result: dict[str, dict[str, int]] = {}
        for tname, cols in spec.items():
            if isinstance(cols, dict):
                for cname, val in cols.items():
                    if tname not in result:
                        result[tname] = {}
                    try:
                        result[tname][cname] = int(val)
                    except (ValueError, TypeError):
                        pass
        return result

    def diff(
        self,
        snap_spec: dict[str, Any],
        model_spec: dict[str, Any],
    ) -> Tuple[List[Op], List[Op]]:
        upgrade_ops: list[Op] = []
        rollback_ops: list[Op] = []

        snap = snap_spec or {}
        model = model_spec or {}

        all_tables = set(snap.keys()) | set(model.keys())
        for tname in sorted(all_tables):
            snap_cols = snap.get(tname, {})
            model_cols = model.get(tname, {})
            all_cols = set(snap_cols.keys()) | set(model_cols.keys())
            for cname in sorted(all_cols):
                snap_val = snap_cols.get(cname)
                model_val = model_cols.get(cname)
                if snap_val != model_val:
                    upgrade_attrs: dict[str, Any] = {
                        "table": tname,
                        "column": cname,
                        "statistics": model_val,
                    }
                    rollback_attrs: dict[str, Any] = {
                        "table": tname,
                        "column": cname,
                        "statistics": snap_val,
                    }
                    upgrade_ops.append(Op(
                        object_type="alter_column_statistics",
                        upgrade_attrs=upgrade_attrs,
                        rollback_attrs=rollback_attrs,
                    ))
                    rollback_ops.append(Op(
                        object_type="alter_column_statistics",
                        upgrade_attrs=rollback_attrs,
                        rollback_attrs=upgrade_attrs,
                    ))

        return upgrade_ops, rollback_ops

    def emit(
        self, op: Op, db_name: Optional[str] = None
    , **kwargs: Any) -> List[MigrationStatement]:
        from dbwarden.engine.model_discovery import _qualified_name
        from dbwarden.engine.backends.postgresql.render import _quote_pg

        stmts: list[MigrationStatement] = []
        table = op.upgrade_attrs["table"]
        column = op.upgrade_attrs["column"]
        statistics = op.upgrade_attrs.get("statistics")
        schema = op.upgrade_attrs.get("schema")
        qtable = _qualified_name(_quote_pg(table), _quote_pg(schema) if schema else None)

        def _validated_statistics(value: Any) -> int:
            if isinstance(value, bool) or not isinstance(value, int) or value < -1:
                raise ValueError("PostgreSQL statistics target must be an integer >= -1")
            return value

        if statistics is not None:
            up = f"ALTER TABLE {qtable} ALTER COLUMN {column} SET STATISTICS {_validated_statistics(statistics)};"
        else:
            up = f"ALTER TABLE {qtable} ALTER COLUMN {column} SET STATISTICS -1;"
        rollback_statistics = op.rollback_attrs.get("statistics")
        if rollback_statistics is not None:
            rb = f"ALTER TABLE {qtable} ALTER COLUMN {column} SET STATISTICS {_validated_statistics(rollback_statistics)};"
        else:
            rb = f"ALTER TABLE {qtable} ALTER COLUMN {column} SET STATISTICS -1;"

        stmts.append(MigrationStatement(
            order=StatementOrder.ALTER_TABLE_OPTIONS,
            upgrade_sql=up,
            rollback_sql=rb,
        ))

        return stmts
