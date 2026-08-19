from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from dbwarden.engine.backends.clickhouse.cluster import emit_with_cluster
from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.model_discovery import _format_clickhouse_expression
from dbwarden.engine.snapshot import (
    MigrationStatement,
    StatementOrder,
)
from dbwarden.engine.snapshot.ch_utils import _serialize_clickhouse_engine
from dbwarden.databases.clickhouse.engine import ChEngineSpec


# NOTE: immutable keys (ch_partition_by, ch_primary_key, ch_sample_by) are
# deliberately excluded; they are refused by check_immutable() before this
# list is iterated.  Never add them back here.
_CH_OPTION_KEYS: frozenset[str] = frozenset({
    "ch_engine",
    "ch_order_by",
    "ch_ttl",
    "ch_settings",
    "ch_object_type",
    "ch_select_statement",
    "ch_to_table",
    "ch_zookeeper_path",
    "ch_replica_name",
})

_RECREATE_REQUIRED_CH_KEYS: frozenset[str] = frozenset({
    "ch_engine",
    "ch_select_statement",
    "ch_to_table",
    "ch_zookeeper_path",
    "ch_replica_name",
    "ch_object_type",
})


class ChTableHandler(ObjectHandler):
    object_type: str = "ch_table"
    op_types: tuple[str, ...] = ("alter_ch_options", "recreate_ch_table")
    run_phase: RunPhase = RunPhase.DIFF
    statement_order: StatementOrder = StatementOrder.ALTER_TABLE_OPTIONS
    clickhouse_engine_recreate: bool = False

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        setting_defaults = snapshot.get("ch_setting_defaults") or {}
        for tname, tdata in snapshot.get("tables", {}).items():
            ch_opts = tdata.get("ch_options") or {}
            if ch_opts:
                result[tname] = {
                    "ch_options": dict(ch_opts),
                    "snapshot_table": tdata,
                    "ch_setting_defaults": dict(setting_defaults),
                }
        return result

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table in model_tables:
            if table.clickhouse_options:
                result[table.name] = {
                    "ch_options": dict(table.clickhouse_options),
                    "model_table": table,
                }
        return result

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        return {}

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        if not spec:
            return {}
        from dbwarden.engine.backends.clickhouse.canonicalize import canonicalize_primary_key

        for entry in spec.values():
            opts = dict(entry.get("ch_options") or {})
            opts.pop("ch_engine_raw", None)
            opts = {k: v for k, v in opts.items() if v is not None and v is not False and v != []}
            engine = opts.get("ch_engine")
            if isinstance(engine, ChEngineSpec):
                engine = _serialize_clickhouse_engine(engine)
                opts["ch_engine"] = engine
            if isinstance(engine, (list, tuple)) and len(engine) == 1:
                opts["ch_engine"] = engine[0]
            select = opts.get("ch_select_statement")
            if isinstance(select, str):
                opts["ch_select_statement"] = re.sub(r"\s+", " ", select).strip()
            opts = canonicalize_primary_key(opts)
            for key in ("ch_order_by", "ch_primary_key"):
                val = opts.get(key)
                if isinstance(val, str) and val:
                    opts[key] = [part.strip() for part in val.split(",") if part.strip()]

            # Strip settings that equal the server's reported defaults, so a
            # hand-written model that omits settings does not drift against a
            # snapshot that contains ClickHouse-reported defaults.
            setting_defaults = entry.get("ch_setting_defaults") or {}
            if setting_defaults and opts.get("ch_settings"):
                settings = opts["ch_settings"]
                if isinstance(settings, dict):
                    stripped = {
                        k: v for k, v in settings.items()
                        if str(setting_defaults.get(k)) != str(v)
                    }
                    if stripped:
                        opts["ch_settings"] = stripped
                    else:
                        opts.pop("ch_settings", None)

            opts = {k: v for k, v in opts.items() if v is not None and v is not False and v != []}
            entry["ch_options"] = opts
        return spec

    def diff(
        self,
        snap_spec: dict[str, Any],
        model_spec: dict[str, Any],
    ) -> Tuple[List[Op], List[Op]]:
        upgrade_ops: list[Op] = []
        rollback_ops: list[Op] = []

        def _strip_default_settings(opts: dict[str, Any], defaults: dict[str, str]) -> dict[str, Any]:
            if not defaults or not opts.get("ch_settings"):
                return opts
            settings = opts["ch_settings"]
            if not isinstance(settings, dict):
                return opts
            stripped = {
                k: v for k, v in settings.items()
                if str(defaults.get(k)) != str(v)
            }
            new_opts = dict(opts)
            if stripped:
                new_opts["ch_settings"] = stripped
            else:
                new_opts.pop("ch_settings", None)
            return new_opts

        all_tables = set(snap_spec.keys()) | set(model_spec.keys())
        for tname in sorted(all_tables):
            snap_entry = snap_spec.get(tname, {})
            model_entry = model_spec.get(tname, {})
            snap_opts = snap_entry.get("ch_options", {})
            model_opts = model_entry.get("ch_options", {})
            if not snap_opts and not model_opts:
                continue

            setting_defaults = snap_entry.get("ch_setting_defaults") or {}
            snap_opts = _strip_default_settings(snap_opts, setting_defaults)
            model_opts = _strip_default_settings(model_opts, setting_defaults)

            snapshot_table = snap_entry.get("snapshot_table")
            model_table = model_entry.get("model_table")

            # Check for immutable key changes BEFORE the alter-changes path
            from dbwarden.engine.backends.clickhouse.canonicalize import check_immutable
            check_immutable(snap_opts, model_opts, tname)

            ch_changes: dict[str, dict[str, Any]] = {}
            for key in _CH_OPTION_KEYS:
                snap_val = snap_opts.get(key)
                model_val = model_opts.get(key)
                if json.dumps(snap_val, sort_keys=True, default=str) != json.dumps(model_val, sort_keys=True, default=str):
                    if snap_val is None and model_val is None:
                        continue
                    ch_changes[key] = {"from": snap_val, "to": model_val}

            has_recreate_keys = any(k in _RECREATE_REQUIRED_CH_KEYS for k in ch_changes)
            if has_recreate_keys and self.clickhouse_engine_recreate and snapshot_table is not None and model_table is not None:
                from dbwarden.engine.offline import _table_to_state_entry
                from dbwarden.engine.snapshot.ch_utils import classify_clickhouse_recreate_rollback

                reason = ",".join(k for k in ch_changes if k in _RECREATE_REQUIRED_CH_KEYS)
                rollback_kind, rollback_reason = classify_clickhouse_recreate_rollback(
                    snap_opts.get("ch_engine"),
                    model_opts.get("ch_engine"),
                )
                from_dict = {
                    "name": tname,
                    **snapshot_table,
                    "backend_table_spec": {"backend": "clickhouse", **snap_opts},
                }
                to_dict = _table_to_state_entry(model_table)
                to_snap_dict = {
                    "name": tname,
                    **snapshot_table,
                    "backend_table_spec": {"backend": "clickhouse", **snap_opts},
                }
                upgrade_ops.append(Op(
                    object_type="recreate_ch_table",
                    upgrade_attrs={
                        "table": tname,
                        "reason": reason,
                        "from_table": from_dict,
                        "to_table": to_dict,
                        "drop_old_after_swap": False,
                        "preserve_old_suffix": "__dbw_old",
                        "failed_suffix": "__dbw_failed",
                        "rollback_kind": rollback_kind,
                        "rollback_reason": rollback_reason,
                    },
                    rollback_attrs={
                        "table": tname,
                        "reason": reason,
                        "from_table": to_dict,
                        "to_table": to_snap_dict,
                        "drop_old_after_swap": False,
                        "preserve_old_suffix": "__dbw_failed",
                        "failed_suffix": "__dbw_old",
                        "rollback_kind": rollback_kind,
                        "rollback_reason": rollback_reason,
                    },
                    irreversible=rollback_kind == "irreversible",
                ))
                rollback_ops.append(Op(
                    object_type="recreate_ch_table",
                    upgrade_attrs={
                        "table": tname,
                        "reason": reason,
                        "from_table": to_dict,
                        "to_table": to_snap_dict,
                        "drop_old_after_swap": False,
                        "preserve_old_suffix": "__dbw_failed",
                        "failed_suffix": "__dbw_old",
                        "rollback_kind": rollback_kind,
                        "rollback_reason": rollback_reason,
                    },
                    rollback_attrs={
                        "table": tname,
                        "reason": reason,
                        "from_table": from_dict,
                        "to_table": to_dict,
                        "drop_old_after_swap": False,
                        "preserve_old_suffix": "__dbw_old",
                        "failed_suffix": "__dbw_failed",
                        "rollback_kind": rollback_kind,
                        "rollback_reason": rollback_reason,
                    },
                    irreversible=rollback_kind == "irreversible",
                ))
                continue

            if ch_changes:
                upgrade_ops.append(Op(
                    object_type="alter_ch_options",
                    upgrade_attrs={
                        "table": tname,
                        "changes": ch_changes,
                    },
                    rollback_attrs={
                        "table": tname,
                        "changes": {k: {"from": v["to"], "to": v["from"]} for k, v in ch_changes.items()},
                    },
                ))
                rollback_ops.append(Op(
                    object_type="alter_ch_options",
                    upgrade_attrs={
                        "table": tname,
                        "changes": {k: {"from": v["to"], "to": v["from"]} for k, v in ch_changes.items()},
                    },
                    rollback_attrs={
                        "table": tname,
                        "changes": ch_changes,
                    },
                ))

        return upgrade_ops, rollback_ops

    @emit_with_cluster
    def emit(
        self, op: Op, db_name: Optional[str] = None
    , **kwargs: Any) -> List[MigrationStatement]:
        from dbwarden.engine.snapshot import _build_clickhouse_recreate_table_sql
        from dbwarden.engine.backends.clickhouse.cluster import ClusterableStatement

        stmts: list[MigrationStatement] = []

        if op.object_type == "recreate_ch_table":
            return _build_clickhouse_recreate_table_sql(
                op.upgrade_attrs, db_name, cluster_ctx=self._cluster_ctx,
            )

        if op.object_type == "alter_ch_options":
            changes_map = op.upgrade_attrs.get("changes", {})
            rollback_changes_map = (op.rollback_attrs or {}).get("changes")
            if rollback_changes_map is None:
                rollback_changes_map = {
                    key: {"from": change.get("to"), "to": change.get("from")}
                    for key, change in changes_map.items()
                }
            up_stmts: list[ClusterableStatement | str] = []
            rb_stmts: list[ClusterableStatement | str] = []
            table = op.upgrade_attrs["table"]
            prefix = f"ALTER TABLE {table}"

            def _append_change_stmts(
                target: list[ClusterableStatement | str],
                key: str,
                to_val: Any,
                from_val: Any,
            ) -> None:
                if key == "ch_settings" and isinstance(to_val, dict):
                    for setting_key, setting_value in to_val.items():
                        suffix = f"MODIFY SETTING {setting_key} = {setting_value}"
                        target.append(ClusterableStatement(prefix, suffix))
                elif key == "ch_ttl" and to_val:
                    ttl_sql = ", ".join(to_val) if isinstance(to_val, list) else str(to_val)
                    target.append(ClusterableStatement(prefix, f"MODIFY TTL {ttl_sql}"))
                elif key == "ch_ttl" and from_val:
                    target.append(ClusterableStatement(prefix, "REMOVE TTL"))
                elif key == "ch_order_by" and to_val:
                    target.append(ClusterableStatement(prefix, f"MODIFY ORDER BY {_format_clickhouse_expression(to_val)}"))
                elif key in _RECREATE_REQUIRED_CH_KEYS:
                    if key == "ch_engine":
                        note = (
                            f"-- ENGINE is immutable for '{table}'. "
                            f"To change the engine, author a data_op() with a controlled "
                            f"rebuild: CREATE TABLE new (...), INSERT ... SELECT, RENAME swap, "
                            f"DROP old. Re-run with --clickhouse-engine-recreate to "
                            f"auto-generate the swap sequence."
                        )
                    else:
                        note = (
                            f"-- {key} changed for '{table}'. "
                            f"Re-run with --clickhouse-engine-recreate to "
                            f"auto-generate recreation SQL."
                        )
                    target.append(note)

            for key, change in changes_map.items():
                _append_change_stmts(up_stmts, key, change.get("to"), change.get("from"))
            for key, change in rollback_changes_map.items():
                _append_change_stmts(rb_stmts, key, change.get("to"), change.get("from"))

            if up_stmts or rb_stmts:
                up_rendered = "\n".join(
                    s.render(self._cluster_ctx) if isinstance(s, ClusterableStatement) else s
                    for s in up_stmts
                ) if up_stmts else "-- no-op"
                rb_rendered = "\n".join(
                    s.render(self._cluster_ctx) if isinstance(s, ClusterableStatement) else s
                    for s in rb_stmts
                ) if rb_stmts else "-- no-op"
                stmts.append(MigrationStatement(
                    order=StatementOrder.ALTER_TABLE_OPTIONS,
                    upgrade_sql=up_rendered,
                    rollback_sql=rb_rendered,
                ))

        return stmts
