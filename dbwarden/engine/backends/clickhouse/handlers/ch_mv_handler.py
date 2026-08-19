from __future__ import annotations

from typing import Any, List, Optional, Tuple

from dbwarden.engine.backends.clickhouse.cluster import emit_with_cluster
from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.snapshot import (
    MigrationStatement,
    StatementOrder,
)


class ChMaterializedViewHandler(ObjectHandler):
    """Handler for materialized-view-specific operations.

    This handler manages MV-level changes that are distinct from the table-level
    ``alter_ch_options`` handled by ``ChTableHandler``:
      - ``MODIFY QUERY``, in-place SELECT change (CH 24.8+)
      - ``MODIFY REFRESH``, refresh schedule change (refreshable MVs)

    Table-level MV properties (engine, order_by, settings on implicit .inner.
    storage, TTL) continue to flow through ``ChTableHandler`` as
    ``alter_ch_options`` ops.  The two handlers operate on different op types
    and do not conflict.
    """

    object_type: str = "ch_materialized_view"
    op_types: tuple[str, ...] = ("modify_mv_query", "modify_mv_refresh")
    run_phase: RunPhase = RunPhase.DIFF
    statement_order: StatementOrder = StatementOrder.ALTER_VIEW

    # ── extract ──────────────────────────────────────────────────────────────

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for tname, tdata in snapshot.get("tables", {}).items():
            ch_opts = tdata.get("ch_options") or {}
            if ch_opts.get("ch_object_type") != "materialized_view":
                continue
            result[tname] = {
                "ch_select_statement": ch_opts.get("ch_select_statement"),
                "ch_refresh": ch_opts.get("ch_refresh"),
                "ch_to_table": ch_opts.get("ch_to_table"),
            }
        return result

    # ── model spec ────────────────────────────────────────────────────────────

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table in model_tables:
            if not table.clickhouse_options:
                continue
            opts = table.clickhouse_options
            if opts.get("ch_object_type") != "materialized_view":
                continue
            result[table.name] = {
                "ch_select_statement": opts.get("ch_select_statement"),
                "ch_refresh": opts.get("ch_refresh"),
                "ch_to_table": opts.get("ch_to_table"),
            }
        return result

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        return {}

    # ── canonicalize ──────────────────────────────────────────────────────────

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        import re

        for entry in spec.values():
            select = entry.get("ch_select_statement")
            if isinstance(select, str):
                entry["ch_select_statement"] = re.sub(r"\s+", " ", select).strip()
        return spec

    # ── diff ──────────────────────────────────────────────────────────────────

    def diff(
        self,
        snap_spec: dict[str, Any],
        model_spec: dict[str, Any],
    ) -> Tuple[List[Op], List[Op]]:
        upgrade_ops: list[Op] = []
        rollback_ops: list[Op] = []

        all_mvs = set(snap_spec.keys()) | set(model_spec.keys())
        for mv_name in sorted(all_mvs):
            snap_entry = snap_spec.get(mv_name, {})
            model_entry = model_spec.get(mv_name, {})

            snap_select = snap_entry.get("ch_select_statement")
            model_select = model_entry.get("ch_select_statement")
            snap_refresh = snap_entry.get("ch_refresh")
            model_refresh = model_entry.get("ch_refresh")
            snap_to = snap_entry.get("ch_to_table")
            model_to = model_entry.get("ch_to_table")

            # SELECT change -> MODIFY QUERY (in-place, non-destructive)
            if snap_select != model_select and snap_select is not None and model_select is not None:
                upgrade_ops.append(Op(
                    object_type="modify_mv_query",
                    upgrade_attrs={
                        "mv_name": mv_name,
                        "to_select": model_select,
                        "from_select": snap_select,
                    },
                    rollback_attrs={
                        "mv_name": mv_name,
                        "to_select": snap_select,
                        "from_select": model_select,
                    },
                ))
                rollback_ops.append(Op(
                    object_type="modify_mv_query",
                    upgrade_attrs={
                        "mv_name": mv_name,
                        "to_select": snap_select,
                        "from_select": model_select,
                    },
                    rollback_attrs={
                        "mv_name": mv_name,
                        "to_select": model_select,
                        "from_select": snap_select,
                    },
                ))

            # Refresh schedule change -> MODIFY REFRESH
            # Handles None→value, value→None, and value→value transitions.
            if snap_refresh != model_refresh:
                to_refresh = model_refresh
                from_refresh = snap_refresh
                upgrade_ops.append(Op(
                    object_type="modify_mv_refresh",
                    upgrade_attrs={
                        "mv_name": mv_name,
                        "to_refresh": to_refresh,
                        "from_refresh": from_refresh,
                    },
                    rollback_attrs={
                        "mv_name": mv_name,
                        "to_refresh": from_refresh,
                        "from_refresh": to_refresh,
                    },
                ))
                rollback_ops.append(Op(
                    object_type="modify_mv_refresh",
                    upgrade_attrs={
                        "mv_name": mv_name,
                        "to_refresh": from_refresh,
                        "from_refresh": to_refresh,
                    },
                    rollback_attrs={
                        "mv_name": mv_name,
                        "to_refresh": to_refresh,
                        "from_refresh": from_refresh,
                    },
                ))

        return upgrade_ops, rollback_ops

    # ── emit ──────────────────────────────────────────────────────────────────

    @emit_with_cluster
    def emit(
        self, op: Op, db_name: Optional[str] = None,
        **kwargs: Any,
    ) -> List[MigrationStatement]:
        from dbwarden.engine.backends.clickhouse.cluster import ClusterableStatement

        stmts: list[MigrationStatement] = []

        if op.object_type == "modify_mv_query":
            mv_name = op.upgrade_attrs["mv_name"]
            to_select = op.upgrade_attrs["to_select"]
            from_select = op.upgrade_attrs["from_select"]

            upgrade_stmt = ClusterableStatement(
                prefix=f"ALTER TABLE {mv_name}",
                suffix=f"MODIFY QUERY {to_select}",
            )
            rollback_stmt = ClusterableStatement(
                prefix=f"ALTER TABLE {mv_name}",
                suffix=f"MODIFY QUERY {from_select}",
            )

            stmts.append(upgrade_stmt.to_migration(
                StatementOrder.ALTER_VIEW, self._cluster_ctx, rollback=rollback_stmt,
            ))

        elif op.object_type == "modify_mv_refresh":
            mv_name = op.upgrade_attrs["mv_name"]
            to_refresh = op.upgrade_attrs["to_refresh"]
            from_refresh = op.upgrade_attrs["from_refresh"]

            if to_refresh is not None:
                upgrade_stmt = ClusterableStatement(
                    prefix=f"ALTER TABLE {mv_name}",
                    suffix=f"MODIFY REFRESH {to_refresh}",
                )
                upgrade_sql = upgrade_stmt.render(self._cluster_ctx)
            else:
                upgrade_sql = f"-- REFRESH removed for MV {mv_name}; manual ALTER required"

            if from_refresh is not None:
                rollback_stmt = ClusterableStatement(
                    prefix=f"ALTER TABLE {mv_name}",
                    suffix=f"MODIFY REFRESH {from_refresh}",
                )
                rollback_sql = rollback_stmt.render(self._cluster_ctx)
            else:
                rollback_sql = f"-- REFRESH was not set for MV {mv_name}; nothing to roll back"

            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_VIEW,
                upgrade_sql=upgrade_sql,
                rollback_sql=rollback_sql,
            ))

        return stmts
