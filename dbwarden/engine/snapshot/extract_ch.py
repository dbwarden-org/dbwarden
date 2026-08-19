from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text

from dbwarden.databases.clickhouse.engine import ChEngineSpec

from dbwarden.engine.backends.clickhouse.parse import (
    parse_dict_layout as _parse_clickhouse_dict_layout,
    parse_dict_lifetime as _parse_clickhouse_dict_lifetime,
    parse_dict_primary_key as _parse_clickhouse_dict_primary_key,
    parse_dict_source as _parse_clickhouse_dict_source,
    parse_mv_query as _parse_clickhouse_mv_query,
    parse_mv_to_table as _parse_clickhouse_mv_to_table,
    parse_projection_queries as _parse_clickhouse_projection_queries,
    parse_replica_name as _parse_clickhouse_replica_name,
    parse_settings as _parse_clickhouse_settings,
    parse_ttl_expressions as _parse_clickhouse_ttl_expressions,
    parse_tuple_or_list as _clickhouse_tuple_or_list,
    parse_zookeeper_path as _parse_clickhouse_zookeeper_path,
)

from dbwarden.engine.backends.clickhouse.parse import parse_mv_refresh as _parse_clickhouse_mv_refresh
from dbwarden.engine.backends.clickhouse.render import _parse_ch_type_wrappers

from .ch_utils import _clean_clickhouse_expression, _pick_clickhouse_codec, _serialize_clickhouse_engine


def _extract_setting_defaults(connection: Any) -> dict[str, str]:
    """Snapshot MergeTree setting defaults so canonicalization works offline.

    Canonicalization runs in both online and offline pipelines; the offline
    one has no connection.  Persisting the defaults map at extract time lets
    both sides strip never-declared defaults without a live server, and
    survives ClickHouse changing a default in a future version.
    """
    from sqlalchemy import text
    rows = connection.execute(
        text("SELECT name, value FROM system.merge_tree_settings WHERE changed = 0")
    ).fetchall()
    return {row.name: str(row.value) for row in rows}


def _extract_clickhouse_schema_snapshot(connection: Any, db_name: str) -> dict[str, Any]:
    from dbwarden.databases.clickhouse.engine import ChEngineSpec

    tables: dict[str, Any] = {}
    enums: dict[str, Any] = {}
    indexes: dict[str, Any] = {}
    constraints: dict[str, Any] = {}

    named_collections: dict[str, Any] = {}
    try:
        nc_rows = connection.execute(
            text("SELECT name, collection FROM system.named_collections")
        ).fetchall()
        for row in nc_rows:
            entry = dict(row.collection) if row.collection else {}
            named_collections[row.name] = {"name": row.name, "entries": entry}
    except Exception:
        pass

    roles: dict[str, Any] = {}
    try:
        role_rows = connection.execute(
            text("SELECT name, storage FROM system.roles")
        ).fetchall()
        for row in role_rows:
            roles[row.name] = {"name": row.name, "storage": row.storage, "settings": {}}
    except Exception:
        pass

    users: dict[str, Any] = {}
    try:
        user_rows = connection.execute(
            text("SELECT name, storage, auth_type, host_ip, host_names, host_regexp, host_like, "
                 "default_roles, settings_profile, grantees FROM system.users")
        ).fetchall()
        for row in user_rows:
            users[row.name] = {
                "name": row.name, "storage": row.storage, "auth": str(getattr(row, "auth_type", "")),
                "host": "ANY", "roles": [], "default_roles": [], "settings_profile": None,
            }
    except Exception:
        pass

    settings_profiles: dict[str, Any] = {}
    try:
        sp_rows = connection.execute(
            text("SELECT name, storage, settings, to_roles FROM system.settings_profiles")
        ).fetchall()
        for row in sp_rows:
            settings_profiles[row.name] = {
                "name": row.name, "storage": row.storage,
                "settings": dict(row.settings) if row.settings else {},
                "to_roles": list(row.to_roles) if row.to_roles else [],
            }
    except Exception:
        pass

    quotas: dict[str, Any] = {}
    try:
        quota_rows = connection.execute(
            text("SELECT name, storage, interval, queries, errors, result_rows, read_rows, "
                 "execution_time FROM system.quotas")
        ).fetchall()
        for row in quota_rows:
            quotas[row.name] = {
                "name": row.name, "storage": row.storage,
                "interval": str(getattr(row, "interval", "")),
                "limits": {"queries": getattr(row, "queries", 0), "errors": getattr(row, "errors", 0)},
                "to_roles": [],
            }
    except Exception:
        pass

    row_policies: dict[str, Any] = {}
    try:
        rp_rows = connection.execute(
            text("SELECT name, short_name, storage, database, table, select_filter, "
                 "is_permissive, roles FROM system.row_policies")
        ).fetchall()
        for row in rp_rows:
            row_policies[row.name] = {
                "name": row.name, "table": f"{row.database}.{row.table}",
                "using": getattr(row, "select_filter", "") or "",
                "to_roles": list(row.roles) if row.roles else ["ALL"],
                "permissive": bool(getattr(row, "is_permissive", True)),
            }
    except Exception:
        pass

    grants: dict[str, Any] = {}
    try:
        grant_rows = connection.execute(
            text("SELECT user_name, role_name, access_type, database, table, "
                 "is_partial_revoke, grant_option FROM system.grants")
        ).fetchall()
        for row in grant_rows:
            entity = row.user_name or row.role_name or ""
            on = f"{row.database}.{row.table}" if row.database and row.table else row.database or "*.*"
            key = f"{entity}:{on}"
            if key not in grants:
                grants[key] = {"privileges": [], "on": on, "to": entity, "with_grant_option": False}
            grants[key]["privileges"].append(row.access_type)
            if row.grant_option:
                grants[key]["with_grant_option"] = True
    except Exception:
        pass

    table_rows = connection.execute(
        text(
            "SELECT name, engine, engine_full, sorting_key, primary_key, partition_key, "
            "sampling_key, create_table_query, comment "
            "FROM system.tables WHERE database = currentDatabase()"
        )
    ).fetchall()

    column_rows = connection.execute(
        text(
            "SELECT table, name, type, default_kind, default_expression, compression_codec AS codec_expression, "
            "comment, is_in_primary_key, is_in_sorting_key, is_in_partition_key "
            "FROM system.columns WHERE database = currentDatabase()"
        )
    ).fetchall()

    # ttl_expression was added to system.columns in CH 24.4
    # Query it separately so older versions don't break
    ttl_by_column: dict[tuple[str, str], str | None] = {}
    try:
        ttl_rows = connection.execute(
            text(
                "SELECT table, name, ttl_expression FROM system.columns "
                "WHERE database = currentDatabase() AND ttl_expression != ''"
            )
        ).fetchall()
        for row in ttl_rows:
            ttl_by_column[(row.table, row.name)] = row.ttl_expression
    except Exception:
        pass

    index_rows = connection.execute(
        text(
            "SELECT table, name, type, expr, granularity FROM system.data_skipping_indices "
            "WHERE database = currentDatabase()"
        )
    ).fetchall()

    columns_by_table: dict[str, list[dict[str, Any]]] = {}
    for row in column_rows:
        columns_by_table.setdefault(row.table, []).append({
            "name": row.name,
            "type": row.type,
            "default_kind": getattr(row, "default_kind", None),
            "default_expression": getattr(row, "default_expression", None),
            "codec_expression": getattr(row, "codec_expression", None),
            "ttl_expression": ttl_by_column.get((row.table, row.name)),
            "comment": getattr(row, "comment", None),
            "is_in_primary_key": bool(getattr(row, "is_in_primary_key", False)),
            "is_in_sorting_key": bool(getattr(row, "is_in_sorting_key", False)),
            "is_in_partition_key": bool(getattr(row, "is_in_partition_key", False)),
        })

    indexes_by_table: dict[str, list[dict[str, Any]]] = {}
    for row in index_rows:
        indexes_by_table.setdefault(row.table, []).append({
            "name": row.name,
            "type": row.type,
            "expr": row.expr,
            "granularity": row.granularity,
        })

    for row in table_rows:
        table_name = row.name
        create_query = getattr(row, "create_table_query", "") or ""
        engine_name = getattr(row, "engine", "") or ""
        engine_full = getattr(row, "engine_full", "") or ""
        comment = getattr(row, "comment", None) or None

        if engine_name.upper() == "DICTIONARY":
            object_type = "dictionary"
        elif create_query.strip().upper().startswith("CREATE MATERIALIZED VIEW"):
            object_type = "materialized_view"
        else:
            object_type = "table"

        if object_type == "materialized_view":
            mv_eng_match = re.search(
                r'ENGINE\s*=\s*(\w+(?:\s*\([^)]*\))?)',
                create_query,
            )
            if mv_eng_match:
                ch_engine = ChEngineSpec.from_engine_string(mv_eng_match.group(1))
            else:
                ch_engine = None
        elif engine_full and re.match(r'\w+\s*\(', engine_full):
            ch_engine = ChEngineSpec.from_engine_string(engine_full)
        else:
            ch_engine = ChEngineSpec(engine_name) if engine_name else None

        ch_engine_serialized = _serialize_clickhouse_engine(ch_engine)

        if object_type == "materialized_view":
            _mv_order_match = re.search(
                r'ORDER\s+BY\s+(.+?)(?:\s+SETTINGS|\s+TTL|\s+AS\s+SELECT|\s*$)',
                create_query, re.IGNORECASE | re.DOTALL
            )
            if _mv_order_match:
                sorting_key = _clickhouse_tuple_or_list(_mv_order_match.group(1).strip())
            else:
                sorting_key = _clickhouse_tuple_or_list(getattr(row, "sorting_key", None))
        else:
            sorting_key = _clickhouse_tuple_or_list(getattr(row, "sorting_key", None))
        primary_key = _clickhouse_tuple_or_list(getattr(row, "primary_key", None))
        partition_key = _clean_clickhouse_expression(getattr(row, "partition_key", None))
        sampling_key = _clean_clickhouse_expression(getattr(row, "sampling_key", None))

        ch_options: dict[str, Any] = {
            "ch_engine_raw": ch_engine.to_dict() if ch_engine else None,
            "ch_engine": ch_engine_serialized,
            "ch_order_by": sorting_key,
            "ch_primary_key": primary_key,
            "ch_partition_by": partition_key,
            "ch_sample_by": sampling_key,
            "ch_ttl": _parse_clickhouse_ttl_expressions(create_query),
            "ch_settings": _parse_clickhouse_settings(create_query),
            "ch_object_type": object_type,
            "ch_select_statement": _parse_clickhouse_mv_query(create_query),
            "ch_to_table": _parse_clickhouse_mv_to_table(create_query),
            "ch_refresh": _parse_clickhouse_mv_refresh(create_query) if object_type == "materialized_view" else None,
            "ch_dictionary": object_type == "dictionary",
            "ch_dict_layout": _parse_clickhouse_dict_layout(create_query),
            "ch_dict_source": _parse_clickhouse_dict_source(create_query),
            "ch_dict_lifetime": _parse_clickhouse_dict_lifetime(create_query),
            "ch_dict_primary_key": _parse_clickhouse_dict_primary_key(create_query),
            "ch_projections": _parse_clickhouse_projection_queries(create_query),
            "ch_zookeeper_path": _parse_clickhouse_zookeeper_path(create_query, engine_name),
            "ch_replica_name": _parse_clickhouse_replica_name(create_query, engine_name),
        }

        columns_dict: dict[str, Any] = {}
        for col in columns_by_table.get(table_name, []):
            raw_type = col["type"]
            _, ch_nullable, ch_low_cardinality = _parse_ch_type_wrappers(str(raw_type))
            default_kind = col.get("default_kind")
            default_expression = col.get("default_expression")
            ch_column: dict[str, Any] = {
                "ch_codec": _pick_clickhouse_codec(col.get("codec_expression")),
                "ch_default_expression": default_expression if default_kind == "DEFAULT" else None,
                "ch_materialized": default_expression if default_kind == "MATERIALIZED" else None,
            "ch_alias": default_expression if default_kind == "ALIAS" else None,
            "ch_ephemeral": default_expression if default_kind == "EPHEMERAL" else None,
            "ch_ttl": col.get("ttl_expression"),
            "ch_low_cardinality": ch_low_cardinality,
                "ch_nullable": ch_nullable,
                "ch_type": raw_type,
            }

            columns_dict[col["name"]] = {
                "type": raw_type,
                "nullable": ch_nullable,
                "primary_key": bool(col.get("is_in_primary_key", False)),
                "default": default_expression if default_kind == "DEFAULT" else None,
                "comment": col.get("comment"),
                "ch_column": ch_column,
            }

        table_entry: dict[str, Any] = {
            "object_type": object_type,
            "columns": columns_dict,
            "primary_key": [c for c in (primary_key or [])] if isinstance(primary_key, list) else ([primary_key] if isinstance(primary_key, str) and primary_key else []),
            "comment": comment,
            "indexes": indexes_by_table.get(table_name, []),
            "ch_options": ch_options,
        }
        tables[table_name] = table_entry

    return {
        "format_version": 1,
        "migration_id": "",
        "database_name": db_name,
        "database_type": "clickhouse",
        "applied_at": "",
        "tables": tables,
        "enums": enums,
        "indexes": indexes,
        "constraints": constraints,
        "named_collections": named_collections,
        "roles": roles,
        "users": users,
        "settings_profiles": settings_profiles,
        "quotas": quotas,
        "row_policies": row_policies,
        "grants": grants,
        "ch_setting_defaults": _extract_setting_defaults(connection),
    }
