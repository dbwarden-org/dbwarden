from __future__ import annotations

import logging
from typing import Any

from dbwarden.engine.core.models import ModelTable
from dbwarden.engine.core.statement_order import _assemble_migration, MigrationStatement

from .sql_builders import _build_clickhouse_recreate_table_sql


IRREVERSIBLE_ANNOTATION = "dbwarden: irreversible"


class RollbackContractError(ValueError):
    """Raised when generated rollback SQL violates the rollback contract."""


_IRREVERSIBLE_ROLLBACK_OPS: frozenset[str] = frozenset({
    "alter_enum_add_value",
    "refresh_matview",
    "alter_pg_partition",
    "apply_data_op",
    "recreate_ch_table",
})


def _rollback_kind_from_sql(rollback_sql: str) -> str | None:
    sql = rollback_sql.strip().lower()
    if not sql.startswith("--"):
        return None
    if "irreversible" in sql:
        return "irreversible"
    if sql.startswith("-- no-op") or "nothing to roll back" in sql:
        return "no-op"
    placeholder_markers = (
        "cannot ",
        "manual",
        "no rollback",
        "revert",
        "inverse requires manual migration",
        "not yet implemented",
    )
    if any(marker in sql for marker in placeholder_markers):
        return "placeholder"
    return None


def _enforce_rollback_contract(
    stmt: MigrationStatement,
    op_type: str,
    target: str,
    rollback_kind: str,
    *,
    enforce_rollback_contract: bool,
    acknowledge_irreversible: bool = False,
) -> None:
    if not enforce_rollback_contract:
        return
    if rollback_kind == "placeholder":
        if acknowledge_irreversible:
            return
        raise RollbackContractError(
            f"Placeholder rollback for {op_type} on {target} is not allowed. "
            f"Provide executable rollback SQL or add '-- {IRREVERSIBLE_ANNOTATION}' "
            "and mark the migration irreversible."
        )
    if rollback_kind == "irreversible" and op_type not in _IRREVERSIBLE_ROLLBACK_OPS and not acknowledge_irreversible:
        raise RollbackContractError(
            f"Irreversible rollback for {op_type} on {target} is not in the known irreversible set. "
            f"Add '-- {IRREVERSIBLE_ANNOTATION}' only after accepting that this migration cannot roll back automatically."
        )
    if rollback_kind == "real" and stmt.rollback_sql.strip().startswith("--"):
        raise RollbackContractError(
            f"Rollback for {op_type} on {target} is classified real but emits a SQL comment."
        )


def snapshot_diff_to_sql(
    upgrade_ops: list[dict[str, Any]],
    rollback_ops: list[dict[str, Any]],
    database: str | None = None,
    db_name: str | None = None,
    safe_type_change: bool = False,
    concurrent: bool = True,
    postgres_auto_using: bool = False,
    enforce_rollback_contract: bool = False,
    acknowledge_irreversible: bool = False,
) -> tuple[str, str, list[Any]]:
    from dbwarden.engine.discovery import (
        generate_add_column_sql,
        generate_create_table_sql,
        generate_drop_object_sql,
        _qualified_name,
    )
    from dbwarden.engine.offline import reconstruct_model_table

    schemas: set[str] = set()
    for op in upgrade_ops:
        if "schema" in op and op["schema"]:
            schemas.add(op["schema"])
    if schemas:
        ext_statements = "\n".join(
            f'CREATE SCHEMA IF NOT EXISTS "{s.replace(chr(34), chr(34) + chr(34))}";'
            for s in sorted(schemas)
        )
        upgrade_ops = [{"type": "create_schema", "schema": s, "sql": None} for s in sorted(schemas)] + upgrade_ops
        rollback_ops = rollback_ops + [{"type": "drop_schema", "schema": s, "sql": None} for s in reversed(sorted(schemas))]
    from dbwarden.engine.migration_name import Change
    from dbwarden.engine.backends.postgresql.handlers import (
        ColumnHandler,
        ConstraintHandler,
        IndexHandler,
        PartitionHandler,
        PgTableHandler,
        RenameTableHandler,
        SchemaHandler,
        StatisticsHandler,
        TableHandler,
        TypeHandler,
        ViewHandler,
    )
    from dbwarden.engine.backends.clickhouse.handlers import (
        ChAggTargetHandler,
        ChColumnHandler,
        ChCommentHandler,
        ChDataOpHandler,
        ChDictionaryHandler,
        ChMaterializedViewHandler,
        ChProjectionHandler,
        ChSkipIndexHandler,
        ChTableHandler,
    )
    from dbwarden.engine.backends.mysql.handlers import MyTableHandler
    from dbwarden.engine.core.ordering import apply_public_ordering
    from dbwarden.engine.core.protocol import Op
    from dbwarden.plugin import ObjectPluginRegistry
    from dbwarden.logging import Verbosity, get_logger

    _emit_dispatch: dict[str, Any] = {}
    for _h in (
        ChTableHandler(),
        ChAggTargetHandler(),
        ColumnHandler(),
        ChColumnHandler(),
        ChCommentHandler(),
        ConstraintHandler(),
        ChDataOpHandler(),
        ChDictionaryHandler(),
        IndexHandler(),
        ChMaterializedViewHandler(),
        MyTableHandler(),
        PartitionHandler(),
        PgTableHandler(),
        ChProjectionHandler(),
        RenameTableHandler(),
        SchemaHandler(),
        ChSkipIndexHandler(),
        StatisticsHandler(),
        TableHandler(),
        TypeHandler(),
        ViewHandler(),
    ):
        for _ot in getattr(_h, "op_types", (_h.object_type,)):
            _emit_dispatch[_ot] = _h
    for _registration in ObjectPluginRegistry.handlers().values():
        _h = _registration.handler
        apply_public_ordering(_h)
        for _ot in getattr(_h, "op_types", (_h.object_type,)):
            _emit_dispatch[_ot] = _h

    _CHANGE_TABLE_KEY: dict[str, str] = {
        "rename_table": "old_table",
        "create_schema": "schema", "drop_schema": "schema",
        "create_domain": "name", "drop_domain": "name",
        "create_sequence": "name", "drop_sequence": "name",
        "alter_pg_storage_param": "table",
        "alter_pg_rls": "table",
        "add_policy": "table", "drop_policy": "table", "alter_policy": "table",
        "add_grant": "table", "revoke_grant": "table",
        "alter_enum_add_value": "enum_name", "create_type": "enum_name", "drop_type": "enum_name",
        "alter_my_table": "table",
        "alter_view": "table",
        "refresh_matview": "table",
        "create_table": "table",
        "drop_table": "table",
        "alter_table_comment": "table",
        "add_index": "table",
        "drop_index": "table",
        "alter_pg_table": "table",
        "add_exclude_constraint": "table",
        "drop_exclude_constraint": "table",
        "add_unique_constraint": "table",
        "drop_unique_constraint": "table",
        "rename_unique_constraint": "table",
        "add_check_constraint": "table",
        "drop_check_constraint": "table",
        "add_foreign_key": "table",
        "drop_foreign_key": "table",
        "alter_ch_options": "table",
        "recreate_ch_table": "table",
        "alter_ch_projection": "table",
        "alter_ch_skip_index": "table",
        "alter_ch_comment": "table",
        "modify_mv_query": "mv_name",
        "modify_mv_refresh": "mv_name",
        "alter_ch_dict": "name",
        "create_ch_agg_target": "name",
        "drop_ch_agg_target": "name",
        "apply_data_op": "name",
        "create_ch_named_collection": "name",
        "drop_ch_named_collection": "name",
        "alter_ch_named_collection": "name",
        "create_ch_settings_profile": "name",
        "drop_ch_settings_profile": "name",
        "alter_ch_settings_profile": "name",
        "create_ch_role": "name",
        "drop_ch_role": "name",
        "alter_ch_role": "name",
        "create_ch_user": "name",
        "drop_ch_user": "name",
        "alter_ch_user": "name",
        "create_ch_quota": "name",
        "drop_ch_quota": "name",
        "alter_ch_quota": "name",
        "create_ch_row_policy": "name",
        "drop_ch_row_policy": "name",
        "alter_ch_row_policy": "name",
        "alter_ch_grant": "name",
        "add_column": "table",
        "drop_column": "table",
        "rename_column": "table",
        "alter_column_type": "table",
        "alter_column_nullable": "table",
        "alter_column_autoincrement": "table",
        "alter_column_default": "table",
        "alter_column_comment": "table",
        "alter_ch_column": "table",
        "alter_pg_column_meta": "table",
        "alter_my_column_meta": "table",
        "create_composite_type": "type_name", "drop_composite_type": "type_name",
        "create_event_trigger": "trigger_name", "drop_event_trigger": "trigger_name",
        "create_extended_statistics": "stat_name", "drop_extended_statistics": "stat_name",
        "create_function": "function_name", "drop_function": "function_name",
        "create_role": "role_name", "drop_role": "role_name", "alter_role": "role_name",
        "alter_default_privileges": "schema",
    }
    _CHANGE_TARGET_KEY: dict[str, str] = {
        "rename_table": "new_table",
        "alter_pg_storage_param": "param",
        "add_policy": "name", "drop_policy": "name", "alter_policy": "name",
        "add_grant": "role", "revoke_grant": "role",
        "alter_enum_add_value": "value",
        "alter_my_table": "key",
        "alter_pg_table": "key",
        "add_index": "columns",
        "add_column": "column",
        "drop_column": "column",
        "alter_column_type": "column",
        "alter_column_nullable": "column",
        "alter_column_autoincrement": "column",
        "alter_column_default": "column",
        "alter_column_comment": "column",
        "alter_ch_column": "column",
        "alter_pg_column_meta": "column",
        "alter_my_column_meta": "column",
        "attach_partition": "partition_name", "detach_partition": "partition_name",
        "alter_trigger": "name",
        "alter_column_statistics": "column",
    }

    statements: list[MigrationStatement] = []
    changes: list[Any] = []
    logger = get_logger(debug_level=logging.getLogger("dbwarden").level)

    def _warn_about_rollback(stmt: MigrationStatement, op_type: str, target: str) -> str:
        inferred = _rollback_kind_from_sql(stmt.rollback_sql)
        rollback_kind = stmt.rollback_kind
        if rollback_kind == "real" and inferred:
            rollback_kind = inferred
        if rollback_kind == "placeholder" and op_type in _IRREVERSIBLE_ROLLBACK_OPS:
            rollback_kind = "irreversible"
        if rollback_kind == "irreversible":
            reason = stmt.rollback_reason or "rollback SQL is irreversible or not executable"
            logger.warning(
                f"Irreversible rollback for {op_type} on {target}: {reason}"
            )
        elif rollback_kind in ("conditional", "placeholder") and logger.verbosity >= Verbosity.VERBOSE:
            reason = stmt.rollback_reason or (
                "rollback is conditional" if rollback_kind == "conditional" else "rollback SQL is a placeholder or manual instruction"
            )
            logger.warning(
                f"{rollback_kind.title()} rollback for {op_type} on {target}: {reason}"
            )
        return rollback_kind

    if not concurrent:
        for op in upgrade_ops:
            if op["type"] in ("add_index", "drop_index"):
                op["concurrently"] = False
        for op in rollback_ops:
            if op["type"] in ("add_index", "drop_index"):
                op["concurrently"] = False

    for op in upgrade_ops:
        _handler = _emit_dispatch.get(op["type"])
        if _handler is not None:
            _ot = op["type"]
            _attrs: dict[str, Any] = {
                k: v for k, v in op.items()
                if k not in ("type", "__rollback_attrs", "__irreversible")
            }
            _rollback_attrs: dict[str, Any] = dict(op.get("__rollback_attrs") or {})
            if _ot in ("create_domain", "drop_domain"):
                _info: dict[str, Any] = {"type": op.get("domain_type", "text")}
                for _k in ("schema", "default", "not_null", "check"):
                    if op.get(_k) is not None:
                        _info[_k] = op[_k]
                _attrs = {"domain_name": op.get("name") or op.get("domain_name", ""), "domain_info": _info}
            elif _ot in ("create_sequence", "drop_sequence"):
                _info = {}
                for _k in ("increment", "minvalue", "maxvalue", "start", "cycle", "owned_by", "schema"):
                    if op.get(_k) is not None:
                        _info[_k] = op[_k]
                _attrs = {"seq_name": op.get("name") or op.get("seq_name", ""), "seq_info": _info}
            _op_obj = Op(
                object_type=_ot,
                upgrade_attrs=_attrs,
                rollback_attrs=_rollback_attrs,
                irreversible=bool(op.get("__irreversible", False)),
            )
            _table_key = _CHANGE_TABLE_KEY.get(_ot, "table")
            _table = op.get(_table_key) or op.get("name") or op.get("table", "")
            emitted = _handler.emit(_op_obj, db_name=db_name)
            if op.get("__irreversible"):
                for stmt in emitted:
                    stmt.rollback_kind = "irreversible"
                    stmt.rollback_reason = stmt.rollback_reason or op.get("rollback_reason")
            for stmt in emitted:
                rollback_kind = _warn_about_rollback(stmt, _ot, str(_table))
                _enforce_rollback_contract(
                    stmt,
                    _ot,
                    str(_table),
                    rollback_kind,
                    enforce_rollback_contract=enforce_rollback_contract,
                    acknowledge_irreversible=acknowledge_irreversible,
                )
            statements.extend(emitted)
            _change: dict[str, Any] = {"operation": _ot, "table": _table}
            _target_key = _CHANGE_TARGET_KEY.get(_ot)
            if _target_key and op.get(_target_key) is not None:
                _change["target"] = op[_target_key]
            changes.append(Change(**_change))
            if _ot == "add_index":
                changes[-1] = Change(
                    operation="add_index", table=_table,
                    target=",".join(op.get("columns", [])),
                    index_type=op.get("using"),
                )
            if _ot == "rename_column":
                changes[-1] = Change(
                    operation="rename_column", table=_table,
                    target=op.get("new_name", ""),
                    resolved_from=op.get("resolved_from"),
                )
            if _ot == "add_foreign_key":
                changes[-1] = Change(
                    operation="add_foreign_key", table=_table,
                    target=f"{op.get('referenced_table', '')}({','.join(op.get('referenced_columns', []))})",
                )
            if _ot == "create_table":
                _ct_table = _find_model_table(_table, db_name=db_name)
                if _ct_table and hasattr(_ct_table, 'indexes') and _ct_table.indexes:
                    for _idx in _ct_table.indexes:
                        changes.append(Change(
                            operation="add_index", table=_table,
                            target=",".join(_idx.columns), index_type=_idx.using,
                        ))
            if _ot == "rename_table":
                changes[-1] = Change(
                    operation="rename_table", table=_table,
                    target=op.get("new_table", ""),
                    resolved_from=op.get("resolved_from"),
                )
            continue

    upgrade_sql, rollback_sql = _assemble_migration(statements)
    return upgrade_sql, rollback_sql, changes


def _find_model_table(table_name: str, db_name: str | None = None) -> ModelTable | None:
    from dbwarden.config import get_database
    from dbwarden.engine.discovery import get_model_table_by_name

    config = get_database(db_name)
    model_paths = config.model_paths
    if model_paths is None:
        from dbwarden.engine.discovery import auto_discover_model_paths
        model_paths = auto_discover_model_paths()
    if not model_paths:
        return None

    if config.model_tables is not None and table_name not in config.model_tables:
        return None
    return get_model_table_by_name(table_name, model_paths, db_name=db_name)
