from __future__ import annotations

from typing import Any, List, Optional, Tuple

from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.core.statement_order import MigrationStatement, StatementOrder

# Table options that exist only in the CREATE TABLE statement. Changing either
# one means the table has to be rebuilt.
_SQ_TABLE_KEYS: tuple[str, ...] = ("sq_without_rowid", "sq_strict")

# Column attributes that likewise live only in the CREATE TABLE statement.
_SQ_COLUMN_KEYS: tuple[str, ...] = ("sq_generated", "sq_generated_mode", "sq_collate")


def _normalize_column_meta(meta: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        key: meta[key] for key in _SQ_COLUMN_KEYS
        if meta.get(key) is not None
    }
    if normalized.get("sq_generated"):
        # An omitted mode means STORED; spelling it out keeps a snapshot that
        # records the default from reading as a change against a model that
        # leaves it implicit.
        normalized["sq_generated_mode"] = str(
            normalized.get("sq_generated_mode", "STORED")
        ).upper()
    else:
        normalized.pop("sq_generated_mode", None)
    return normalized


class SqTableHandler(ObjectHandler):
    """Diffs SQLite table and column attributes that only a rebuild can change.

    ``WITHOUT ROWID``, ``STRICT``, generated columns and column collations are
    all properties of the CREATE statement.  A change to any of them is
    reported here and turned into a ``recreate_sq_table`` by the SQLite
    collapse pass.
    """

    object_type: str = "sq_table"
    op_types: tuple[str, ...] = (
        "alter_sq_table", "alter_sq_column_meta", "recreate_sq_table",
    )
    run_phase: RunPhase = RunPhase.DIFF
    statement_order: StatementOrder = StatementOrder.ALTER_TABLE_OPTIONS

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for tname, tdata in (snapshot.get("tables") or {}).items():
            options = dict(tdata.get("sq_table") or {})
            backend_spec = tdata.get("backend_table_spec") or {}
            if not options and backend_spec.get("backend") == "sqlite":
                options = {k: v for k, v in backend_spec.items() if k.startswith("sq_")}
            columns = {
                col_name: dict(col.get("sq_column") or {})
                for col_name, col in (tdata.get("columns") or {}).items()
                if col.get("sq_column")
            }
            if options or columns:
                result[tname] = {"options": options, "columns": columns}
        return result

    def model_spec_from_tables(self, model_tables: list[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table in model_tables:
            options = dict(getattr(table, "sq_table", None) or {})
            columns = {
                col.name: dict(getattr(col, "sq_meta", None) or {})
                for col in table.columns
                if getattr(col, "sq_meta", None)
            }
            if options or columns:
                result[table.name] = {"options": options, "columns": columns}
        return result

    def model_spec_from_config(self, config: Any) -> dict[str, Any]:
        return {}

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        if not spec:
            return {}
        return {
            tname: {
                "options": {
                    key: bool((entry.get("options") or {}).get(key, False))
                    for key in _SQ_TABLE_KEYS
                },
                "columns": {
                    col_name: _normalize_column_meta(meta)
                    for col_name, meta in (entry.get("columns") or {}).items()
                },
            }
            for tname, entry in spec.items()
        }

    def diff(
        self,
        snap_spec: dict[str, Any],
        model_spec: dict[str, Any],
    ) -> Tuple[List[Op], List[Op]]:
        upgrade_ops: list[Op] = []
        rollback_ops: list[Op] = []

        def _pair(op_type: str, upgrade_attrs: dict[str, Any], rollback_attrs: dict[str, Any]) -> None:
            upgrade_ops.append(Op(
                object_type=op_type,
                upgrade_attrs=upgrade_attrs,
                rollback_attrs=rollback_attrs,
            ))
            rollback_ops.append(Op(
                object_type=op_type,
                upgrade_attrs=rollback_attrs,
                rollback_attrs=upgrade_attrs,
            ))

        for tname in sorted(set(snap_spec) | set(model_spec)):
            snap_entry = snap_spec.get(tname, {})
            model_entry = model_spec.get(tname, {})

            snap_options = snap_entry.get("options") or {}
            model_options = model_entry.get("options") or {}
            for key in _SQ_TABLE_KEYS:
                snap_val = bool(snap_options.get(key, False))
                model_val = bool(model_options.get(key, False))
                if snap_val == model_val:
                    continue
                _pair(
                    "alter_sq_table",
                    {"table": tname, "key": key, "to_value": model_val, "from_value": snap_val},
                    {"table": tname, "key": key, "to_value": snap_val, "from_value": model_val},
                )

            snap_columns = snap_entry.get("columns") or {}
            model_columns = model_entry.get("columns") or {}
            for col_name in sorted(set(snap_columns) | set(model_columns)):
                snap_meta = snap_columns.get(col_name, {})
                model_meta = model_columns.get(col_name, {})
                if snap_meta == model_meta:
                    continue
                _pair(
                    "alter_sq_column_meta",
                    {
                        "table": tname, "column": col_name,
                        "to_value": model_meta, "from_value": snap_meta,
                    },
                    {
                        "table": tname, "column": col_name,
                        "to_value": snap_meta, "from_value": model_meta,
                    },
                )

        return upgrade_ops, rollback_ops

    def emit(
        self, op: Op, db_name: Optional[str] = None, **kwargs: Any
    ) -> List[MigrationStatement]:
        from dbwarden.engine.snapshot import _get_backend

        if _get_backend(db_name) != "sqlite":
            return []

        if op.object_type == "recreate_sq_table":
            from dbwarden.engine.backends.sqlite.sql_build import build_sqlite_table_rebuild

            return build_sqlite_table_rebuild(op.upgrade_attrs, db_name)

        # An alter that reached emit was not collapsed into a rebuild, which
        # means the before/after table shapes were unavailable. Say so instead
        # of emitting SQL SQLite would reject.
        table = op.upgrade_attrs.get("table", "")
        target = op.upgrade_attrs.get("key") or op.upgrade_attrs.get("column", "")
        note = (
            f"-- SQLite: '{target}' on {table} can only change by rebuilding the "
            f"table, and the table definition needed to generate the rebuild "
            f"was unavailable."
        )
        return [
            MigrationStatement(
                order=StatementOrder.ALTER_TABLE_OPTIONS,
                upgrade_sql=note,
                rollback_sql=note,
                rollback_kind="irreversible",
                rollback_reason=f"{target} change on {table} could not be rendered",
            )
        ]
