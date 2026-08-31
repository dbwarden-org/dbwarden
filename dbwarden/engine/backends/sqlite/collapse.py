"""Fold SQLite-unsupported operations into a table rebuild.

The diff handlers are backend-agnostic: they emit ``alter_column_type``,
``add_foreign_key`` and friends regardless of which database is targeted.  Most
of those statements do not exist in SQLite.  Rather than emitting a comment for
each one, every op that SQLite cannot express is replaced - per table - by a
single ``recreate_sq_table`` op carrying the before and after shape of the
table, which :mod:`dbwarden.engine.backends.sqlite.sql_build` renders as the
create/copy/drop/rename sequence.
"""

from __future__ import annotations

from typing import Any

from dbwarden.engine.backends.sqlite.sql_build import (
    add_column_requires_rebuild,
    drop_column_requires_rebuild,
)

# Operations with no SQLite equivalent. Each one forces a rebuild of its table.
REBUILD_OP_TYPES: frozenset[str] = frozenset({
    "alter_column_type",
    "alter_column_nullable",
    "alter_column_default",
    "alter_column_autoincrement",
    "add_unique_constraint",
    "drop_unique_constraint",
    "rename_unique_constraint",
    "add_check_constraint",
    "drop_check_constraint",
    "add_foreign_key",
    "drop_foreign_key",
    "alter_sq_table",
    "alter_sq_column_meta",
    "recreate_sq_table",
})

# Operations that only make sense on another backend. On SQLite they are
# dropped rather than rebuilt: there is nothing in the SQLite schema they
# could correspond to.
_IGNORED_OP_TYPES: frozenset[str] = frozenset({
    "add_exclude_constraint",
    "drop_exclude_constraint",
    "alter_pg_table",
    "alter_pg_column_meta",
    "alter_my_table",
    "alter_my_column_meta",
    "alter_pg_storage_param",
    "alter_pg_rls",
    "alter_column_statistics",
})

_REASON_LABELS: dict[str, str] = {
    "alter_column_type": "column type change",
    "alter_column_nullable": "column nullability change",
    "alter_column_default": "column default change",
    "alter_column_autoincrement": "autoincrement change",
    "add_unique_constraint": "added UNIQUE constraint",
    "drop_unique_constraint": "dropped UNIQUE constraint",
    "rename_unique_constraint": "renamed UNIQUE constraint",
    "add_check_constraint": "added CHECK constraint",
    "drop_check_constraint": "dropped CHECK constraint",
    "add_foreign_key": "added FOREIGN KEY",
    "drop_foreign_key": "dropped FOREIGN KEY",
    "alter_sq_table": "table option change",
    "alter_sq_column_meta": "generated column or collation change",
    "add_column": "added column",
    "drop_column": "dropped column",
}


def _constraint_kind(constraint: dict[str, Any]) -> str | None:
    """Classify a snapshot constraint by shape as well as by its ``type``.

    Diff handlers canonicalize the snapshot in place, which strips ``type`` and
    renames ``referenced_table`` to ``referred_table``.  Reading the shape as
    well means a rebuild built after that pass still sees the constraints.
    """
    kind = constraint.get("type")
    if kind in ("foreign_key", "unique", "check", "exclude"):
        return kind
    if constraint.get("referred_table") or constraint.get("referenced_table"):
        return "foreign_key"
    if constraint.get("expression"):
        return "check"
    if constraint.get("columns"):
        return "unique"
    return None


def _constraint_table(key: str, constraint: dict[str, Any]) -> str | None:
    table = constraint.get("table")
    if table:
        return table
    # Canonicalization drops "table"; the snapshot key is "<table>.<name>".
    return key.rsplit(".", 1)[0] if "." in key else None


def snapshot_state_entries(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Rebuild per-table model-state entries out of a schema snapshot.

    A snapshot keeps indexes and constraints in top-level maps keyed by name,
    while a rebuild needs one self-contained entry per table - the same shape
    :func:`dbwarden.engine.offline.reconstruct_model_table` consumes.
    """
    entries: dict[str, dict[str, Any]] = {}
    for table_name, table_data in (snapshot.get("tables") or {}).items():
        columns: dict[str, Any] = {}
        for col_name, col_entry in (table_data.get("columns") or {}).items():
            column = dict(col_entry)
            column.setdefault("name", col_name)
            columns[col_name] = column

        indexes = [
            dict(index)
            for index in (snapshot.get("indexes") or {}).values()
            if index.get("table") == table_name
        ]

        foreign_keys: list[dict[str, Any]] = []
        uniques: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []
        for key, constraint in (snapshot.get("constraints") or {}).items():
            if _constraint_table(key, constraint) != table_name:
                continue
            kind = _constraint_kind(constraint)
            if kind == "foreign_key":
                foreign_keys.append(dict(constraint))
            elif kind == "unique":
                uniques.append(dict(constraint))
            elif kind == "check":
                checks.append(dict(constraint))

        backend_spec = dict(table_data.get("backend_table_spec") or {})
        backend_spec.update(table_data.get("sq_table") or {})
        if backend_spec:
            backend_spec.setdefault("backend", "sqlite")

        entries[table_name] = {
            "name": table_name,
            "object_type": table_data.get("object_type", "table"),
            "comment": table_data.get("comment"),
            "schema": table_data.get("schema"),
            "columns": columns,
            "indexes": indexes,
            "foreign_keys": foreign_keys,
            "uniques": uniques,
            "checks": checks,
            "backend_table_spec": backend_spec,
            "sq_index_ddl": dict(table_data.get("sq_index_ddl") or {}),
        }
    return entries


def model_state_entries(model_tables: list[Any]) -> dict[str, dict[str, Any]]:
    """Model-state entries for the tables a rebuild may need to render."""
    from dbwarden.engine.core.model_state import _table_to_state_entry

    return {table.name: _table_to_state_entry(table) for table in model_tables}


def _op_table(op: dict[str, Any]) -> str | None:
    return op.get("table")


def _needs_rebuild(
    op: dict[str, Any],
    from_entries: dict[str, dict[str, Any]],
    to_entries: dict[str, dict[str, Any]],
) -> bool:
    op_type = op.get("type")
    if op_type in REBUILD_OP_TYPES:
        return True
    table = _op_table(op)
    if not table:
        return False
    if op_type == "add_column":
        column = _model_column(to_entries.get(table), op.get("column"))
        return column is not None and add_column_requires_rebuild(column) is not None
    if op_type == "drop_column":
        return drop_column_requires_rebuild(
            op.get("column", ""), from_entries.get(table),
        ) is not None
    return False


def _model_column(entry: dict[str, Any] | None, column_name: str | None) -> Any:
    if not entry or not column_name:
        return None
    col_entry = (entry.get("columns") or {}).get(column_name)
    if not col_entry:
        return None
    from dbwarden.engine.core.model_state import reconstruct_model_column

    col_entry = dict(col_entry)
    col_entry.setdefault("name", column_name)
    return reconstruct_model_column(col_entry)


def collapse_sqlite_ops(
    upgrade_ops: list[dict[str, Any]],
    rollback_ops: list[dict[str, Any]],
    *,
    from_entries: dict[str, dict[str, Any]],
    to_entries: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Replace SQLite-unsupported ops with per-table ``recreate_sq_table`` ops.

    ``from_entries`` and ``to_entries`` map a table name to its model-state
    entry before and after the change; both shapes are needed because a rebuild
    has to render the table twice, once per migration direction.
    """
    created = {op["table"] for op in upgrade_ops if op.get("type") == "create_table" and op.get("table")}
    dropped = {op["table"] for op in upgrade_ops if op.get("type") == "drop_table" and op.get("table")}
    settled = created | dropped

    rebuild_reasons: dict[str, list[str]] = {}
    for op in upgrade_ops:
        table = _op_table(op)
        if not table or table in settled:
            continue
        if _needs_rebuild(op, from_entries, to_entries):
            label = _REASON_LABELS.get(op.get("type", ""), op.get("type", "schema change"))
            reasons = rebuild_reasons.setdefault(table, [])
            if label not in reasons:
                reasons.append(label)

    # A rebuild can only be rendered when both shapes are known. Without them
    # the original ops are left alone, so the failure is a visible one.
    rebuild_tables = {
        table for table in rebuild_reasons
        if table in from_entries and table in to_entries
    }

    def _keep(op: dict[str, Any]) -> bool:
        op_type = op.get("type")
        table = _op_table(op)
        if op_type in _IGNORED_OP_TYPES:
            return False
        if table in settled:
            # A table being created carries its constraints inside CREATE
            # TABLE; a table being dropped takes them with it.
            return op_type not in REBUILD_OP_TYPES
        if table in rebuild_tables:
            # A rebuild recreates the table's indexes itself, and index
            # statements are ordered before it, so an index created here would
            # be dropped again by the rebuild that follows.
            if op_type in ("add_index", "drop_index"):
                return False
            return not _needs_rebuild(op, from_entries, to_entries)
        return True

    kept_upgrade = [op for op in upgrade_ops if _keep(op)]
    kept_rollback = [op for op in rollback_ops if _keep(op)]

    for table in sorted(rebuild_tables):
        reason = ", ".join(rebuild_reasons[table])
        index_ddl = dict(from_entries[table].get("sq_index_ddl") or {})
        kept_upgrade.append({
            "type": "recreate_sq_table",
            "table": table,
            "reason": reason,
            "from_table": from_entries[table],
            "to_table": to_entries[table],
            "index_ddl": index_ddl,
        })
        kept_rollback.append({
            "type": "recreate_sq_table",
            "table": table,
            "reason": reason,
            "from_table": to_entries[table],
            "to_table": from_entries[table],
            "index_ddl": index_ddl,
        })

    return kept_upgrade, kept_rollback
