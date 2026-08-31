"""Impact analysis for SQLite-specific schema changes.

The cost that matters on SQLite is the table rebuild: any change SQLite's
``ALTER TABLE`` cannot express copies the whole table, holds a write lock for
the duration, and rebuilds the table's indexes.  Those changes are reported
here so they are visible before the migration runs rather than only in the
generated SQL.
"""

from __future__ import annotations

from typing import Any

from dbwarden.engine.core.models import ModelTable
from dbwarden.models import SafetyIssue

_SQ_TABLE_RULES: dict[str, str] = {
    "sq_without_rowid": "Change WITHOUT ROWID for '{table}'",
    "sq_strict": "Change STRICT for '{table}'",
}


def _snapshot_sq_table(table_snapshot: dict[str, Any]) -> dict[str, Any]:
    options = dict(table_snapshot.get("sq_table") or {})
    if options:
        return options
    backend_spec = table_snapshot.get("backend_table_spec") or {}
    if backend_spec.get("backend") == "sqlite":
        return {k: v for k, v in backend_spec.items() if k.startswith("sq_")}
    return {}


def analyze_sqlite_options(
    table_snapshot: dict[str, Any], model_table: ModelTable
) -> list[SafetyIssue]:
    issues: list[SafetyIssue] = []
    snapshot_options = _snapshot_sq_table(table_snapshot)
    model_options = getattr(model_table, "sq_table", None) or {}

    for key, template in _SQ_TABLE_RULES.items():
        snap_val = bool(snapshot_options.get(key, False))
        model_val = bool(model_options.get(key, False))
        if snap_val == model_val:
            continue
        issues.append(
            SafetyIssue(
                severity="WARNING",
                change_type=key,
                table_name=model_table.name,
                message=(
                    template.format(table=model_table.name)
                    + " (rebuilds the table and copies every row)"
                ),
                required_flag="--force",
            )
        )

    snapshot_columns = table_snapshot.get("columns", {}) or {}
    for column in model_table.columns:
        snap_meta = (snapshot_columns.get(column.name, {}) or {}).get("sq_column") or {}
        model_meta = getattr(column, "sq_meta", None) or {}
        if snap_meta.get("sq_generated") != model_meta.get("sq_generated"):
            issues.append(
                SafetyIssue(
                    severity="WARNING",
                    change_type="change_sq_generated",
                    table_name=model_table.name,
                    column_name=column.name,
                    message=(
                        f"Change generated expression of "
                        f"'{model_table.name}.{column.name}' "
                        f"(rebuilds the table and copies every row)"
                    ),
                    required_flag="--force",
                )
            )
        elif snap_meta.get("sq_collate") != model_meta.get("sq_collate"):
            issues.append(
                SafetyIssue(
                    severity="WARNING",
                    change_type="change_sq_collate",
                    table_name=model_table.name,
                    column_name=column.name,
                    message=(
                        f"Change collation of '{model_table.name}.{column.name}' "
                        f"(rebuilds the table and copies every row)"
                    ),
                    required_flag="--force",
                )
            )

    return issues
