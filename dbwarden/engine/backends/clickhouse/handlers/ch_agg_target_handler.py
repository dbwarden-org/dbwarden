from __future__ import annotations

from typing import Any, List, Optional, Tuple

from dbwarden.engine.backends.clickhouse.cluster import emit_with_cluster
from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.snapshot import MigrationStatement, StatementOrder


class ChAggTargetHandler(ObjectHandler):
    """ClickHouse aggregating-view target table lifecycle.

    When ``aggregating_view()`` declares a target table (the "agg" half of the
    triad), this handler creates or drops that target as a standalone DDL object
    managed inside the migration stream.
    """

    object_type: str = "ch_agg_target"
    op_types: tuple[str, ...] = ("create_ch_agg_target", "drop_ch_agg_target")
    run_phase: RunPhase = RunPhase.DIFF
    statement_order: StatementOrder = StatementOrder.CREATE_TABLE

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for tname, tdata in snapshot.get("tables", {}).items():
            ch_opts = tdata.get("ch_options") or {}
            obj_type = ch_opts.get("ch_object_type", "table")
            engine = ch_opts.get("ch_engine", "")
            if obj_type == "table" and "AggregatingMergeTree" in str(engine):
                result[tname] = {"exists": True}
        return result

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table in model_tables:
            ch_opts = table.clickhouse_options or {}
            engine = ch_opts.get("ch_engine", "")
            if "AggregatingMergeTree" in str(engine):
                result[table.name] = {"exists": True, "options": dict(ch_opts)}
        return result

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        return {}

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
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
            in_snap = tname in snap_spec
            in_model = tname in model_spec

            if in_model and not in_snap:
                opts = model_spec[tname].get("options", {})
                upgrade_ops.append(Op(
                    object_type="create_ch_agg_target",
                    upgrade_attrs={"name": tname, "options": dict(opts)},
                    rollback_attrs={"name": tname},
                ))
                rollback_ops.append(Op(
                    object_type="drop_ch_agg_target",
                    upgrade_attrs={"name": tname},
                    rollback_attrs={"name": tname, "options": dict(opts)},
                ))

            if in_snap and not in_model:
                upgrade_ops.append(Op(
                    object_type="drop_ch_agg_target",
                    upgrade_attrs={"name": tname},
                    rollback_attrs={"name": tname, "options": snap_spec[tname]},
                ))
                rollback_ops.append(Op(
                    object_type="create_ch_agg_target",
                    upgrade_attrs={"name": tname, "options": dict(snap_spec[tname])},
                    rollback_attrs={"name": tname},
                ))

        return upgrade_ops, rollback_ops

    @emit_with_cluster
    def emit(
        self, op: Op, db_name: Optional[str] = None,
        **kwargs: Any,
    ) -> List[MigrationStatement]:
        from dbwarden.engine.backends.clickhouse.cluster import ClusterableStatement

        name = op.upgrade_attrs["name"]

        if op.object_type == "create_ch_agg_target":
            opts = op.upgrade_attrs.get("options", {})
            up_stmt = ClusterableStatement(
                prefix=f"CREATE TABLE IF NOT EXISTS {name}",
                suffix="\n" + _render_agg_target_tail(name, opts),
            )
            up_parts = [up_stmt.render(self._cluster_ctx)]
            rb_parts = [ClusterableStatement(
                prefix=f"DROP TABLE IF EXISTS {name}",
                suffix="",
            ).render(self._cluster_ctx)]
        elif op.object_type == "drop_ch_agg_target":
            up_parts = [ClusterableStatement(
                prefix=f"DROP TABLE IF EXISTS {name}",
                suffix="",
            ).render(self._cluster_ctx)]
            opts = op.upgrade_attrs.get("options", {})
            rb_stmt = ClusterableStatement(
                prefix=f"CREATE TABLE IF NOT EXISTS {name}",
                suffix="\n" + _render_agg_target_tail(name, opts),
            )
            rb_parts = [rb_stmt.render(self._cluster_ctx)]
        else:
            up_parts = ["-- no-op"]
            rb_parts = ["-- no-op"]

        return [
            MigrationStatement(
                order=self.statement_order,
                upgrade_sql="\n".join(up_parts),
                rollback_sql="\n".join(rb_parts),
            )
        ]


def _render_agg_target_tail(name: str, opts: dict[str, Any]) -> str:
    """Render everything after the table name for an AggregatingMergeTree target.

    Returns the suffix part (e.g. ``(col1 Int64) ENGINE = AggregatingMergeTree() ...
    ``) for use as a ``ClusterableStatement`` suffix.
    """
    from dbwarden.engine.backends.clickhouse.render import (
        _format_clickhouse_expression,
    )

    order_by = opts.get("ch_order_by")
    partition_by = opts.get("ch_partition_by")
    ttl = opts.get("ch_ttl")
    settings = opts.get("ch_settings")

    parts = ["("]
    columns = opts.get("columns", [])
    col_lines = []
    for col in columns:
        col_lines.append(f"    {col}")
    if not col_lines:
        cols_from_opts = opts.get("ch_column_defs")
        if cols_from_opts:
            col_lines = [f"    {c}" for c in cols_from_opts]
    if col_lines:
        parts.append(",\n".join(col_lines))
    else:
        parts.append("    -- columns defined implicitly via AggregateFunction types")
    parts.append(")")
    if order_by is not None:
        parts.append(f"ENGINE = AggregatingMergeTree() ORDER BY {_format_clickhouse_expression(order_by)}")
    else:
        parts.append("ENGINE = AggregatingMergeTree()")
    if partition_by is not None:
        parts.append(f"PARTITION BY {partition_by}")
    if ttl is not None:
        ttl_str = ", ".join(ttl) if isinstance(ttl, list) else str(ttl)
        parts.append(f"TTL {ttl_str}")
    if settings is not None:
        settings_str = ", ".join(f"{k}={v}" for k, v in settings.items())
        parts.append(f"SETTINGS {settings_str}")
    return "\n".join(parts)
