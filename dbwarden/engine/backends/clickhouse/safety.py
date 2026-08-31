from __future__ import annotations

from enum import Enum
from typing import Any

from dbwarden.engine.core.models import ModelTable
from dbwarden.models import SafetyIssue


class Safety(str, Enum):
    SAFE = "SAFE"
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


CH_COLUMN_CRITICAL = frozenset({"ch_type", "ch_low_cardinality", "ch_nullable"})
CH_COLUMN_WARN = frozenset({"ch_codec", "ch_default_expression", "ch_materialized", "ch_alias", "ch_ephemeral", "ch_ttl"})


def classify_ch_column_change(key: str, *, change: dict | None = None) -> Safety:
    if key == "ch_type" and change:
        to_val = str(change.get("to", ""))
        from_val = str(change.get("from", ""))
        if "AggregateFunction" in to_val or "AggregateFunction" in from_val:
            return Safety.CRITICAL
        return Safety.WARN
    if key in CH_COLUMN_CRITICAL:
        return Safety.CRITICAL
    if key in CH_COLUMN_WARN:
        return Safety.WARN
    return Safety.INFO


CH_OPTION_CRITICAL = frozenset({
    "ch_engine_raw", "ch_order_by", "ch_object_type",
    "ch_select_statement", "ch_to_table", "ch_dictionary",
})
CH_OPTION_WARN = frozenset({
    "ch_settings", "ch_zookeeper_path", "ch_replica_name",
    "ch_dict_layout", "ch_dict_source", "ch_dict_lifetime", "ch_dict_primary_key",
    "ch_ttl",
})


def classify_ch_options_change(key: str) -> Safety:
    if key in CH_OPTION_CRITICAL:
        return Safety.CRITICAL
    if key in CH_OPTION_WARN:
        return Safety.WARN
    return Safety.INFO


_CH_OPTION_KEY_MAP: dict[str, str] = {
    "ch_order_by": "clickhouse_order_by",
    "ch_partition_by": "clickhouse_partition_by",
    "ch_ttl": "clickhouse_ttl",
    "ch_engine": "clickhouse_engine",
    "ch_select_statement": "clickhouse_mv_query",
    "ch_zookeeper_path": "clickhouse_zookeeper_path",
    "ch_replica_name": "clickhouse_replica_name",
    "ch_dict_source": "clickhouse_dict_source",
    "ch_dict_layout": "clickhouse_dict_layout",
    "ch_dict_lifetime": "clickhouse_dict_lifetime",
}


def _normalize_option_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    return value


def _classify_option_to_schema_severity(key: str) -> tuple[str, str | None]:
    """Map classify_ch_options_change() output to schema-analysis severity.

    The schema analysis path (analyze_clickhouse_options) uses a simpler
    severity model: WARNING with --force, or INFO without. This helper
    bridges the two-level classifier (CRITICAL/WARN/INFO) to the
    schema-analysis format.
    """
    classification = classify_ch_options_change(key)
    if classification == Safety.INFO:
        return ("INFO", None)
    # Both WARN and CRITICAL require --force in the schema analysis path
    return ("WARNING", "--force")


def analyze_clickhouse_options(table_snapshot: dict[str, Any], model_table: ModelTable) -> list[SafetyIssue]:
    issues: list[SafetyIssue] = []
    snapshot_options = table_snapshot.get("ch_options", {})
    model_options = model_table.clickhouse_options
    if not snapshot_options and not model_options:
        return issues

    for ch_key in _CH_OPTION_KEY_MAP:
        model_val = _normalize_option_value(model_options.get(ch_key))
        snap_key = ch_key
        snap_val = _normalize_option_value(snapshot_options.get(snap_key))
        if snap_val is None and snap_key in _CH_OPTION_KEY_MAP:
            snap_val = _normalize_option_value(snapshot_options.get(_CH_OPTION_KEY_MAP[snap_key]))

        if snap_val != model_val:
            if snap_val is None and model_val is None:
                continue
            severity, required_flag = _classify_option_to_schema_severity(ch_key)
            issues.append(
                SafetyIssue(
                    severity=severity,
                    change_type=_CH_OPTION_KEY_MAP.get(ch_key, ch_key),
                    table_name=model_table.name,
                    message=f"Change {ch_key.replace('ch_', '').replace('_', ' ')} for '{model_table.name}'",
                    required_flag=required_flag,
                )
            )

    model_projections_raw = model_options.get("ch_projections") or []
    model_projections = set()
    for proj in model_projections_raw:
        if isinstance(proj, dict):
            model_projections.add(proj["name"])
        elif hasattr(proj, "name"):
            model_projections.add(proj.name)

    snapshot_projections_raw = snapshot_options.get("ch_projections") or snapshot_options.get("clickhouse_projections") or []
    snapshot_projections = set()
    for proj in snapshot_projections_raw:
        if isinstance(proj, dict):
            snapshot_projections.add(proj["name"])
        elif hasattr(proj, "name"):
            snapshot_projections.add(proj.name)

    for projection_name in sorted(model_projections - snapshot_projections):
        issues.append(
            SafetyIssue(
                severity="INFO",
                change_type="add_projection",
                table_name=model_table.name,
                message=f"Add projection '{projection_name}' to '{model_table.name}'",
            )
        )

    for projection_name in sorted(snapshot_projections - model_projections):
        issues.append(
            SafetyIssue(
                severity="WARNING",
                change_type="drop_projection",
                table_name=model_table.name,
                message=f"Drop projection '{projection_name}' from '{model_table.name}'",
                required_flag="--force",
            )
        )

    return issues


def classify_ch_safety(
    op: dict,
    model_column: Any = None,
    snapshot_column: Any = None,
) -> list[SafetyIssue]:
    issues: list[SafetyIssue] = []
    if op["type"] == "alter_ch_column":
        for key, change in op.get("ch_options", {}).items():
            issues.append(SafetyIssue(
                change_type="change_ch_column",
                table_name=op["table"],
                column_name=op["column"],
                severity=classify_ch_column_change(key, change=change),
                message=f"CH column {op['column']} {key}: {change.get('from')} -> {change.get('to')}",
            ))
    elif op["type"] == "alter_ch_options":
        for key, change in op.get("ch_options", {}).items():
            issues.append(SafetyIssue(
                change_type="change_ch_options",
                table_name=op["table"],
                severity=classify_ch_options_change(key),
                message=f"CH option {key}: {change.get('from')} -> {change.get('to')}",
            ))
    elif op["type"] == "drop_table" and op.get("object_type") == "materialized_view":
        issues.append(SafetyIssue(
            change_type="drop_materialized_view",
            table_name=op["table"],
            severity="CRITICAL",
            message=f"Dropping materialized view {op['table']} will lose the transformation logic",
        ))
    elif op["type"] == "recreate_ch_table" and op.get("dependent_mvs"):
        for mv in op["dependent_mvs"]:
            issues.append(SafetyIssue(
                severity="INFO",
                change_type="detach_reattach_materialized_view",
                table_name=op["table"],
                message=f"Materialized view '{mv}' will be detached before and reattached after recreating '{op['table']}'",
            ))
    elif op["type"] == "recreate_ch_table" and op.get("to_table", {}).get("object_type") == "dictionary":
        issues.append(SafetyIssue(
            severity="CRITICAL",
            change_type="recreate_dictionary",
            table_name=op["table"],
            message=f"Dictionary '{op['table']}' will be dropped and recreated, losing any cached data",
        ))
    elif op["type"] == "recreate_ch_table" and (
        op.get("from_table", {}).get("object_type") == "materialized_view"
        or op.get("to_table", {}).get("object_type") == "materialized_view"
    ):
        issues.append(SafetyIssue(
            severity="CRITICAL",
            change_type="recreate_materialized_view",
            table_name=op["table"],
            message=f"Materialized view '{op['table']}' will be dropped and recreated, losing any accumulated data",
        ))
    return issues
