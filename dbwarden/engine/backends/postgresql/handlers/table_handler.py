from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.snapshot import (
    MigrationStatement,
    StatementOrder,
)


class TableHandler(ObjectHandler):
    object_type: str = "table"
    op_types: tuple[str, ...] = (
        "create_table",
        "drop_table",
        "alter_table_comment",
    )
    run_phase: RunPhase = RunPhase.DIFF
    statement_order: StatementOrder = StatementOrder.CREATE_TABLE

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for tname, tdata in snapshot.get("tables", {}).items():
            result[tname] = {
                "comment": tdata.get("comment"),
                "object_type": tdata.get("object_type", "table"),
                "schema": tdata.get("schema"),
            }
        return result

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table in model_tables:
            ch_opts = table.clickhouse_options or {}
            if "AggregatingMergeTree" in str(ch_opts.get("ch_engine", "")):
                continue
            result[table.name] = {
                "comment": getattr(table, "comment", None),
                "object_type": getattr(table, "object_type", "table"),
                "schema": getattr(table, "schema", None),
            }
        return result

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        return {}

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        if not spec:
            return {}
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
            snap_entry = snap_spec.get(tname)
            model_entry = model_spec.get(tname)

            if model_entry is not None and snap_entry is None:
                upgrade_ops.append(Op(
                    object_type="create_table",
                    upgrade_attrs={
                        "table": tname,
                        "object_type": model_entry.get("object_type", "table"),
                        "schema": model_entry.get("schema"),
                        "sql": None,
                    },
                    rollback_attrs={
                        "table": tname,
                        "object_type": model_entry.get("object_type", "table"),
                        "schema": model_entry.get("schema"),
                    },
                ))
                rollback_ops.append(Op(
                    object_type="drop_table",
                    upgrade_attrs={
                        "table": tname,
                        "object_type": model_entry.get("object_type", "table"),
                        "schema": model_entry.get("schema"),
                    },
                    rollback_attrs={
                        "table": tname,
                        "object_type": model_entry.get("object_type", "table"),
                        "schema": model_entry.get("schema"),
                    },
                ))
                continue

            if snap_entry is not None and model_entry is None:
                upgrade_ops.append(Op(
                    object_type="drop_table",
                    upgrade_attrs={
                        "table": tname,
                        "object_type": snap_entry.get("object_type", "table"),
                        "schema": snap_entry.get("schema"),
                    },
                    rollback_attrs={
                        "table": tname,
                        "object_type": snap_entry.get("object_type", "table"),
                        "schema": snap_entry.get("schema"),
                    },
                ))
                rollback_ops.append(Op(
                    object_type="create_table",
                    upgrade_attrs={
                        "table": tname,
                        "object_type": snap_entry.get("object_type", "table"),
                        "schema": snap_entry.get("schema"),
                        "sql": None,
                    },
                    rollback_attrs={
                        "table": tname,
                        "object_type": snap_entry.get("object_type", "table"),
                        "schema": snap_entry.get("schema"),
                    },
                ))
                continue

            snap_comment = snap_entry.get("comment")
            model_comment = model_entry.get("comment")
            if snap_comment != model_comment:
                upgrade_ops.append(Op(
                    object_type="alter_table_comment",
                    upgrade_attrs={
                        "table": tname,
                        "comment": model_comment,
                        "previous_comment": snap_comment,
                    },
                    rollback_attrs={
                        "table": tname,
                        "comment": snap_comment,
                    },
                ))
                rollback_ops.append(Op(
                    object_type="alter_table_comment",
                    upgrade_attrs={
                        "table": tname,
                        "comment": snap_comment,
                        "previous_comment": model_comment,
                    },
                    rollback_attrs={
                        "table": tname,
                        "comment": model_comment,
                    },
                ))

        return upgrade_ops, rollback_ops

    def emit(
        self, op: Op, db_name: Optional[str] = None
    , **kwargs: Any) -> List[MigrationStatement]:
        from dbwarden.engine.model_discovery import (
            ModelTable,
            generate_drop_object_sql,
        )
        from dbwarden.engine.offline import reconstruct_model_table
        from dbwarden.engine.snapshot import (
            _build_create_table_sequence,
            _find_model_table,
            _get_backend,
        )

        stmts: list[MigrationStatement] = []
        backend = _get_backend(db_name)

        if op.object_type == "create_table":
            state_table = op.upgrade_attrs.get("state_table")
            table = reconstruct_model_table(state_table) if state_table else _find_model_table(op.upgrade_attrs["table"], db_name=db_name)
            if table:
                table_statements = _build_create_table_sequence(table, db_name)
                stmts.extend(table_statements)

        elif op.object_type == "drop_table":
            state_table = op.upgrade_attrs.get("state_table")
            drop_table = reconstruct_model_table(state_table) if state_table else ModelTable(
                name=op.upgrade_attrs["table"],
                columns=[],
                object_type=op.upgrade_attrs.get("object_type", "table"),
            )
            rollback_sql: str
            if state_table:
                if drop_table:
                    rollback_sql = "\n\n".join(
                        stmt.upgrade_sql
                        for stmt in _build_create_table_sequence(drop_table, db_name)
                    )
                else:
                    rollback_sql = f"-- Cannot rebuild {op.upgrade_attrs['table']} from state"
            else:
                rollback_sql = (
                    f"CREATE TABLE {op.upgrade_attrs['table']} "
                    "(/* see .dbwarden/schemas/ for DDL */)"
                )
            drop_sql = generate_drop_object_sql(drop_table)
            stmts.append(MigrationStatement(
                order=StatementOrder.DROP_TABLE,
                upgrade_sql=drop_sql,
                rollback_sql=rollback_sql,
            ))

        elif op.object_type == "alter_table_comment":
            comment = op.upgrade_attrs.get("comment") or ""
            prev = op.upgrade_attrs.get("previous_comment") or ""
            raw_comment = op.upgrade_attrs.get("comment")
            raw_prev = op.upgrade_attrs.get("previous_comment")
            table = op.upgrade_attrs["table"]
            if backend == "sqlite":
                c = comment.replace(chr(39), chr(39)+chr(39))
                up = f"-- COMMENT ON TABLE {table} IS '{c}';" if comment else f"-- COMMENT ON TABLE {table} IS NULL;"
                rb = f"-- COMMENT ON TABLE {table} IS '{prev}';" if prev else f"-- COMMENT ON TABLE {table} IS NULL;"
            elif backend in ("mysql", "mariadb"):
                c = (raw_comment or "").replace(chr(39), chr(39)+chr(39))
                p = (raw_prev or "").replace(chr(39), chr(39)+chr(39))
                up = f"ALTER TABLE {table} COMMENT = '{c}';"
                rb = f"ALTER TABLE {table} COMMENT = '{p}';"
            else:
                up = f"COMMENT ON TABLE {table} IS '{comment.replace(chr(39), chr(39)+chr(39))}';" if comment else f"COMMENT ON TABLE {table} IS NULL;"
                rb = f"COMMENT ON TABLE {table} IS '{prev.replace(chr(39), chr(39)+chr(39))}';" if prev else f"COMMENT ON TABLE {table} IS NULL;"
            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_TABLE_COMMENT,
                upgrade_sql=up, rollback_sql=rb,
            ))

        return stmts
