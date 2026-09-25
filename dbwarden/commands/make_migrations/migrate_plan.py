import re
from typing import Any

from dbwarden.engine.checksum import calculate_checksum
from dbwarden.engine.migration_name import Change, autogenerate_migration_name
from dbwarden.engine.safety.classifiers import LEGACY_SEVERITY, classify_operation, required_flags
from dbwarden.engine.safety.plans import bind_plan


def _resolve_migration_description(
    description: str | None,
    changes: list[Change],
) -> str:
    if description is None and changes:
        safe_desc = autogenerate_migration_name(changes)
        if not safe_desc:
            safe_desc = "auto_generated"
        return safe_desc
    return re.sub(r"[^a-zA-Z0-9]", "_", description or "auto_generated").lower()


def build_migration_plan(
    migration_id: str,
    changes: list[Change],
    upgrade_sql: str,
    *,
    typed_ops: list[dict] | None = None,
    content: str | None = None,
    backend: str = "",
    rollback_warnings: list[str] | None = None,
    project_root: str | None = None,
) -> dict[str, object]:
    operations = [_build_plan_operation(change) for change in changes]
    checksum = calculate_checksum([upgrade_sql]) if upgrade_sql.strip() else calculate_checksum([])

    op_types: dict[str, int] = {}
    for op in operations:
        t = op["type"]
        op_types[t] = op_types.get(t, 0) + 1

    summary = {
        "total_operations": len(operations),
        "operation_counts": op_types,
        "create_tables": op_types.get("create_table", 0),
        "drop_tables": op_types.get("drop_table", 0),
        "drop_columns": op_types.get("drop_column", 0),
    }

    plan: dict[str, object] = {
        "migration_id": migration_id,
        "operations": operations,
        "summary": summary,
        "required_flags": required_flags(typed_ops if typed_ops is not None else operations, backend),
        "checksum": checksum,
    }
    if rollback_warnings:
        plan["rollback_warnings"] = list(rollback_warnings)
    if content is not None:
        bind_plan(
            plan,
            content,
            typed_ops if typed_ops is not None else operations,
            backend,
            project_root=project_root,
        )
    return plan


def _build_plan_operation(change: Change) -> dict[str, str]:
    operation: dict[str, str] = {
        "type": change.operation,
        "table": change.table,
        "severity": LEGACY_SEVERITY[classify_operation({"type": change.operation})],
    }
    if change.resolved_from:
        operation["resolved_from"] = change.resolved_from
    if change.target:
        if change.operation in ("rename_column", "rename_table"):
            operation["new_name"] = change.target
        else:
            operation["column"] = change.target
    if change.operation == "rename_table":
        operation["old_table"] = change.table
    return operation


def _check_migration_scope(upgrade_ops: list[dict[str, Any]],
                           database: str | None = None) -> list[str]:
    """Warn about unusually large or destructive migrations.

    Returns a list of warning messages. Does not block generation.
    """
    warnings: list[str] = []
    create_tables = [op for op in upgrade_ops if op["type"] == "create_table"]
    drop_tables = [op for op in upgrade_ops if op["type"] == "drop_table"]
    drop_cols = [op for op in upgrade_ops if op["type"] == "drop_column"]
    total_ops = len(upgrade_ops)

    if len(create_tables) > 5:
        warnings.append(
            f"Migration creates {len(create_tables)} tables ({create_tables[0]['table']}, ...). "
            "Large schema additions may indicate un-scoped model diffing."
        )
    if len(drop_tables) > 3:
        tables_list = ", ".join(t["table"] for t in drop_tables[:5])
        warnings.append(
            f"Migration drops {len(drop_tables)} tables ({tables_list}, ...). "
            "This may include tables that should be excluded via model_tables."
        )
    if len(drop_cols) > 3:
        cols_list = ", ".join(f"{c['table']}.{c['column']}" for c in drop_cols[:5])
        warnings.append(
            f"Migration drops {len(drop_cols)} columns ({cols_list}, ...). "
            "Dropping many columns can cause application failures."
        )
    if total_ops > 30:
        warnings.append(
            f"Migration has {total_ops} operations. Consider splitting into smaller, "
            "focused migrations."
        )
    return warnings


def _build_table_rename_ops(
    confirmed_table_intents: set[tuple[str, str]],
    table_resolved_from_map: dict[tuple[str, str], str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    upgrade: list[dict[str, Any]] = []
    rollback: list[dict[str, Any]] = []
    for old_table, new_table in confirmed_table_intents:
        origin = (table_resolved_from_map or {}).get((old_table, new_table))
        upgrade.append({
            "type": "rename_table",
            "old_table": old_table,
            "new_table": new_table,
            "resolved_from": origin,
        })
        rollback.append({
            "type": "rename_table",
            "old_table": new_table,
            "new_table": old_table,
        })
    return {"upgrade": upgrade, "rollback": rollback}
