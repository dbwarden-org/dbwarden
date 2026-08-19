from __future__ import annotations

import re
from typing import Any

from dbwarden.engine.backends.postgresql.extract import (
    _is_autoincrement,
    _strip_pg_expr_parens,
)
from dbwarden.engine.backends.postgresql.render import _is_expression
from dbwarden.engine.backends.postgresql.sql_build import _build_pg_meta_sql

from .extract_ch import _extract_clickhouse_schema_snapshot
from .type_normalize import normalize_type


def extract_full_schema_snapshot(
    database: str | None = None,
    sqlalchemy_url: str | None = None,
    database_type: str | None = None,
) -> dict[str, Any]:
    from sqlalchemy import inspect, text

    if database_type is None:
        try:
            from dbwarden.config import get_database
            database_type = get_database(database).database_type
        except Exception:
            pass

    if database is None:
        try:
            from dbwarden.config import get_multi_db_config
            db_name = get_multi_db_config().default
        except Exception:
            db_name = "default"
    else:
        db_name = database

    if database_type == "clickhouse":
        if sqlalchemy_url is not None:
            from sqlalchemy import create_engine

            engine = create_engine(sqlalchemy_url)
            try:
                with engine.connect() as connection:
                    return _extract_clickhouse_schema_snapshot(connection, db_name)
            finally:
                engine.dispose()

        from dbwarden.database.connection import get_db_connection

        conn_context = get_db_connection(database)
        connection = conn_context.__enter__()
        try:
            return _extract_clickhouse_schema_snapshot(connection, db_name)
        finally:
            conn_context.__exit__(None, None, None)

    if sqlalchemy_url is not None and database_type is not None:
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool
        engine = create_engine(sqlalchemy_url, poolclass=NullPool)
        inspector = inspect(engine)
        own_engine = True
    else:
        from dbwarden.database.connection import get_db_connection
        conn_context = get_db_connection(database)
        connection = conn_context.__enter__()
        try:
            inspector = inspect(connection)
        except Exception:
            conn_context.__exit__(None, None, None)
            raise
        engine = None
        own_engine = False
        from dbwarden.config import get_database
        database_type = get_database(database).database_type

    tables: dict[str, Any] = {}
    enums: dict[str, Any] = {}
    domains: dict[str, Any] = {}
    indexes: dict[str, Any] = {}
    constraints: dict[str, Any] = {}
    sequences: dict[str, Any] = {}
    composite_types: dict[str, Any] = {}
    functions: dict[str, Any] = {}
    roles: dict[str, Any] = {}
    default_privileges: dict[str, Any] = {}
    schema_grants: dict[str, Any] = {}
    event_triggers: dict[str, Any] = {}
    extended_stats: dict[str, Any] = {}

    pg_schema = None
    pg_version: tuple[int, int] | None = None
    if database_type == "postgresql":
        try:
            from dbwarden.config import get_database
            pg_schema = get_database(database).postgres_schema
        except Exception:
            pass
        try:
            _vc = engine.connect() if own_engine and engine is not None else connection
            ver_row = _vc.execute(text("SELECT current_setting('server_version_num')")).scalar()
            if ver_row:
                ver_int = int(ver_row)
                pg_version = (ver_int // 10000, (ver_int // 100) % 100)
            if own_engine and engine is not None:
                _vc.close()
        except Exception:
            pass

    inspect_kw = {"schema": pg_schema} if pg_schema else {}
    table_names = inspector.get_table_names(**inspect_kw)
    for table_name in table_names:
        _regclass_name = f'"{pg_schema}"."{table_name}"' if pg_schema else f'"{table_name}"'
        columns_info = inspector.get_columns(table_name, **inspect_kw)
        pk_info = inspector.get_pk_constraint(table_name, **inspect_kw)

        if database_type == "postgresql":
            try:
                _pg_conn = engine.connect() if own_engine and engine is not None else connection
                local_rows = _pg_conn.execute(
                    text(
                        "SELECT attname FROM pg_attribute "
                        "WHERE attrelid = CAST(:t AS regclass) "
                        "AND attnum > 0 AND NOT attisdropped AND attislocal"
                    ),
                    {"t": _regclass_name},
                ).fetchall()
                local_columns = {r[0] for r in local_rows}
                if local_columns:
                    columns_info = [col for col in columns_info if col.get("name") in local_columns]
                if own_engine:
                    _pg_conn.close()
            except Exception:
                pass

        pk_columns = set(pk_info.get("constrained_columns", []) or [])

        columns_dict: dict[str, Any] = {}
        for col in columns_info:
            col_name = col["name"]
            col_type = col.get("type", "")
            raw_type_str = str(col_type)
            normalized = normalize_type(raw_type_str)
            col_type_name = normalized["type"]
            is_pk = col_name in pk_columns
            col_entry: dict[str, Any] = {
                "type": col_type_name,
                "nullable": bool(col.get("nullable", True)),
                "primary_key": is_pk,
                "default": col.get("default"),
                "autoincrement": _is_autoincrement(col),
            }
            if normalized.get("raw"):
                col_entry["raw"] = True
            if "length" in normalized:
                col_entry["length"] = normalized["length"]
            if "precision" in normalized:
                col_entry["precision"] = normalized["precision"]
            if "scale" in normalized:
                col_entry["scale"] = normalized["scale"]

            if hasattr(col_type, "enums") and col_type.enums:
                enum_values = ", ".join(repr(v) for v in col_type.enums)
                col_entry["type"] = f"enum({enum_values})"

            comment = col.get("comment")
            if comment is not None:
                col_entry["comment"] = comment

            if database_type == "postgresql":
                if hasattr(col_type, "enums") and col_type.enums and hasattr(col_type, "name") and col_type.name:
                    col_entry["type"] = "enum"
                    col_entry["enum_name"] = col_type.name
                    col_entry["pg_type"] = {
                        "kind": "enum",
                        "type_name": col_type.name,
                        "values": list(col_type.enums),
                    }
                enum_match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)$", raw_type_str)
                if enum_match and engine is not None:
                    try:
                        with engine.connect() as conn:
                            enum_row = conn.execute(
                                text("SELECT t.oid, t.typname FROM pg_type t JOIN pg_enum e ON t.oid = e.enumtypid WHERE t.typname = :tname LIMIT 1"),
                                {"tname": raw_type_str},
                            ).fetchone()
                            if enum_row:
                                col_entry["type"] = "enum"
                                col_entry["enum_name"] = raw_type_str
                                val_rows = conn.execute(
                                    text("SELECT enumlabel FROM pg_enum WHERE enumtypid = :oid ORDER BY enumsortorder"),
                                    {"oid": enum_row[0]},
                                ).fetchall()
                                col_entry["pg_type"] = {
                                    "kind": "enum",
                                    "type_name": raw_type_str,
                                    "values": [r[0] for r in val_rows],
                                }
                    except Exception:
                        pass

                pg_column: dict[str, Any] = {}
                if col.get("identity"):
                    identity = col["identity"]
                    pg_column["identity"] = "always" if identity.get("always") else "by_default"
                    if identity.get("start") is not None:
                        pg_column["identity_start"] = identity["start"]
                    if identity.get("increment") is not None:
                        pg_column["identity_increment"] = identity["increment"]
                    if identity.get("minvalue") is not None:
                        pg_column["identity_min"] = identity["minvalue"]
                    if identity.get("maxvalue") is not None:
                        pg_column["identity_max"] = identity["maxvalue"]
                type_obj = col["type"]
                collation = getattr(type_obj, "collation", None)
                if collation:
                    pg_column["collation"] = collation
                if col.get("computed"):
                    pg_column["generated"] = col["computed"].get("sqltext", "")

                type_str_lower = raw_type_str.lower()
                if getattr(type_obj, "item_type", None) is not None:
                    col_entry["pg_type"] = {"kind": "array", "inner": str(type_obj.item_type), "dimensions": 1}
                    col_entry["type"] = "array"
                elif type_str_lower in ("tsvector",):
                    regconfig = getattr(type_obj, "regconfig", None)
                    pg_type_entry: dict[str, Any] = {"kind": "tsvector"}
                    if regconfig:
                        pg_type_entry["config"] = str(regconfig)
                    col_entry["pg_type"] = pg_type_entry
                    col_entry["type"] = "tsvector"
                elif type_str_lower == "jsonb":
                    col_entry["pg_type"] = {"kind": "jsonb"}
                elif normalized.get("has_timezone") and col_entry.get("type") == "timestamp":
                    col_entry["pg_type"] = {"kind": "timestamptz"}
                elif col_entry.get("enum_name"):
                    pass
                else:
                    try:
                        if engine is not None:
                            with engine.connect() as _c:
                                range_row = _c.execute(
                                    text("SELECT rngtypid::regtype::text FROM pg_range WHERE rngtypid = (SELECT oid FROM pg_type WHERE typname = :t)"),
                                    {"t": raw_type_str.lower()},
                                ).fetchone()
                                if range_row:
                                    col_entry["pg_type"] = {"kind": "range", "range_type": raw_type_str}
                                    col_entry["type"] = raw_type_str
                    except Exception:
                        pass

                if pg_column:
                    col_entry["pg_column"] = pg_column

            if database_type in ("mysql", "mariadb"):
                my_column: dict[str, Any] = {}
                type_obj = col.get("type")
                charset = getattr(type_obj, "charset", None)
                collation = getattr(type_obj, "collation", None)
                unsigned = bool(getattr(type_obj, "unsigned", False))
                if charset:
                    my_column["my_charset"] = charset
                if collation:
                    my_column["my_collate"] = collation
                if unsigned:
                    my_column["my_unsigned"] = True
                if my_column:
                    col_entry["my_column"] = my_column

            columns_dict[col_name] = col_entry

        table_entry: dict[str, Any] = {
            "columns": columns_dict,
            "primary_key": list(pk_columns) if pk_columns else [],
            "comment": None,
            "schema": pg_schema,
        }

        if database_type == "postgresql":
            try:
                table_comment = inspector.get_table_comment(table_name, **inspect_kw)
                if table_comment and table_comment.get("text"):
                    table_entry["comment"] = table_comment["text"]
            except Exception:
                pass

            _conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT") if own_engine and engine is not None else connection
            try:
                pg_table: dict[str, Any] = {"backend": "postgresql"}

                try:
                    rows = _conn.execute(
                        text("SELECT unnest(COALESCE(reloptions, '{}')) FROM pg_class WHERE oid = CAST(:t AS regclass)"),
                        {"t": _regclass_name},
                    ).fetchall()
                    params: dict[str, Any] = {}
                    storage_params: dict[str, Any] = {}
                    for row in rows:
                        kv = row[0].split("=", 1)
                        if len(kv) == 2:
                            key = f"pg_{kv[0]}"
                            val: Any = kv[1]
                            if val.isdigit():
                                val = int(val)
                            params[key] = val
                            storage_params[kv[0]] = val
                    if params:
                        pg_table.update(params)
                    if storage_params:
                        pg_table["pg_storage_params"] = storage_params
                except Exception:
                    pass

                try:
                    toast_sql = (
                        "SELECT unnest(COALESCE("
                        "(SELECT reloptions FROM pg_class WHERE oid = c.reltoastrelid), '{}'"
                        ")) FROM pg_class c WHERE c.oid = CAST(:t AS regclass)"
                    )
                    toast_rows = _conn.execute(
                        text(toast_sql),
                        {"t": _regclass_name},
                    ).fetchall()
                    toast_params: dict[str, Any] = {}
                    for row in toast_rows:
                        kv = row[0].split("=", 1)
                        if len(kv) == 2:
                            key = f"toast.{kv[0]}"
                            val: Any = kv[1]
                            if val.isdigit():
                                val = int(val)
                            toast_params[key] = val
                    if toast_params:
                        existing = pg_table.get("pg_storage_params", {})
                        existing.update(toast_params)
                        pg_table["pg_storage_params"] = existing
                except Exception:
                    pass

                try:
                    row = _conn.execute(
                        text("SELECT relpersistence FROM pg_class WHERE oid = CAST(:t AS regclass)"),
                        {"t": _regclass_name},
                    ).fetchone()
                    if row and row[0] == 'u':
                        pg_table["pg_unlogged"] = True
                except Exception:
                    pass

                try:
                    row = _conn.execute(
                        text(
                            "SELECT spcname FROM pg_tablespace t "
                            "JOIN pg_class c ON c.reltablespace = t.oid "
                            "WHERE c.oid = CAST(:t AS regclass)"
                        ),
                        {"t": _regclass_name},
                    ).fetchone()
                    if row:
                        pg_table["pg_tablespace"] = row[0]
                except Exception:
                    pass

                try:
                    rows = _conn.execute(
                        text("""
                            SELECT p_ns.nspname AS parent_schema, p.relname AS parent_name
                            FROM pg_inherits i
                            JOIN pg_class c ON c.oid = i.inhrelid
                            JOIN pg_namespace c_ns ON c_ns.oid = c.relnamespace
                            JOIN pg_class p ON p.oid = i.inhparent
                            JOIN pg_namespace p_ns ON p_ns.oid = p.relnamespace
                            WHERE c.oid = CAST(:t AS regclass)
                        """),
                        {"t": _regclass_name},
                    ).fetchall()
                    parents = [r[1] for r in rows]
                    part_child = _conn.execute(
                        text(
                            "SELECT parent.relname AS parent_name, pg_get_expr(child.relpartbound, child.oid) AS bound "
                            "FROM pg_inherits i "
                            "JOIN pg_class child ON child.oid = i.inhrelid "
                            "JOIN pg_class parent ON parent.oid = i.inhparent "
                            "WHERE child.oid = CAST(:t AS regclass) AND child.relpartbound IS NOT NULL"
                        ),
                        {"t": _regclass_name},
                    ).fetchone()
                    if part_child:
                        pg_table["pg_partition_of"] = part_child.parent_name
                        pg_table["pg_partition_bound"] = part_child.bound
                    elif len(parents) == 1:
                        pg_table["pg_inherits"] = parents[0]
                    elif parents:
                        pg_table["pg_inherits"] = parents
                except Exception:
                    pass

                try:
                    part_row = _conn.execute(
                        text(
                            "SELECT p.partstrat, "
                            "array_agg(a.attname ORDER BY a.attnum) AS part_columns, "
                            "pg_get_expr(p.partexprs, p.partrelid) AS part_expr "
                            "FROM pg_partitioned_table p "
                            "JOIN pg_class c ON c.oid = p.partrelid "
                            "LEFT JOIN pg_attribute a ON a.attrelid = p.partrelid AND a.attnum = ANY(p.partattrs) "
                            "WHERE c.oid = CAST(:t AS regclass) "
                            "GROUP BY p.partstrat, p.partexprs, p.partrelid"
                        ),
                        {"t": _regclass_name},
                    ).fetchone()
                    if part_row:
                        strat_map = {"r": "RANGE", "l": "LIST", "h": "HASH"}
                        strategy = strat_map.get(part_row[0], part_row[0])
                        part_columns = list(part_row[1] or [])
                        part_expr = part_row[2]
                        if part_expr:
                            part_columns.append(part_expr.strip())
                        pg_table["pg_partition"] = {
                            "strategy": strategy,
                            "columns": part_columns,
                        }
                except Exception:
                    pass

                try:
                    rows = _conn.execute(
                        text(
                            "SELECT conname, pg_get_constraintdef(oid) AS definition "
                            "FROM pg_constraint "
                            "WHERE conrelid = CAST(:t AS regclass) AND contype = 'x'"
                        ),
                        {"t": _regclass_name},
                    ).fetchall()
                    excludes = [{"name": r[0], "expression": r[1]} for r in rows]
                    if excludes:
                        pg_table["pg_excludes"] = excludes
                except Exception:
                    pass

                try:
                    child_rows = _conn.execute(
                        text("SELECT n.nspname, c.relname, pg_get_expr(c.relpartbound, c.oid) AS bound "
                             "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                             "JOIN pg_inherits i ON i.inhrelid = c.oid "
                             "WHERE i.inhparent = CAST(:t AS regclass) AND c.relispartition = true "
                             "ORDER BY c.relname"),
                        {"t": _regclass_name},
                    ).fetchall()
                    if child_rows:
                        children = [
                            {"name": r[1] if not pg_schema or r[0] == pg_schema else f"{r[0]}.{r[1]}", "bound": r[2]}
                            for r in child_rows
                        ]
                        pg_table["pg_partitions"] = children
                except Exception as e:
                    import logging
                    logging.getLogger('dbwarden.snapshot').warning('child partitions extraction failed for %s: %s', _regclass_name, e)
                    try:
                        _conn.rollback()
                    except Exception:
                        pass

                try:
                    if pg_version and pg_version >= (14, 0):
                        attr_cols = "a.attname, a.attstorage, a.attcompression, a.attstattarget"
                    else:
                        attr_cols = "a.attname, a.attstorage, NULL::text AS attcompression, a.attstattarget"
                    rows = _conn.execute(
                        text(
                            f"SELECT {attr_cols} "
                            "FROM pg_attribute a "
                            "WHERE a.attrelid = CAST(:t AS regclass) AND a.attnum > 0 AND NOT a.attisdropped "
                            "ORDER BY a.attnum"
                        ),
                        {"t": _regclass_name},
                    ).fetchall()
                    storage_map = {'p': 'PLAIN', 'm': 'MAIN', 'e': 'EXTERNAL', 'x': 'EXTENDED'}
                    for r in rows:
                        cname = r[0]
                        if cname in columns_dict:
                            pg_col = columns_dict[cname].get("pg_column", {})
                            if isinstance(pg_col, dict):
                                storage = storage_map.get(r[1], r[1]) if r[1] else None
                                if storage and storage not in ("PLAIN", "EXTENDED"):
                                    pg_col["storage"] = storage
                                if r[2]:
                                    pg_col["compression"] = r[2]
                                if r[3] is not None and r[3] != -1:
                                    pg_col["statistics"] = r[3]
                                if pg_col:
                                    columns_dict[cname]["pg_column"] = pg_col
                except Exception as e:
                    import logging
                    logging.getLogger('dbwarden.snapshot').warning('attstats extraction failed for %s: %s', _regclass_name, e)
                    try:
                        _conn.rollback()
                    except Exception:
                        pass

                try:
                    row = _conn.execute(
                        text("SELECT relrowsecurity FROM pg_class WHERE oid = CAST(:t AS regclass)"),
                        {"t": _regclass_name},
                    ).fetchone()
                    if row and row[0]:
                        pg_table["pg_rls"] = True
                except Exception:
                    pass

                try:
                    row = _conn.execute(
                        text("SELECT relforcerowsecurity FROM pg_class WHERE oid = CAST(:t AS regclass)"),
                        {"t": _regclass_name},
                    ).fetchone()
                    if row and row[0]:
                        pg_table["pg_rls_force"] = True
                except Exception as e:
                    import logging
                    logging.getLogger('dbwarden.snapshot').warning('rls_force extraction failed for %s: %s', _regclass_name, e)
                    try:
                        _conn.rollback()
                    except Exception:
                        pass

                try:
                    policy_rows = _conn.execute(
                        text(
                            "SELECT policyname, permissive, cmd, roles, qual, with_check "
                            "FROM pg_policies "
                            "WHERE schemaname = :schema AND tablename = :table "
                            "ORDER BY policyname"
                        ),
                        {"schema": pg_schema or "public", "table": table_name},
                    ).fetchall()
                    if policy_rows:
                        policies = []
                        for r in policy_rows:
                            roles = list(r[3]) if r[3] else ["PUBLIC"]
                            policy_entry = {
                                "name": r[0],
                                "permissive": "PERMISSIVE" if r[1] else "RESTRICTIVE",
                                "command": r[2] or "ALL",
                                "role": roles[0] if len(roles) == 1 else roles,
                                "using": _strip_pg_expr_parens(r[4]),
                            }
                            if r[5]:
                                policy_entry["with_check"] = _strip_pg_expr_parens(r[5])
                            policies.append(policy_entry)
                        table_entry["pg_policies"] = policies
                except Exception:
                    pass

                try:
                    grant_rows = _conn.execute(
                        text(
                            "SELECT COALESCE(r.rolname, 'PUBLIC') AS grantee, "
                            "array_agg(acl.privilege_type ORDER BY acl.privilege_type) AS privileges, "
                            "bool_or(acl.is_grantable) AS grantable "
                            "FROM pg_class c "
                            "CROSS JOIN LATERAL aclexplode(c.relacl) AS acl "
                            "LEFT JOIN pg_roles r ON r.oid = acl.grantee "
                            "WHERE c.oid = CAST(:t AS regclass) "
                            "AND c.relacl IS NOT NULL "
                            "AND acl.grantee <> c.relowner "
                            "GROUP BY COALESCE(r.rolname, 'PUBLIC')"
                        ),
                        {"t": _regclass_name},
                    ).fetchall()
                    if grant_rows:
                        grants = []
                        for r in grant_rows:
                            grants.append({
                                "role": r[0],
                                "privileges": list(r[1]) if r[1] else ["ALL"],
                                "grantable": bool(r[2]),
                            })
                        table_entry["pg_grants"] = grants
                except Exception:
                    pass

                try:
                    trigger_rows = _conn.execute(
                        text("""
                            SELECT tgname, pg_get_triggerdef(t.oid) AS definition
                            FROM pg_trigger t
                            WHERE t.tgrelid = CAST(:t AS regclass) AND NOT t.tgisinternal
                            ORDER BY tgname
                        """),
                        {"t": _regclass_name},
                    ).fetchall()
                    if trigger_rows:
                        triggers = []
                        for r in trigger_rows:
                            triggers.append({
                                "name": r[0],
                                "definition": r[1],
                            })
                        table_entry["pg_triggers"] = triggers
                except Exception as e:
                    import logging
                    logging.getLogger('dbwarden.snapshot').warning('trigger extraction failed for %s: %s', _regclass_name, e)
                    try:
                        _conn.rollback()
                    except Exception:
                        pass

                if pg_table:
                    table_entry["pg_table"] = pg_table
            except Exception:
                import logging
                logging.getLogger('dbwarden.snapshot').warning('PG table extraction failed for %s', _regclass_name)
                try:
                    _conn.rollback()
                except Exception:
                    pass
            finally:
                if own_engine and _conn is not None:
                    try:
                        _conn.rollback()
                    except Exception:
                        pass
                    try:
                        _conn.close()
                    except Exception:
                        pass

        if database_type in ("mysql", "mariadb"):
            _conn = engine.connect() if own_engine and engine is not None else connection
            try:
                my_table: dict[str, Any] = {}

                try:
                    row = _conn.execute(
                        text(
                            "SELECT ENGINE, TABLE_COLLATION, AUTO_INCREMENT, ROW_FORMAT, TABLE_COMMENT "
                            "FROM information_schema.TABLES "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
                        ),
                        {"t": table_name},
                    ).fetchone()
                    if row:
                        if row[0]:
                            my_table["my_engine"] = row[0]
                        if row[1]:
                            my_table["my_collate"] = row[1]
                            charset = str(row[1]).split("_", 1)[0]
                            if charset:
                                my_table["my_charset"] = charset
                        if row[2] is not None:
                            my_table["my_auto_increment"] = int(row[2])
                        if row[3]:
                            my_table["my_row_format"] = row[3]
                        if row[4]:
                            table_entry["comment"] = row[4]
                except Exception:
                    pass

                try:
                    rows = _conn.execute(
                        text(
                            "SELECT COLUMN_NAME, COLUMN_TYPE, CHARACTER_SET_NAME, COLLATION_NAME, EXTRA, COLUMN_COMMENT "
                            "FROM information_schema.COLUMNS "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
                        ),
                        {"t": table_name},
                    ).fetchall()
                    for row in rows:
                        col_entry = columns_dict.get(row[0])
                        if col_entry is None:
                            continue
                        my_column = dict(col_entry.get("my_column", {}) or {})
                        column_type = str(row[1] or "")
                        if "unsigned" in column_type.lower():
                            my_column["my_unsigned"] = True
                        if row[2]:
                            my_column["my_charset"] = row[2]
                        if row[3]:
                            my_column["my_collate"] = row[3]
                        extra = str(row[4] or "")
                        if "auto_increment" in extra.lower():
                            col_entry["autoincrement"] = True
                        on_update_match = re.search(r"on update\s+(.+)$", extra, re.IGNORECASE)
                        if on_update_match:
                            my_column["my_on_update"] = on_update_match.group(1).strip()
                        if row[5]:
                            col_entry["comment"] = row[5]
                        if my_column:
                            col_entry["my_column"] = my_column
                except Exception:
                    pass

                if my_table:
                    table_entry["my_table"] = my_table
            except Exception:
                pass
            finally:
                if own_engine and _conn is not None:
                    try:
                        _conn.close()
                    except Exception:
                        pass

        tables[table_name] = table_entry

        constraint_index_names: set[str] = set()
        if database_type == "postgresql":
            _pg_c = None
            try:
                _pg_c = engine.connect() if own_engine and engine is not None else connection
                rows = _pg_c.execute(
                    text(
                        "SELECT ci.relname "
                        "FROM pg_constraint c "
                        "JOIN pg_class ci ON ci.oid = c.conindid "
                        "WHERE c.conrelid = CAST(:t AS regclass) "
                        "AND c.contype IN ('p', 'u', 'x') "
                        "AND c.conindid <> 0"
                    ),
                    {"t": _regclass_name},
                ).fetchall()
                constraint_index_names = {r[0] for r in rows}
            except Exception:
                pass
            finally:
                if own_engine and _pg_c is not None:
                    _pg_c.close()

        for idx in inspector.get_indexes(table_name, **inspect_kw):
            idx_name = idx.get("name", "")
            if not idx_name:
                continue
            if idx_name in constraint_index_names:
                continue
            if idx.get("unique") and set(idx.get("column_names", [])) == pk_columns:
                continue
            raw_cols = list(idx.get("column_names", []))
            raw_exprs = list(idx.get("expressions", []))
            expr = None
            clean_cols: list[str] = []
            if raw_exprs:
                for c in raw_cols:
                    if c is not None:
                        clean_cols.append(c)
                exprs = [e for e in raw_exprs if e is not None]
                if len(exprs) == 1 and not clean_cols:
                    expr = exprs[0]
                else:
                    clean_cols.extend(exprs)
            else:
                for c in raw_cols:
                    if _is_expression(c):
                        expr = c
                    else:
                        clean_cols.append(c)
            idx_entry: dict[str, Any] = {
                "table": table_name,
                "name": idx_name,
                "columns": clean_cols,
                "unique": bool(idx.get("unique", False)),
            }
            if expr:
                idx_entry["expression"] = expr
            idx_dialect = idx.get("dialect_options", {})
            for k in ("postgresql_using", "mysql_using", "mariadb_using", "sqlite_using"):
                val = idx_dialect.get(k)
                if val:
                    idx_entry["using"] = val
                    break
            if "using" not in idx_entry:
                idx_entry["using"] = "btree"
            for k in ("postgresql_where",):
                val = idx_dialect.get(k)
                if val:
                    idx_entry["where"] = val
                    break
            incl = idx.get("include_columns")
            if incl:
                idx_entry["include"] = list(incl)
            for k in ("postgresql_with",):
                val = idx_dialect.get(k)
                if val:
                    idx_entry["with_params"] = val
                    break
            for k in ("postgresql_tablespace",):
                val = idx_dialect.get(k)
                if val:
                    idx_entry["tablespace"] = val
                    break
            for k in ("postgresql_nulls_not_distinct",):
                val = idx_dialect.get(k)
                if val:
                    idx_entry["nulls_not_distinct"] = True
                    break

            if database_type == "postgresql" and idx_name:
                _pg_c = None
                try:
                    _pg_c = engine.connect() if own_engine and engine is not None else connection
                    sort_rows = _pg_c.execute(
                        text("""
                            SELECT a.attname,
                                   pg_index_column_has_property(i.indexrelid, k, 'asc') AS is_asc,
                                   pg_index_column_has_property(i.indexrelid, k, 'nulls_first') AS nf
                            FROM pg_index i
                            CROSS JOIN LATERAL generate_series(0, i.indnkeyatts - 1) AS k
                            JOIN pg_class ci ON ci.oid = i.indexrelid
                            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[k]
                            WHERE ci.relname = :idxname AND i.indkey[k] <> 0
                            ORDER BY k
                        """),
                        {"idxname": idx_name},
                    ).fetchall()
                    sorting: dict[str, str] = {}
                    for r in sort_rows:
                        parts: list[str] = []
                        if r.is_asc is False:
                            parts.append("DESC")
                        if r.nf is False:
                            parts.append("NULLS LAST")
                        elif r.nf is True:
                            parts.append("NULLS FIRST")
                        if parts:
                            sorting[r.attname] = " ".join(parts)
                    if sorting:
                        idx_entry["column_sorting"] = sorting
                except Exception:
                    pass
                finally:
                    if own_engine and _pg_c is not None:
                        _pg_c.close()

            if database_type == "postgresql" and idx_name:
                _pg_c = None
                try:
                    _pg_c = engine.connect() if own_engine and engine is not None else connection
                    opclass_rows = _pg_c.execute(
                        text("""
                            SELECT a.attname, o.opcname
                            FROM pg_index i
                            CROSS JOIN LATERAL generate_series(0, i.indnkeyatts - 1) AS k
                            JOIN pg_class ci ON ci.oid = i.indexrelid
                            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[k]
                            JOIN pg_opclass o ON o.oid = i.indclass[k]
                            WHERE ci.relname = :idxname AND i.indkey[k] <> 0
                              AND COALESCE(o.opcdefault, false) = false
                            ORDER BY k
                        """),
                        {"idxname": idx_name},
                    ).fetchall()
                    if opclass_rows:
                        idx_entry["postgresql_ops"] = {r.attname: r.opcname for r in opclass_rows}
                except Exception:
                    pass
                finally:
                    if own_engine and _pg_c is not None:
                        _pg_c.close()

            if database_type == "postgresql" and idx_name:
                _pg_c = None
                try:
                    _pg_c = engine.connect() if own_engine and engine is not None else connection
                    row = _pg_c.execute(
                        text("""
                            SELECT d.description
                            FROM pg_index i
                            JOIN pg_class ci ON ci.oid = i.indexrelid
                            LEFT JOIN pg_description d ON d.objoid = ci.oid AND d.objsubid = 0
                                WHERE ci.relname = :idxname
                        """),
                        {"idxname": idx_name},
                    ).scalar()
                    if row:
                        idx_entry["comment"] = row
                except Exception:
                    pass
                finally:
                    if own_engine and _pg_c is not None:
                        _pg_c.close()

            indexes[f"{table_name}.{idx_name}"] = idx_entry

        for fk in inspector.get_foreign_keys(table_name, **inspect_kw):
            fk_name = fk.get("name", "")
            if not fk_name:
                fk_name = f"fk_{table_name}_{'_'.join(fk.get('constrained_columns', []))}"
            fk_options = fk.get("options", {})
            fk_match = fk_options.get("match")
            if fk_match and fk_match.upper() != "SIMPLE":
                fk_match = fk_match.upper()
            else:
                fk_match = None
            constraints[f"{table_name}.{fk_name}"] = {
                "type": "foreign_key",
                "name": fk_name,
                "table": table_name,
                "columns": list(fk.get("constrained_columns", [])),
                "referenced_table": fk.get("referred_table", ""),
                "referenced_columns": list(fk.get("referred_columns", [])),
                "on_delete": fk_options.get("ondelete", "NO ACTION"),
                "on_update": fk_options.get("onupdate", "NO ACTION"),
                "deferrable": bool(fk_options.get("deferrable", False)),
            }
            if fk_match:
                constraints[f"{table_name}.{fk_name}"]["match"] = fk_match

        for uq in inspector.get_unique_constraints(table_name, **inspect_kw):
            uq_name = uq.get("name", "")
            if not uq_name:
                continue
            constraints[f"{table_name}.{uq_name}"] = {
                "type": "unique",
                "name": uq_name,
                "table": table_name,
                "columns": list(uq.get("column_names", [])),
            }

        for ck in inspector.get_check_constraints(table_name, **inspect_kw):
            ck_name = ck.get("name", "")
            if not ck_name:
                ck_name = f"ck_{table_name}_{hash(ck.get('sqltext', ''))}"
            constraints[f"{table_name}.{ck_name}"] = {
                "type": "check",
                "name": ck_name,
                "table": table_name,
                "columns": [],
                "expression": ck.get("sqltext", ""),
            }

        if database_type == "postgresql":
            _pg_conn = engine.connect() if own_engine else connection
            _regclass_name = f'"{pg_schema}"."{table_name}"' if pg_schema else f'"{table_name}"'
            try:
                no_inherit_rows = _pg_conn.execute(
                    text("SELECT conname, connoinherit FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) AND contype = 'c'"),
                    {"t": _regclass_name},
                ).fetchall()
                for r in no_inherit_rows:
                    if r[1]:
                        cname = f"{table_name}.{r[0]}"
                        if cname in constraints:
                            constraints[cname]["no_inherit"] = True
            except Exception:
                pass
            try:
                defer_rows = _pg_conn.execute(
                    text("SELECT conname, condeferrable, condeferred FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) AND contype = 'u'"),
                    {"t": _regclass_name},
                ).fetchall()
                for r in defer_rows:
                    cname = f"{table_name}.{r[0]}"
                    if cname in constraints:
                        constraints[cname]["deferrable"] = bool(r[1])
                        constraints[cname]["initially_deferred"] = bool(r[2])
            except Exception:
                pass
            try:
                validated_rows = _pg_conn.execute(
                    text("SELECT conname, contype, convalidated FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) AND contype IN ('f', 'c')"),
                    {"t": _regclass_name},
                ).fetchall()
                for r in validated_rows:
                    cname = f"{table_name}.{r[0]}"
                    if cname in constraints:
                        constraints[cname]["validated"] = bool(r[2])
            except Exception:
                pass
            if own_engine:
                _pg_conn.close()

    if database_type == "postgresql":
        try:
            for enum_info in inspector.get_enums():
                enums[enum_info["name"]] = list(enum_info.get("labels", []))
        except Exception:
            pass

        try:
            _pg_conn = None
            _pg_conn = engine.connect() if own_engine else connection
            domain_rows = _pg_conn.execute(
                text("""
                    SELECT t.typname AS domain_name,
                           pg_catalog.format_type(t.typbasetype, t.typtypmod) AS domain_type,
                           t.typnotnull AS not_null,
                           pg_catalog.pg_get_expr(t.typdefaultbin, 'pg_catalog.pg_class'::regclass) AS default,
                           n.nspname AS schema
                    FROM pg_catalog.pg_type t
                    JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
                    WHERE t.typtype = 'd'
                      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                    ORDER BY t.typname
                """),
            ).fetchall()
            for r in domain_rows:
                domain_info = {
                    "domain_type": r.domain_type,
                    "not_null": bool(r.not_null),
                }
                if r.default:
                    domain_info["default"] = r.default
                domains[r.domain_name] = domain_info
                if r.schema and r.schema != "public":
                    domains[r.domain_name]["schema"] = r.schema
            check_rows = _pg_conn.execute(
                text("""
                    SELECT t.typname AS domain_name,
                           pg_catalog.pg_get_constraintdef(c.oid) AS check_def
                    FROM pg_catalog.pg_type t
                    JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
                    JOIN pg_catalog.pg_constraint c ON c.contypid = t.oid AND c.contype = 'c'
                    WHERE t.typtype = 'd'
                      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                """),
            ).fetchall()
            for r in check_rows:
                if r.domain_name in domains:
                    domains[r.domain_name]["check"] = r.check_def
        except Exception:
            pass
        finally:
            if own_engine and _pg_conn is not None:
                _pg_conn.close()

        try:
            view_names = inspector.get_view_names(**inspect_kw)
            _pg_conn = None
            _pg_conn = engine.connect() if own_engine else connection
            for view_name in view_names:
                if view_name in tables:
                    continue
                view_columns = inspector.get_columns(view_name, **inspect_kw)
                view_pk = inspector.get_pk_constraint(view_name, **inspect_kw)
                view_pk_columns = set(view_pk.get("constrained_columns", []) or [])

                view_columns_dict: dict[str, Any] = {}
                for col in view_columns:
                    col_name = col["name"]
                    col_type = col.get("type", "")
                    normalized = normalize_type(str(col_type))
                    view_columns_dict[col_name] = {
                        "type": normalized["type"],
                        "nullable": bool(col.get("nullable", True)),
                        "primary_key": col_name in view_pk_columns,
                        "default": col.get("default"),
                        "autoincrement": _is_autoincrement(col),
                    }
                    if normalized.get("raw"):
                        view_columns_dict[col_name]["raw"] = True
                    if "length" in normalized:
                        view_columns_dict[col_name]["length"] = normalized["length"]
                    if "precision" in normalized:
                        view_columns_dict[col_name]["precision"] = normalized["precision"]
                    if "scale" in normalized:
                        view_columns_dict[col_name]["scale"] = normalized["scale"]
                    comment = col.get("comment")
                    if comment is not None:
                        view_columns_dict[col_name]["comment"] = comment

                view_definition = None
                view_materialized = False
                try:
                    vrow = _pg_conn.execute(
                        text("SELECT pg_get_viewdef(:t, false) AS vdef, relkind FROM pg_class WHERE oid = CAST(:t2 AS regclass)"),
                        {"t": view_name, "t2": view_name},
                    ).fetchone()
                    if vrow:
                        view_definition = vrow[0] if vrow[0] else None
                        view_materialized = vrow[1] == 'm'
                except Exception:
                    pass

                view_entry: dict[str, Any] = {
                    "columns": view_columns_dict,
                    "primary_key": list(view_pk_columns) if view_pk_columns else [],
                    "comment": None,
                    "schema": pg_schema,
                    "object_type": "view",
                    "pg_view_definition": view_definition,
                    "pg_view_materialized": view_materialized,
                }
                try:
                    view_comment = inspector.get_table_comment(view_name, **inspect_kw)
                    if view_comment and view_comment.get("text"):
                        view_entry["comment"] = view_comment["text"]
                except Exception:
                    pass

                tables[view_name] = view_entry

            try:
                matview_q = "SELECT relname FROM pg_class WHERE relkind = 'm'"
                if pg_schema:
                    matview_q += " AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = :schema)"
                    matview_rows = _pg_conn.execute(text(matview_q), {"schema": pg_schema})
                else:
                    matview_rows = _pg_conn.execute(text(matview_q + " AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = current_schema())"))
                for mrow in matview_rows:
                    mv_name = mrow[0]
                    if mv_name in tables:
                        mv_entry = tables[mv_name]
                        try:
                            vdef_row = _pg_conn.execute(
                                text("SELECT pg_get_viewdef(:t, false)"),
                                {"t": mv_name},
                            ).fetchone()
                            mv_entry["pg_view_definition"] = vdef_row[0] if vdef_row else None
                        except Exception:
                            mv_entry["pg_view_definition"] = None
                        mv_entry["pg_view_materialized"] = True
                        mv_entry["object_type"] = "materialized_view"
                        mv_entry["schema"] = pg_schema
                        mv_entry.pop("options", None)
                        mv_entry.pop("foreign_keys", None)
                    else:
                        try:
                            mv_columns = inspector.get_columns(mv_name, **inspect_kw)
                            mv_pk = inspector.get_pk_constraint(mv_name, **inspect_kw)
                            mv_pk_cols = set(mv_pk.get("constrained_columns", []) or [])
                            mv_cols_dict = {}
                            for col in mv_columns:
                                cname = col["name"]
                                ctype = col.get("type", "")
                                norm = normalize_type(str(ctype))
                                mv_cols_dict[cname] = {
                                    "type": norm["type"],
                                    "nullable": bool(col.get("nullable", True)),
                                    "primary_key": cname in mv_pk_cols,
                                    "default": col.get("default"),
                                    "autoincrement": _is_autoincrement(col),
                                }
                            vdef_row = _pg_conn.execute(
                                text("SELECT pg_get_viewdef(:t, false)"),
                                {"t": mv_name},
                            ).fetchone()
                            vdef = vdef_row[0] if vdef_row else None
                            tables[mv_name] = {
                                "columns": mv_cols_dict,
                                "primary_key": list(mv_pk_cols) if mv_pk_cols else [],
                                "comment": None,
                                "schema": pg_schema,
                                "object_type": "materialized_view",
                                "pg_view_definition": vdef,
                                "pg_view_materialized": True,
                            }
                        except Exception:
                            pass
            except Exception:
                pass

            try:
                seq_rows = _pg_conn.execute(
                    text("""SELECT seq.relname AS seq_name,
                                   s.seqincrement, s.seqmin, s.seqmax, s.seqstart,
                                   s.seqcycle, pg_get_userbyid(seq.relowner) AS owned_by
                            FROM pg_sequence s
                            JOIN pg_class seq ON seq.oid = s.seqrelid
                            WHERE seq.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = current_schema())"""),
                ).fetchall()
                for r in seq_rows:
                    seq_info: dict[str, Any] = {"increment": r.seqincrement}
                    if r.seqmin is not None:
                        seq_info["minvalue"] = r.seqmin
                    if r.seqmax is not None:
                        seq_info["maxvalue"] = r.seqmax
                    if r.seqstart is not None:
                        seq_info["start"] = r.seqstart
                    if r.seqcycle:
                        seq_info["cycle"] = True
                    if r.owned_by:
                        seq_info["owned_by"] = r.owned_by
                    sequences[r.seq_name] = seq_info
            except Exception as e:
                import logging
                logging.getLogger('dbwarden.snapshot').warning('sequences extraction failed: %s', e)

            try:
                comp_rows = _pg_conn.execute(
                    text("""SELECT t.typname AS type_name, n.nspname AS schema,
                                   a.attname AS col_name,
                                   pg_catalog.format_type(a.atttypid, a.atttypmod) AS col_type
                            FROM pg_catalog.pg_type t
                            JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
                            JOIN pg_catalog.pg_attribute a ON a.attrelid = t.typrelid
                            WHERE t.typtype = 'c'
                              AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                              AND NOT EXISTS (
                                  SELECT 1 FROM pg_class c
                                  WHERE c.relname = t.typname
                                    AND c.relnamespace = t.typnamespace
                                    AND c.relkind IN ('r', 'v', 'm', 'S', 't', 'p'))
                              AND a.attnum > 0 AND NOT a.attisdropped
                            ORDER BY t.typname, a.attnum"""),
                ).fetchall()
                comp_type_map: dict[str, dict[str, Any]] = {}
                for r in comp_rows:
                    tname = r.type_name
                    if tname not in comp_type_map:
                        comp_type_map[tname] = {"columns": []}
                        if r.schema and r.schema != "public":
                            comp_type_map[tname]["schema"] = r.schema
                    comp_type_map[tname]["columns"].append({"name": r.col_name, "type": r.col_type})
                composite_types.update(comp_type_map)
            except Exception as e:
                import logging
                logging.getLogger('dbwarden.snapshot').warning('composite_types extraction failed: %s', e)
                try:
                    _pg_conn.rollback()
                except Exception:
                    pass

            try:
                func_rows = _pg_conn.execute(
                    text("""SELECT n.nspname AS schema, p.proname AS func_name,
                                   pg_catalog.pg_get_functiondef(p.oid) AS definition
                            FROM pg_catalog.pg_proc p
                            JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
                              AND p.prokind IN ('f', 'p', 'w')
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM pg_catalog.pg_depend d
                                  JOIN pg_catalog.pg_extension e ON e.oid = d.refobjid
                                  WHERE d.objid = p.oid AND d.deptype = 'e'
                              )
                            ORDER BY p.proname"""),
                ).fetchall()
                for r in func_rows:
                    func_entry: dict[str, Any] = {"definition": r.definition}
                    if r.schema and r.schema != "public":
                        func_entry["schema"] = r.schema
                    functions[r.func_name] = func_entry
            except Exception as e:
                import logging
                logging.getLogger('dbwarden.snapshot').warning('functions extraction failed: %s', e)
                try:
                    _pg_conn.rollback()
                except Exception:
                    pass

            try:
                role_rows = _pg_conn.execute(
                    text("""SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb,
                                   rolcanlogin, rolconnlimit, rolvaliduntil
                            FROM pg_roles WHERE rolname NOT LIKE 'pg_%'"""),
                ).fetchall()
                for r in role_rows:
                    role_info: dict[str, Any] = {}
                    if r.rolsuper:
                        role_info["superuser"] = True
                    if not r.rolinherit:
                        role_info["inherit"] = False
                    if r.rolcreaterole:
                        role_info["createrole"] = True
                    if r.rolcreatedb:
                        role_info["createdb"] = True
                    if r.rolcanlogin:
                        role_info["login"] = True
                    if r.rolconnlimit is not None and r.rolconnlimit != -1:
                        role_info["connlimit"] = r.rolconnlimit
                    if r.rolvaliduntil:
                        role_info["valid_until"] = str(r.rolvaliduntil)
                    roles[r.rolname] = role_info
            except Exception as e:
                import logging
                logging.getLogger('dbwarden.snapshot').warning('roles extraction failed: %s', e)
                try:
                    _pg_conn.rollback()
                except Exception:
                    pass

            try:
                dp_rows = _pg_conn.execute(
                    text("""SELECT n.nspname, COALESCE(r.rolname, 'PUBLIC') AS grantee,
                                   da.defaclobjtype, acl.privilege_type
                            FROM pg_default_acl da
                            JOIN pg_namespace n ON n.oid = da.defaclnamespace
                            CROSS JOIN LATERAL aclexplode(da.defaclacl) AS acl
                            LEFT JOIN pg_roles r ON r.oid = acl.grantee"""),
                ).fetchall()
                dp_map: dict[str, dict[str, Any]] = {}
                for r in dp_rows:
                    obj_type_map = {'r': 'tables', 'S': 'sequences', 'f': 'functions', 'T': 'types', 'n': 'schemas'}
                    obj_type = obj_type_map.get(r.defaclobjtype, r.defaclobjtype)
                    key = f"{r.nspname}.{r.grantee}.{obj_type}"
                    if key not in dp_map:
                        dp_map[key] = {"schema": r.nspname, "role": r.grantee, "object_type": obj_type, "privileges": []}
                    dp_map[key]["privileges"].append(r.privilege_type)
                for val in dp_map.values():
                    val["privileges"] = list(sorted(set(val["privileges"])))
                default_privileges.update(dp_map)
            except Exception as e:
                import logging
                logging.getLogger('dbwarden.snapshot').warning('default_privileges extraction failed: %s', e)
                try:
                    _pg_conn.rollback()
                except Exception:
                    pass

            try:
                sg_rows = _pg_conn.execute(
                    text("""SELECT n.nspname AS schema, COALESCE(r.rolname, 'PUBLIC') AS grantee,
                                   array_agg(acl.privilege_type ORDER BY acl.privilege_type) AS privileges,
                                   bool_or(acl.is_grantable) AS grantable
                            FROM pg_namespace n
                            CROSS JOIN LATERAL aclexplode(n.nspacl) AS acl
                            LEFT JOIN pg_roles r ON r.oid = acl.grantee
                            WHERE n.nspacl IS NOT NULL
                              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                            GROUP BY n.nspname, COALESCE(r.rolname, 'PUBLIC')"""),
                ).fetchall()
                sg_map: dict[str, list[dict[str, Any]]] = {}
                for r in sg_rows:
                    schema_name = r.schema
                    if schema_name not in sg_map:
                        sg_map[schema_name] = []
                    sg_map[schema_name].append({
                        "role": r.grantee,
                        "privileges": list(r.privileges) if r.privileges else ["ALL"],
                        "grantable": bool(r.grantable),
                    })
                schema_grants.update(sg_map)
            except Exception as e:
                import logging
                logging.getLogger('dbwarden.snapshot').warning('schema_grants extraction failed: %s', e)
                try:
                    _pg_conn.rollback()
                except Exception:
                    pass

            try:
                et_rows = _pg_conn.execute(
                    text("""SELECT evtname, evtevent, proname AS func_name,
                                   n.nspname AS func_schema, evtenabled, evttags
                            FROM pg_event_trigger et
                            LEFT JOIN pg_proc p ON p.oid = et.evtfoid
                            LEFT JOIN pg_namespace n ON n.oid = p.pronamespace"""),
                ).fetchall()
                for r in et_rows:
                    et_entry: dict[str, Any] = {
                        "event": r.evtevent,
                        "function": {"name": r.func_name, "schema": r.func_schema},
                    }
                    if r.evttags:
                        et_entry["tags"] = list(r.evttags)
                    if r.evtenabled and r.evtenabled != 'O':
                        et_entry["enabled"] = r.evtenabled
                    event_triggers[r.evtname] = et_entry
            except Exception as e:
                import logging
                logging.getLogger('dbwarden.snapshot').warning('event_triggers extraction failed: %s', e)
                try:
                    _pg_conn.rollback()
                except Exception:
                    pass

            try:
                if pg_version and pg_version >= (14, 0):
                    stats_rows = _pg_conn.execute(
                        text("""SELECT s.stxname,
                                       s.stxnamespace::regnamespace::text AS schema,
                                       c.relname AS table_name,
                                       s.stxkind AS kinds, s.stxkeys AS columns,
                                       pg_get_statisticsobjdef_expressions(s.oid) AS expressions
                                FROM pg_statistic_ext s
                                JOIN pg_class c ON c.oid = s.stxrelid
                                WHERE s.stxrelid > 0"""),
                    ).fetchall()
                else:
                    stats_rows = _pg_conn.execute(
                        text("""SELECT s.stxname,
                                       s.stxnamespace::regnamespace::text AS schema,
                                       c.relname AS table_name,
                                       s.stxkind AS kinds, s.stxkeys AS columns,
                                       NULL AS expressions
                                FROM pg_statistic_ext s
                                JOIN pg_class c ON c.oid = s.stxrelid
                                WHERE s.stxrelid > 0"""),
                    ).fetchall()
                for r in stats_rows:
                    stat_entry: dict[str, Any] = {"table": r.table_name, "kinds": list(r.kinds) if r.kinds else []}
                    if r.schema and r.schema != "public":
                        stat_entry["schema"] = r.schema
                    if r.columns:
                        stat_entry["columns"] = r.columns
                    if r.expressions:
                        exprs = [e.strip() for e in str(r.expressions).split(",") if e.strip()]
                        stat_entry["expressions"] = exprs
                    stat_key = f"{r.schema}.{r.stxname}" if r.schema and r.schema != "public" else r.stxname
                    extended_stats[stat_key] = stat_entry
            except Exception as e:
                import logging
                logging.getLogger('dbwarden.snapshot').warning('extended_stats extraction failed: %s', e)
                try:
                    _pg_conn.rollback()
                except Exception:
                    pass

        except Exception:
            pass
        finally:
            if own_engine and _pg_conn is not None:
                _pg_conn.close()

    if own_engine and engine is not None:
        engine.dispose()
    else:
        try:
            conn_context.__exit__(None, None, None)
        except Exception:
            pass

    return {
        "format_version": 1,
        "migration_id": "",
        "database_name": db_name,
        "database_type": database_type or "",
        "applied_at": "",
        "tables": tables,
        "enums": enums,
        "domains": domains,
        "indexes": indexes,
        "constraints": constraints,
        "sequences": sequences,
        "composite_types": composite_types,
        "functions": functions,
        "roles": roles,
        "default_privileges": default_privileges,
        "schema_grants": schema_grants,
        "event_triggers": event_triggers,
        "extended_stats": extended_stats,
    }
