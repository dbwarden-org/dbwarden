from __future__ import annotations

import json
from typing import Any, List, Optional, Tuple

from dbwarden.engine.backends.clickhouse.cluster import emit_with_cluster
from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.snapshot import MigrationStatement, StatementOrder


_DICT_KEYS: frozenset[str] = frozenset({
    "ch_dictionary",
    "ch_dict_layout",
    "ch_dict_source",
    "ch_dict_lifetime",
    "ch_dict_primary_key",
})


class ChDictionaryHandler(ObjectHandler):
    """ClickHouse dictionary lifecycle: extract, diff, and emit DDL."""

    object_type: str = "ch_dictionary"
    op_types: tuple[str, ...] = ("alter_ch_dict",)
    run_phase: RunPhase = RunPhase.DIFF
    statement_order: StatementOrder = StatementOrder.ALTER_TABLE_OPTIONS

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for tname, tdata in snapshot.get("tables", {}).items():
            ch_opts = tdata.get("ch_options") or {}
            ch_d = {k: ch_opts.get(k) for k in _DICT_KEYS if k in ch_opts}
            if ch_d.get("ch_dictionary"):
                result[tname] = ch_d
        return result

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table in model_tables:
            ch_opts = table.clickhouse_options or {}
            ch_d = {k: ch_opts.get(k) for k in _DICT_KEYS if k in ch_opts}
            if ch_d.get("ch_dictionary"):
                result[table.name] = ch_d
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
            snap_d = snap_spec.get(tname, {})
            model_d = model_spec.get(tname, {})

            snap_enabled = snap_d.get("ch_dictionary", False)
            model_enabled = model_d.get("ch_dictionary", False)

            if snap_enabled and not model_enabled:
                upgrade_ops.append(Op(
                    object_type="alter_ch_dict",
                    upgrade_attrs={"name": tname, "action": "drop"},
                    rollback_attrs={"name": tname, "action": "create", "options": dict(snap_d)},
                ))
                rollback_ops.append(Op(
                    object_type="alter_ch_dict",
                    upgrade_attrs={"name": tname, "action": "create", "options": dict(snap_d)},
                    rollback_attrs={"name": tname, "action": "drop"},
                ))
                continue

            if model_enabled and not snap_enabled:
                upgrade_ops.append(Op(
                    object_type="alter_ch_dict",
                    upgrade_attrs={"name": tname, "action": "create", "options": dict(model_d)},
                    rollback_attrs={"name": tname, "action": "drop"},
                ))
                rollback_ops.append(Op(
                    object_type="alter_ch_dict",
                    upgrade_attrs={"name": tname, "action": "drop"},
                    rollback_attrs={"name": tname, "action": "create", "options": dict(model_d)},
                ))
                continue

            changes: dict[str, dict[str, Any]] = {}
            for k in _DICT_KEYS:
                if k == "ch_dictionary":
                    continue
                snap_val = snap_d.get(k)
                model_val = model_d.get(k)
                if json.dumps(snap_val, sort_keys=True, default=str) != json.dumps(model_val, sort_keys=True, default=str):
                    changes[k] = {"from": snap_val, "to": model_val}

            if changes:
                upgrade_ops.append(Op(
                    object_type="alter_ch_dict",
                    upgrade_attrs={
                        "name": tname,
                        "action": "alter",
                        "changes": changes,
                    },
                    rollback_attrs={
                        "name": tname,
                        "action": "alter",
                        "changes": {k: {"from": v["to"], "to": v["from"]} for k, v in changes.items()},
                    },
                ))
                rollback_ops.append(Op(
                    object_type="alter_ch_dict",
                    upgrade_attrs={
                        "name": tname,
                        "action": "alter",
                        "changes": {k: {"from": v["to"], "to": v["from"]} for k, v in changes.items()},
                    },
                    rollback_attrs={
                        "name": tname,
                        "action": "alter",
                        "changes": changes,
                    },
                ))

        return upgrade_ops, rollback_ops

    @emit_with_cluster
    def emit(
        self, op: Op, db_name: Optional[str] = None,
        **kwargs: Any,
    ) -> List[MigrationStatement]:
        from dbwarden.engine.backends.clickhouse.cluster import ClusterableStatement

        name = op.upgrade_attrs["name"]
        action = op.upgrade_attrs.get("action", "alter")
        stmts: list[MigrationStatement] = []

        if action == "drop":
            stmt = ClusterableStatement(
                prefix=f"DROP DICTIONARY IF EXISTS {name}",
                suffix="",
            )
            rb_opts = (op.rollback_attrs or {}).get("options", {})
            rb_parts = [_render_create_dict_sql(name, rb_opts)] if rb_opts else ["-- no rollback"]
            rb_sql = "\n".join(rb_parts)
            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_TABLE_OPTIONS,
                upgrade_sql=stmt.render(self._cluster_ctx),
                rollback_sql=rb_sql,
            ))
        elif action == "create":
            opts = op.upgrade_attrs.get("options", {})
            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_TABLE_OPTIONS,
                upgrade_sql=_render_create_dict_sql(name, opts, self._cluster_ctx),
                rollback_sql=f"DROP DICTIONARY IF EXISTS {name}",
            ))
        elif action == "alter":
            changes = op.upgrade_attrs.get("changes", {})
            up_parts: list[str] = []
            rb_parts: list[str] = []
            for key, change in changes.items():
                to_val = change.get("to")
                from_val = change.get("from")
                if key == "ch_dict_layout" and to_val:
                    up_parts.append(ClusterableStatement(
                        prefix=f"ALTER DICTIONARY {name}",
                        suffix=f"MODIFY LAYOUT({to_val})",
                    ).render(self._cluster_ctx))
                    if from_val:
                        rb_parts.append(ClusterableStatement(
                            prefix=f"ALTER DICTIONARY {name}",
                            suffix=f"MODIFY LAYOUT({from_val})",
                        ).render(self._cluster_ctx))
                elif key == "ch_dict_source" and to_val:
                    up_parts.append(ClusterableStatement(
                        prefix=f"ALTER DICTIONARY {name}",
                        suffix=f"MODIFY SOURCE({to_val})",
                    ).render(self._cluster_ctx))
                    if from_val:
                        rb_parts.append(ClusterableStatement(
                            prefix=f"ALTER DICTIONARY {name}",
                            suffix=f"MODIFY SOURCE({from_val})",
                        ).render(self._cluster_ctx))
                elif key == "ch_dict_lifetime" and to_val is not None:
                    up_parts.append(ClusterableStatement(
                        prefix=f"ALTER DICTIONARY {name}",
                        suffix=f"MODIFY LIFETIME({to_val})",
                    ).render(self._cluster_ctx))
                    if from_val is not None:
                        rb_parts.append(ClusterableStatement(
                            prefix=f"ALTER DICTIONARY {name}",
                            suffix=f"MODIFY LIFETIME({from_val})",
                        ).render(self._cluster_ctx))
                elif key == "ch_dict_primary_key" and to_val:
                    up_key = str(to_val).strip()
                    if not (up_key.startswith("(") and up_key.endswith(")")):
                        up_key = f"({up_key})"
                    up_parts.append(f"ALTER DICTIONARY {name} MODIFY PRIMARY KEY {up_key}")
                    if from_val:
                        rb_key = str(from_val).strip()
                        if not (rb_key.startswith("(") and rb_key.endswith(")")):
                            rb_key = f"({rb_key})"
                        rb_parts.append(f"ALTER DICTIONARY {name} MODIFY PRIMARY KEY {rb_key}")
                else:
                    up_parts.append(f"-- {key} changed for dict {name}; manual reconcile")
                    rb_parts.append(f"-- {key} rollback for dict {name}; manual reconcile")
            stmts.append(MigrationStatement(
                order=StatementOrder.ALTER_TABLE_OPTIONS,
                upgrade_sql="\n".join(up_parts) if up_parts else "-- no-op",
                rollback_sql="\n".join(rb_parts) if rb_parts else "-- no-op",
            ))

        return stmts


def _render_create_dict_sql(
    name: str, opts: dict[str, Any],
    ctx: Any = None,
) -> str:
    from dbwarden.engine.backends.clickhouse.cluster import ClusterableStatement

    parts = [f"CREATE DICTIONARY IF NOT EXISTS {name}"]
    pk = opts.get("ch_dict_primary_key")
    layout = opts.get("ch_dict_layout")
    source = opts.get("ch_dict_source")
    lifetime = opts.get("ch_dict_lifetime")
    if pk:
        parts.append(f"PRIMARY KEY {pk}")
    if source:
        parts.append(f"SOURCE({source})")
    if lifetime is not None:
        parts.append(f"LIFETIME({lifetime})")
    if layout:
        parts.append(f"LAYOUT({layout})")
    if ctx is not None:
        cs = ClusterableStatement(prefix=parts[0], suffix=" ".join(parts[1:]))
        return cs.render(ctx)
    return "\n".join(parts)
