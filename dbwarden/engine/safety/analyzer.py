from __future__ import annotations

from typing import Any

from dbwarden.engine.backends.clickhouse.safety import (
    analyze_clickhouse_options,
)
from dbwarden.engine.backends.postgresql.safety import (
    classify_pg_type_change,
)
from dbwarden.engine.backends.sqlite.safety import (
    analyze_sqlite_options,
)
from dbwarden.engine.discovery import (
    ModelTable,
)
from dbwarden.engine.safety.classifiers import (
    _snapshot_column_type_signature,
)
from dbwarden.models import SafetyIssue


def analyze_schema(
    model_tables: list[ModelTable],
    schema_snapshot: dict[str, dict[str, Any]],
) -> list[SafetyIssue]:
    issues: list[SafetyIssue] = []
    model_by_name = {table.name: table for table in model_tables}
    snapshot_names = set(schema_snapshot.keys())
    model_names = set(model_by_name.keys())

    for table_name in sorted(model_names - snapshot_names):
        table = model_by_name[table_name]
        object_label = table.object_type.replace("_", " ")
        issues.append(
            SafetyIssue(
                severity="INFO",
                change_type="create_table",
                table_name=table_name,
                message=f"Create {object_label} '{table_name}'",
            )
        )

    for table_name in sorted(snapshot_names - model_names):
        issues.append(
            SafetyIssue(
                severity="WARNING",
                change_type="drop_table",
                table_name=table_name,
                message=f"Drop table '{table_name}'",
                required_flag="--force",
            )
        )

    for table_name in sorted(snapshot_names & model_names):
        model_table = model_by_name[table_name]
        table_snapshot = schema_snapshot[table_name]
        issues.extend(_analyze_table(table_snapshot, model_table))

    return issues


def _analyze_table(table_snapshot: dict[str, Any], model_table: ModelTable) -> list[SafetyIssue]:
    issues: list[SafetyIssue] = []
    snapshot_columns = table_snapshot.get("columns", {})
    model_columns = {column.name: column for column in model_table.columns}

    if table_snapshot.get("comment") != model_table.comment:
        issues.append(
            SafetyIssue(
                severity="INFO",
                change_type="change_table_comment",
                table_name=model_table.name,
                message=f"Change comment of table '{model_table.name}'",
            )
        )

    if table_snapshot.get("object_type", "table") != model_table.object_type:
        issues.append(
            SafetyIssue(
                severity="WARNING",
                change_type="change_object_type",
                table_name=model_table.name,
                message=(
                    f"Change object type for '{model_table.name}' from "
                    f"{table_snapshot.get('object_type', 'table')} to {model_table.object_type}"
                ),
                required_flag="--force",
            )
        )

    for column_name in sorted(model_columns.keys() - snapshot_columns.keys()):
        column = model_columns[column_name]
        issues.append(
            SafetyIssue(
                severity="INFO",
                change_type="add_column",
                table_name=model_table.name,
                column_name=column_name,
                message=f"Add column '{column_name}' to '{model_table.name}'",
            )
        )

    for column_name in sorted(snapshot_columns.keys() - model_columns.keys()):
        issues.append(
            SafetyIssue(
                severity="WARNING",
                change_type="drop_column",
                table_name=model_table.name,
                column_name=column_name,
                message=f"Drop column '{column_name}' from '{model_table.name}'",
                required_flag="--force",
            )
        )

    for column_name in sorted(snapshot_columns.keys() & model_columns.keys()):
        snapshot_column = snapshot_columns[column_name]
        model_column = model_columns[column_name]
        from dbwarden.engine.snapshot.type_normalize import _model_type_str, normalize_type

        snapshot_type = _snapshot_column_type_signature(snapshot_column)
        model_type = normalize_type(_model_type_str(model_column.type))
        model_pg_column = None
        if model_column.pg_meta:
            model_pg_column = {}
            for src, dst in (
                ("pg_collation", "collation"),
                ("pg_storage", "storage"),
                ("pg_compression", "compression"),
                ("pg_generated", "generated"),
                ("pg_identity", "identity"),
                ("pg_identity_start", "identity_start"),
                ("pg_identity_increment", "identity_increment"),
                ("pg_identity_min", "identity_min"),
                ("pg_identity_max", "identity_max"),
            ):
                if src in model_column.pg_meta:
                    model_pg_column[dst] = model_column.pg_meta[src]
            if not model_pg_column:
                model_pg_column = None
            if model_column.pg_meta.get("pg_type"):
                model_type = {
                    "type": model_column.pg_meta["pg_type"].get("kind", model_type.get("type")),
                    "pg_type": model_column.pg_meta["pg_type"],
                }
            if model_column.pg_meta.get("pg_enum_name"):
                model_type = {"type": "enum", "enum_name": model_column.pg_meta["pg_enum_name"]}
        severity = "WARNING"
        required_flag = "--force"
        if table_snapshot.get("database_type") == "postgresql":
            classification = classify_pg_type_change(snapshot_type, model_type)
            severity = {"SAFE": "INFO", "WARN": "WARNING", "CRITICAL": "WARNING"}[classification]
            required_flag = None if classification == "SAFE" else "--force"
        if snapshot_type != model_type:
            issues.append(
                SafetyIssue(
                    severity=severity,
                    change_type="change_column_type",
                    table_name=model_table.name,
                    column_name=column_name,
                    message=(
                        f"Change type of '{model_table.name}.{column_name}' from "
                        f"{snapshot_column.get('type')} to {model_column.type}"
                    ),
                    required_flag=required_flag,
                )
            )

        if snapshot_column.get("comment") != model_column.comment:
            issues.append(
                SafetyIssue(
                    severity="INFO",
                    change_type="change_column_comment",
                    table_name=model_table.name,
                    column_name=column_name,
                    message=(
                        f"Change comment of '{model_table.name}.{column_name}'"
                    ),
                )
            )

        if snapshot_column.get("pg_column") != model_pg_column:
            issues.append(
                SafetyIssue(
                    severity="WARNING",
                    change_type="change_pg_column_meta",
                    table_name=model_table.name,
                    column_name=column_name,
                    message=(
                        f"Change PostgreSQL metadata of '{model_table.name}.{column_name}'"
                    ),
                    required_flag="--force",
                )
            )

    snap_pg_table = table_snapshot.get("pg_table") or {}
    model_pg_table = model_table.pg_table or {}
    pg_table_keys = [("fillfactor", "INFO", None, "Change fillfactor for '{table}'"),
                     ("tablespace", "WARNING", "--force", "Change tablespace for '{table}'"),
                     ("inherits", "WARNING", "--force", "Change inheritance parents for '{table}'"),
                     ("pg_excludes", "WARNING", "--force", "Change EXCLUDE constraints for '{table}'")]
    for key, severity, required_flag, msg_template in pg_table_keys:
        if snap_pg_table.get(key) != model_pg_table.get(key):
            issues.append(
                SafetyIssue(
                    severity=severity,
                    change_type=f"change_pg_table_{key}",
                    table_name=model_table.name,
                    message=msg_template.replace("{table}", model_table.name),
                    required_flag=required_flag,
                )
            )

    snap_storage = snap_pg_table.get("pg_storage_params") or {}
    model_storage = model_pg_table.get("pg_storage_params") or {}
    for key in sorted(set(snap_storage.keys()) | set(model_storage.keys())):
        if snap_storage.get(key) != model_storage.get(key):
            issues.append(
                SafetyIssue(
                    severity="INFO",
                    change_type="change_pg_storage_params",
                    table_name=model_table.name,
                    message=f"Change storage parameter '{key}' for '{model_table.name}'",
                )
            )

    issues.extend(analyze_clickhouse_options(table_snapshot, model_table))
    issues.extend(analyze_sqlite_options(table_snapshot, model_table))
    return issues
