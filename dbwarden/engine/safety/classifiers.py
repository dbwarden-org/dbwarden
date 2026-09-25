from __future__ import annotations

from enum import Enum
from typing import Any


class Safety(str, Enum):
    SAFE = "SAFE"
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


LEVELS = tuple(Safety)
LEGACY_SEVERITY = {
    Safety.SAFE: "INFO",
    Safety.INFO: "INFO",
    Safety.WARN: "WARNING",
    Safety.CRITICAL: "ERROR",
    Safety.UNKNOWN: "UNKNOWN",
}
_INFO_OPS = frozenset(
    [
        "create_table",
        "create_view",
        "add_column",
        "create_schema",
        "create_type",
        "create_domain",
        "create_sequence",
        "create_extension",
        "create_function",
        "create_composite_type",
        "create_event_trigger",
        "create_extended_statistics",
        "create_role",
        "add_index",
        "create_index",
        "add_foreign_key",
        "add_unique_constraint",
        "add_check_constraint",
        "add_exclude_constraint",
        "add_policy",
        "add_grant",
        "alter_column_comment",
        "alter_table_comment",
        "alter_column_default",
        "drop_not_null",
        "alter_enum_add_value",
        "rename_unique_constraint",
        "validate_constraint",
        "create_ch_agg_target",
        "create_ch_named_collection",
        "create_ch_settings_profile",
        "create_ch_role",
        "create_ch_user",
        "create_ch_quota",
        "create_ch_row_policy",
    ]
)
_WARN_OPS = frozenset(
    [
        "rename_column",
        "rename_table",
        "set_not_null",
        "alter_column_type",
        "alter_column_autoincrement",
        "alter_pg_column_meta",
        "alter_my_column_meta",
        "alter_sq_column_meta",
        "alter_ch_column",
        "alter_ch_dict",
        "alter_ch_options",
        "alter_ch_projection",
        "alter_ch_row_policy",
        "alter_ch_skip_index",
        "alter_default_privileges",
        "alter_pg_rls",
        "alter_pg_table",
        "alter_role",
        "alter_sq_table",
        "alter_my_table",
        "alter_view",
        "detach_partition",
        "attach_partition",
        "drop_ch_named_collection",
        "drop_ch_quota",
        "drop_ch_role",
        "drop_ch_row_policy",
        "drop_ch_settings_profile",
        "drop_ch_user",
        "drop_check_constraint",
        "drop_composite_type",
        "drop_domain",
        "drop_event_trigger",
        "drop_exclude_constraint",
        "drop_extended_statistics",
        "drop_foreign_key",
        "drop_function",
        "drop_index",
        "drop_role",
        "drop_schema",
        "drop_sequence",
        "drop_type",
        "drop_unique_constraint",
        "modify_mv_query",
        "modify_mv_refresh",
        "recreate_sq_table",
        "revoke_grant",
        "refresh_matview",
        "alter_trigger",
        "alter_column_statistics",
        "alter_ch_named_collection",
        "alter_ch_settings_profile",
        "alter_ch_role",
        "alter_ch_user",
        "alter_ch_quota",
        "alter_ch_grant",
        "alter_policy",
        "drop_policy",
        "alter_pg_storage_param",
        "drop_view",
    ]
)
_CRITICAL_OPS = frozenset(
    {
        "drop_table",
        "drop_column",
        "drop_ch_agg_target",
        "alter_pg_partition",
        "truncate",
        "safe_type_contract",
    }
)
_INFO_OPS |= {"create_pg_extension", "alter_ch_comment", "create_trigger"}
_WARN_OPS |= {
    "alter_sequence",
    "alter_domain",
    "alter_function",
    "alter_composite_type",
    "alter_event_trigger",
    "alter_extended_statistics",
    "alter_pg_extension",
    "drop_pg_extension",
    "drop_trigger",
    "add_projection",
    "drop_projection",
    "recreate_dictionary",
}
_CRITICAL_OPS |= {"drop_materialized_view", "recreate_materialized_view"}


def severity_level(value: str | Safety, *, allow_unknown: bool = False) -> Safety:
    try:
        level = Safety(value.upper())
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            "Severity must be SAFE, INFO, WARN, or CRITICAL. hint: use --help"
        ) from exc
    if level == Safety.UNKNOWN and not allow_unknown:
        raise ValueError(
            "UNKNOWN is not a selectable severity. hint: classify the migration first"
        )
    return level


def exceeds(severity: str | Safety, ceiling: str | Safety) -> bool:
    cap = severity_level(ceiling)
    level = Safety(severity)
    # UNKNOWN exceeds every ceiling, including CRITICAL: a file whose
    # severity cannot be established must stop, never run by default.
    return level == Safety.UNKNOWN or (
        cap != Safety.CRITICAL and LEVELS.index(level) > LEVELS.index(cap)
    )


def classify_operation(op: dict[str, Any], backend: str = "") -> Safety:
    level = _classify_operation(op, backend)
    declaration = op.get("plugin_safety")
    if declaration is None:
        return level
    if not isinstance(declaration, dict) or not isinstance(declaration.get("plugin"), str) or not declaration["plugin"]:
        return Safety.UNKNOWN
    try:
        declared = severity_level(declaration["severity"])
    except (KeyError, ValueError):
        return Safety.UNKNOWN
    return declared if level == Safety.UNKNOWN else max(level, declared, key=LEVELS.index)


def _classify_operation(op: dict[str, Any], backend: str = "") -> Safety:
    kind = op.get("type", op.get("kind", ""))
    if kind == "declarative_data":
        value = op.get("data_severity")
        return Safety(value) if value in {"INFO", "WARN", "CRITICAL"} else Safety.UNKNOWN
    if kind == "select":
        return Safety.SAFE
    if kind == "alter_pg_storage_param" or (
        kind == "alter_pg_table"
        and op.get("key") in {"fillfactor", "pg_fillfactor", "pg_storage_params"}
    ):
        return Safety.INFO
    if kind in {"insert", "update", "delete", "backfill"}:
        return Safety.INFO if op.get("bounded_key") is True else Safety.WARN
    if kind in {"add_index", "create_index"} and backend == "postgresql":
        return Safety.INFO if op.get("concurrently", True) else Safety.WARN
    if kind == "alter_column_nullable":
        return (
            Safety.INFO
            if op.get("to_nullable", op.get("nullable")) is True
            else Safety.WARN
        )
    if kind == "add_column":
        definition = op.get("definition") or {}
        column = op.get("model_column")
        nullable = definition.get(
            "nullable",
            column.get("nullable", True)
            if isinstance(column, dict)
            else getattr(column, "nullable", True),
        )
        default = definition.get(
            "default",
            column.get("default")
            if isinstance(column, dict)
            else getattr(column, "default", None),
        )
        if (
            not nullable
            or op.get("volatile_default")
            or (
                isinstance(default, str)
                and (
                    "(" in default
                    or default.upper()
                    in {"CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME"}
                )
            )
        ):
            return Safety.WARN
    if kind == "recreate_ch_table":
        return (
            Safety.CRITICAL
            if op.get("lossy")
            or op.get("drop_old_after_swap")
            or op.get("rollback_kind") == "irreversible"
            else Safety.WARN
        )
    if kind == "alter_column_type" and backend == "postgresql":
        from dbwarden.engine.backends.postgresql.safety import classify_pg_type_change
        from dbwarden.engine.snapshot.type_normalize import normalize_type

        before, after = (
            op.get("from_type", op.get("snap_type")),
            op.get("to_type", op.get("model_type")),
        )
        if isinstance(before, str) and isinstance(after, str):
            before, after = normalize_type(before), normalize_type(after)
        if isinstance(before, dict) and isinstance(after, dict):
            return Safety(classify_pg_type_change(before, after))
    if kind == "alter_ch_column":
        from dbwarden.engine.backends.clickhouse.safety import classify_ch_column_change

        changes = op.get("changes") or {}
        if changes:
            return max(
                (classify_ch_column_change(k, change=v) for k, v in changes.items()),
                key=LEVELS.index,
            )
        if op.get("key"):
            return classify_ch_column_change(
                op["key"],
                change={"from": op.get("from_value"), "to": op.get("to_value")},
            )
    if kind in {"alter_ch_options", "alter_ch_table"}:
        from dbwarden.engine.backends.clickhouse.safety import (
            classify_ch_options_change,
        )

        if op.get("key"):
            return classify_ch_options_change(op["key"])
        return Safety.WARN
    if kind in _CRITICAL_OPS:
        return Safety.CRITICAL
    if kind in _WARN_OPS:
        return Safety.WARN
    if kind in _INFO_OPS:
        return Safety.INFO
    return Safety.UNKNOWN


def required_flags(ops: list[dict[str, Any]], backend: str = "") -> list[str]:
    return (
        ["--force"]
        if any(
            classify_operation(op, backend) in (Safety.WARN, Safety.CRITICAL)
            for op in ops
        )
        else []
    )


def _snapshot_column_type_signature(snapshot_column: dict[str, Any]) -> dict[str, Any]:
    extra_keys = {"length", "precision", "scale", "pg_type", "enum_name"}
    if not any(key in snapshot_column for key in extra_keys):
        from dbwarden.engine.snapshot.type_normalize import normalize_type

        return normalize_type(str(snapshot_column.get("type", "")))

    sig: dict[str, Any] = {"type": snapshot_column.get("type")}
    for key in ("length", "precision", "scale", "pg_type", "enum_name"):
        if key in snapshot_column:
            sig[key] = snapshot_column[key]
    return sig
