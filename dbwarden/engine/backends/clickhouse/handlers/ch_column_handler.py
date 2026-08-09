from __future__ import annotations

from typing import Any, List, Optional, Tuple

from dbwarden.engine.backends.clickhouse.cluster import emit_with_cluster
from dbwarden.engine.backends.clickhouse.render import _apply_ch_nullable_low_cardinality
from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.snapshot import MigrationStatement, StatementOrder


class ChColumnHandler(ObjectHandler):
    """ClickHouse column meta changes: codec, TTL, default, materialized, alias,
    low_cardinality, nullable, and type changes.

    Previously handled inside the PostgreSQL ColumnHandler, which violated the
    boundary rule.  Extracted to this handler in the corrective plan step 2.
    """

    object_type: str = "ch_column"
    op_types: tuple[str, ...] = ("alter_ch_column",)
    run_phase: RunPhase = RunPhase.DIFF
    statement_order: StatementOrder = StatementOrder.ALTER_COLUMN_TYPE

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for tname, tdata in snapshot.get("tables", {}).items():
            cols: dict[str, Any] = {}
            for cname, cdata in tdata.get("columns", {}).items():
                ch_col = cdata.get("ch_column")
                if ch_col:
                    cols[cname] = ch_col
            if cols:
                result[tname] = cols
        return result

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table in model_tables:
            cols: dict[str, Any] = {}
            for col in table.columns:
                ch_meta = getattr(col, "ch_meta", None) or {}
                cols[col.name] = ch_meta
            if cols:
                result[table.name] = cols
        return result

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        return {}

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        if not spec:
            return {}
        result: dict[str, Any] = {}
        for tname, tdata in spec.items():
            if not tdata:
                continue
            cols: dict[str, Any] = {}
            for cname, ch_meta in tdata.items():
                if not ch_meta:
                    continue
                cleaned = {k: v for k, v in ch_meta.items() if v is not None and v is not False}
                codec = cleaned.get("ch_codec")
                if isinstance(codec, str) and codec.upper().startswith("CODEC(") and codec.endswith(")"):
                    cleaned["ch_codec"] = codec[6:-1]
                if cleaned:
                    cols[cname] = cleaned
            if cols:
                result[tname] = cols
        return result

    def diff(
        self,
        snap_spec: dict[str, Any],
        model_spec: dict[str, Any],
    ) -> Tuple[List[Op], List[Op]]:
        from dbwarden.engine.snapshot import _diff_ch_column_extras

        upgrade_ops: list[Op] = []
        rollback_ops: list[Op] = []

        all_tables = set(snap_spec.keys()) | set(model_spec.keys())
        for tname in sorted(all_tables):
            snap_cols = snap_spec.get(tname, {})
            model_cols = model_spec.get(tname, {})
            all_cols = set(snap_cols.keys()) | set(model_cols.keys())
            for cname in sorted(all_cols):
                snap_ch = snap_cols.get(cname, {})
                model_ch = model_cols.get(cname, {})
                if snap_ch == model_ch:
                    continue
                temp_up: list[dict] = []
                temp_rb: list[dict] = []
                _diff_ch_column_extras(snap_ch, model_ch, tname, cname, temp_up, temp_rb)
                for d in temp_up:
                    upgrade_ops.append(Op(
                        object_type="alter_ch_column",
                        upgrade_attrs={k: v for k, v in d.items() if k != "type"},
                        rollback_attrs={},
                    ))
                for d in temp_rb:
                    rollback_ops.append(Op(
                        object_type="alter_ch_column",
                        upgrade_attrs={k: v for k, v in d.items() if k != "type"},
                        rollback_attrs={},
                    ))

        return upgrade_ops, rollback_ops

    @emit_with_cluster
    def emit(
        self, op: Op, db_name: Optional[str] = None,
        **kwargs: Any,
    ) -> List[MigrationStatement]:
        from dbwarden.engine.snapshot import _strip_ch_type_wrappers

        table = op.upgrade_attrs["table"]
        column = op.upgrade_attrs["column"]
        from_ch = op.upgrade_attrs.get("from_ch_column", {}) or {}
        to_ch = op.upgrade_attrs.get("to_ch_column", {}) or {}
        base_type = to_ch.get("ch_type") or from_ch.get("ch_type") or ""
        up_parts: list[str] = []
        rb_parts: list[str] = []

        if to_ch.get("ch_type") and to_ch.get("ch_type") != from_ch.get("ch_type"):
            up_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {to_ch['ch_type']}")
            rb_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {from_ch.get('ch_type', base_type)}")

        if to_ch.get("ch_codec") != from_ch.get("ch_codec"):
            if to_ch.get("ch_codec"):
                up_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} CODEC({to_ch['ch_codec']})")
            else:
                up_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} REMOVE CODEC")
            if from_ch.get("ch_codec"):
                rb_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} CODEC({from_ch['ch_codec']})")
            else:
                rb_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} REMOVE CODEC")

        if to_ch.get("ch_ttl") != from_ch.get("ch_ttl"):
            if to_ch.get("ch_ttl"):
                up_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} TTL {to_ch['ch_ttl']}")
            else:
                up_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} REMOVE TTL")
            if from_ch.get("ch_ttl"):
                rb_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} TTL {from_ch['ch_ttl']}")
            else:
                rb_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} REMOVE TTL")

        if to_ch.get("ch_default_expression") != from_ch.get("ch_default_expression"):
            if to_ch.get("ch_default_expression"):
                up_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} DEFAULT {to_ch['ch_default_expression']}")
            else:
                up_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} REMOVE DEFAULT")
            if from_ch.get("ch_default_expression"):
                rb_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} DEFAULT {from_ch['ch_default_expression']}")
            else:
                rb_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} REMOVE DEFAULT")

        if to_ch.get("ch_materialized") != from_ch.get("ch_materialized"):
            if to_ch.get("ch_materialized"):
                up_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} MATERIALIZED {to_ch['ch_materialized']}")
            else:
                up_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} REMOVE MATERIALIZED")
            if from_ch.get("ch_materialized"):
                rb_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} MATERIALIZED {from_ch['ch_materialized']}")
            else:
                rb_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} REMOVE MATERIALIZED")

        if to_ch.get("ch_alias") != from_ch.get("ch_alias"):
            if to_ch.get("ch_alias"):
                up_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} ALIAS {to_ch['ch_alias']}")
            else:
                up_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} REMOVE ALIAS")
            if from_ch.get("ch_alias"):
                rb_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} ALIAS {from_ch['ch_alias']}")
            else:
                rb_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} REMOVE ALIAS")

        if to_ch.get("ch_ephemeral") != from_ch.get("ch_ephemeral"):
            if to_ch.get("ch_ephemeral"):
                up_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} EPHEMERAL {to_ch['ch_ephemeral']}")
            else:
                up_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} REMOVE EPHEMERAL")
            if from_ch.get("ch_ephemeral"):
                rb_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} EPHEMERAL {from_ch['ch_ephemeral']}")
            else:
                rb_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {base_type} REMOVE EPHEMERAL")

        _ch_type_changed = to_ch.get("ch_type") and to_ch.get("ch_type") != from_ch.get("ch_type")
        if not _ch_type_changed:
            ch_lc_diff = to_ch.get("ch_low_cardinality") != from_ch.get("ch_low_cardinality")
            ch_null_diff = to_ch.get("ch_nullable") != from_ch.get("ch_nullable")
            if ch_lc_diff or ch_null_diff:
                _base = _strip_ch_type_wrappers(to_ch.get("ch_type") or from_ch.get("ch_type") or "")
                target = _apply_ch_nullable_low_cardinality(
                    _base,
                    bool(to_ch.get("ch_nullable")),
                    bool(to_ch.get("ch_low_cardinality")),
                )
                up_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {target}")
                _base_rb = _strip_ch_type_wrappers(from_ch.get("ch_type") or to_ch.get("ch_type") or "")
                rb_target = _apply_ch_nullable_low_cardinality(
                    _base_rb,
                    bool(from_ch.get("ch_nullable")),
                    bool(from_ch.get("ch_low_cardinality")),
                )
                rb_parts.append(f"ALTER TABLE {table} MODIFY COLUMN {column} {rb_target}")

        if up_parts:
            return [
                MigrationStatement(
                    order=StatementOrder.ALTER_COLUMN_TYPE,
                    upgrade_sql=";\n".join(up_parts),
                    rollback_sql=";\n".join(rb_parts),
                )
            ]
        return []
