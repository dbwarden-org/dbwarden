"""PostgreSQL-specific schema extraction helpers.

This module contains all PostgreSQL enrichment logic that was previously
inlined in the ``extract_full_schema_snapshot`` God Function. It handles:

- Column enrichment (enum/identity/collation/generated, array/tsvector/jsonb/range)
- Table metadata (RLS, partitioning, storage, triggers, policies, grants)
- Database-level objects (enums, domains, views, sequences, composite types,
  functions, roles, default privileges, schema grants, event triggers,
  extended statistics)
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any

from dbwarden.engine.backends.postgresql.extract import _strip_pg_expr_parens
from dbwarden.logging import get_component_logger

_snapshot_logger = get_component_logger("snapshot")


@contextmanager
def _pg_connection(engine: Any, connection: Any):
    """Yield a live connection, reusing ``connection`` when no engine is owned."""
    if connection is not None:
        yield connection
    elif engine is not None:
        with engine.connect() as conn:
            yield conn
    else:
        yield None


def enrich_column_pg(
    col_entry: dict[str, Any],
    col: dict[str, Any],
    raw_type_str: str,
    normalized: dict[str, Any],
    engine: Any,
    connection: Any = None,
) -> None:
    """Enrich a column entry with PostgreSQL-specific metadata.

    Adds pg_type (enum/array/tsvector/jsonb/range), identity, collation,
    and generated column information. Reuses ``connection`` when the caller
    already holds one, so batch extraction does not open an engine per column.
    """
    col_type = col.get("type", "")

    if hasattr(col_type, "enums") and col_type.enums and hasattr(col_type, "name") and col_type.name:
        col_entry["type"] = "enum"
        col_entry["enum_name"] = col_type.name
        col_entry["pg_type"] = {
            "kind": "enum",
            "type_name": col_type.name,
            "values": list(col_type.enums),
        }
    enum_match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)$", raw_type_str)
    if enum_match:
        from sqlalchemy import text
        with _pg_connection(engine, connection) as conn:
            if conn is not None:
                try:
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
        from sqlalchemy import text
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
        from sqlalchemy import text
        with _pg_connection(engine, connection) as _c:
            if _c is not None:
                try:
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


def _pg_table_comment(
    inspector: Any,
    table_entry: dict[str, Any],
    table_name: str,
    inspector_kw: dict[str, Any],
) -> None:
    try:
        table_comment = table_entry.get("_inspector")
        if table_comment and hasattr(table_comment, "get_table_comment"):
            tc = table_comment.get_table_comment(table_name, **inspector_kw)
            if tc and tc.get("text"):
                table_entry["comment"] = tc["text"]
    except Exception:
        pass


def _pg_reloptions(conn: Any, regclass: str) -> dict[str, Any]:
    from sqlalchemy import text
    try:
        rows = conn.execute(
            text("SELECT unnest(COALESCE(reloptions, '{}')) FROM pg_class WHERE oid = CAST(:t AS regclass)"),
            {"t": regclass},
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
        result: dict[str, Any] = {}
        if params:
            result.update(params)
        if storage_params:
            result["pg_storage_params"] = storage_params
        return result
    except Exception:
        return {}


def _pg_toast_params(conn: Any, regclass: str) -> dict[str, Any]:
    from sqlalchemy import text
    try:
        toast_sql = (
            "SELECT unnest(COALESCE("
            "(SELECT reloptions FROM pg_class WHERE oid = c.reltoastrelid), '{}'"
            ")) FROM pg_class c WHERE c.oid = CAST(:t AS regclass)"
        )
        toast_rows = conn.execute(
            text(toast_sql),
            {"t": regclass},
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
            return {"pg_storage_params": toast_params}
        return {}
    except Exception:
        return {}


def _pg_unlogged(conn: Any, regclass: str) -> dict[str, Any]:
    from sqlalchemy import text
    try:
        row = conn.execute(
            text("SELECT relpersistence FROM pg_class WHERE oid = CAST(:t AS regclass)"),
            {"t": regclass},
        ).fetchone()
        if row and row[0] == 'u':
            return {"pg_unlogged": True}
    except Exception:
        pass
    return {}


def _pg_tablespace(conn: Any, regclass: str) -> dict[str, Any]:
    from sqlalchemy import text
    try:
        row = conn.execute(
            text(
                "SELECT spcname FROM pg_tablespace t "
                "JOIN pg_class c ON c.reltablespace = t.oid "
                "WHERE c.oid = CAST(:t AS regclass)"
            ),
            {"t": regclass},
        ).fetchone()
        if row:
            return {"pg_tablespace": row[0]}
    except Exception:
        pass
    return {}


def _pg_inheritance(conn: Any, regclass: str) -> dict[str, Any]:
    from sqlalchemy import text
    try:
        rows = conn.execute(
            text("""
                SELECT p_ns.nspname AS parent_schema, p.relname AS parent_name
                FROM pg_inherits i
                JOIN pg_class c ON c.oid = i.inhrelid
                JOIN pg_namespace c_ns ON c_ns.oid = c.relnamespace
                JOIN pg_class p ON p.oid = i.inhparent
                JOIN pg_namespace p_ns ON p_ns.oid = p.relnamespace
                WHERE c.oid = CAST(:t AS regclass)
            """),
            {"t": regclass},
        ).fetchall()
        parents = [r[1] for r in rows]
        part_child = conn.execute(
            text(
                "SELECT parent.relname AS parent_name, pg_get_expr(child.relpartbound, child.oid) AS bound "
                "FROM pg_inherits i "
                "JOIN pg_class child ON child.oid = i.inhrelid "
                "JOIN pg_class parent ON parent.oid = i.inhparent "
                "WHERE child.oid = CAST(:t AS regclass) AND child.relpartbound IS NOT NULL"
            ),
            {"t": regclass},
        ).fetchone()
        result: dict[str, Any] = {}
        if part_child:
            result["pg_partition_of"] = part_child.parent_name
            result["pg_partition_bound"] = part_child.bound
        elif len(parents) == 1:
            result["pg_inherits"] = parents[0]
        elif parents:
            result["pg_inherits"] = parents
        return result
    except Exception:
        return {}


def _pg_partition_strategy(conn: Any, regclass: str) -> dict[str, Any]:
    from sqlalchemy import text
    try:
        part_row = conn.execute(
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
            {"t": regclass},
        ).fetchone()
        if part_row:
            strat_map = {"r": "RANGE", "l": "LIST", "h": "HASH"}
            strategy = strat_map.get(part_row[0], part_row[0])
            part_columns = list(part_row[1] or [])
            part_expr = part_row[2]
            if part_expr:
                part_columns.append(part_expr.strip())
            return {
                "pg_partition": {
                    "strategy": strategy,
                    "columns": part_columns,
                },
            }
    except Exception:
        pass
    return {}


def _pg_exclusion_constraints(conn: Any, regclass: str) -> dict[str, Any]:
    from sqlalchemy import text
    try:
        rows = conn.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) AS definition "
                "FROM pg_constraint "
                "WHERE conrelid = CAST(:t AS regclass) AND contype = 'x'"
            ),
            {"t": regclass},
        ).fetchall()
        excludes = [{"name": r[0], "expression": r[1]} for r in rows]
        if excludes:
            return {"pg_excludes": excludes}
    except Exception:
        pass
    return {}


def _pg_child_partitions(conn: Any, regclass: str, pg_schema: str | None) -> dict[str, Any]:
    from sqlalchemy import text
    try:
        child_rows = conn.execute(
            text("SELECT n.nspname, c.relname, pg_get_expr(c.relpartbound, c.oid) AS bound "
                 "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                 "JOIN pg_inherits i ON i.inhrelid = c.oid "
                 "WHERE i.inhparent = CAST(:t AS regclass) AND c.relispartition = true "
                 "ORDER BY c.relname"),
            {"t": regclass},
        ).fetchall()
        if child_rows:
            children = [
                {"name": r[1] if not pg_schema or r[0] == pg_schema else f"{r[0]}.{r[1]}", "bound": r[2]}
                for r in child_rows
            ]
            return {"pg_partitions": children}
    except Exception as e:
        _snapshot_logger.warning('child partitions extraction failed for %s: %s', regclass, e)
        try:
            conn.rollback()
        except Exception:
            pass
    return {}


def _pg_column_storage(conn: Any, regclass: str, columns_dict: dict[str, Any], pg_version: tuple[int, int] | None) -> None:
    from sqlalchemy import text
    try:
        if pg_version and pg_version >= (14, 0):
            attr_cols = "a.attname, a.attstorage, a.attcompression, a.attstattarget"
        else:
            attr_cols = "a.attname, a.attstorage, NULL::text AS attcompression, a.attstattarget"
        rows = conn.execute(
            text(
                f"SELECT {attr_cols}, t.typstorage "
                "FROM pg_attribute a "
                "JOIN pg_type t ON t.oid = a.atttypid "
                "WHERE a.attrelid = CAST(:t AS regclass) AND a.attnum > 0 AND NOT a.attisdropped "
                "ORDER BY a.attnum"
            ),
            {"t": regclass},
        ).fetchall()
        storage_map = {'p': 'PLAIN', 'm': 'MAIN', 'e': 'EXTERNAL', 'x': 'EXTENDED'}
        for r in rows:
            cname = r[0]
            if cname in columns_dict:
                pg_col = columns_dict[cname].get("pg_column", {})
                if isinstance(pg_col, dict):
                    storage = storage_map.get(r[1], r[1]) if r[1] else None
                    default_storage = storage_map.get(r[4], r[4]) if len(r) > 4 and r[4] else None
                    if storage and storage != default_storage:
                        pg_col["storage"] = storage
                    if r[2]:
                        pg_col["compression"] = r[2]
                    if r[3] is not None and r[3] != -1:
                        pg_col["statistics"] = r[3]
                    if pg_col:
                        columns_dict[cname]["pg_column"] = pg_col
    except Exception as e:
        _snapshot_logger.warning('attstats extraction failed for %s: %s', regclass, e)
        try:
            conn.rollback()
        except Exception:
            pass


def _pg_rls(conn: Any, regclass: str) -> dict[str, Any]:
    from sqlalchemy import text
    result: dict[str, Any] = {}
    try:
        row = conn.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE oid = CAST(:t AS regclass)"),
            {"t": regclass},
        ).fetchone()
        if row and row[0]:
            result["pg_rls"] = True
    except Exception:
        pass
    try:
        row = conn.execute(
            text("SELECT relforcerowsecurity FROM pg_class WHERE oid = CAST(:t AS regclass)"),
            {"t": regclass},
        ).fetchone()
        if row and row[0]:
            result["pg_rls_force"] = True
    except Exception as e:
        _snapshot_logger.warning('rls_force extraction failed for %s: %s', regclass, e)
        try:
            conn.rollback()
        except Exception:
            pass
    return result


def _pg_policies(conn: Any, table_name: str, pg_schema: str | None) -> dict[str, Any]:
    from sqlalchemy import text
    try:
        policy_rows = conn.execute(
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
                policy_roles = list(r[3]) if r[3] else ["PUBLIC"]
                policy_entry = {
                    "name": r[0],
                    "permissive": "PERMISSIVE" if r[1] else "RESTRICTIVE",
                    "command": r[2] or "ALL",
                    "role": (
                        policy_roles[0]
                        if len(policy_roles) == 1
                        else policy_roles
                    ),
                    "using": _strip_pg_expr_parens(r[4]),
                }
                if r[5]:
                    policy_entry["with_check"] = _strip_pg_expr_parens(r[5])
                policies.append(policy_entry)
            return {"pg_policies": policies}
    except Exception:
        pass
    return {}


def _pg_table_grants(conn: Any, regclass: str) -> dict[str, Any]:
    from sqlalchemy import text
    try:
        grant_rows = conn.execute(
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
            {"t": regclass},
        ).fetchall()
        if grant_rows:
            grants = []
            for r in grant_rows:
                grants.append({
                    "role": r[0],
                    "privileges": list(r[1]) if r[1] else ["ALL"],
                    "grantable": bool(r[2]),
                })
            return {"pg_grants": grants}
    except Exception:
        pass
    return {}


def _pg_triggers(conn: Any, regclass: str) -> dict[str, Any]:
    from sqlalchemy import text
    try:
        trigger_rows = conn.execute(
            text("""
                SELECT tgname, pg_get_triggerdef(t.oid) AS definition
                FROM pg_trigger t
                WHERE t.tgrelid = CAST(:t AS regclass) AND NOT t.tgisinternal
                ORDER BY tgname
            """),
            {"t": regclass},
        ).fetchall()
        if trigger_rows:
            triggers = []
            for r in trigger_rows:
                triggers.append({
                    "name": r[0],
                    "definition": r[1],
                })
            return {"pg_triggers": triggers}
    except Exception as e:
        _snapshot_logger.warning('trigger extraction failed for %s: %s', regclass, e)
        try:
            conn.rollback()
        except Exception:
            pass
    return {}


def enrich_table_pg(
    table_entry: dict[str, Any],
    columns_dict: dict[str, Any],
    table_name: str,
    _regclass_name: str,
    pg_schema: str | None,
    pg_version: tuple[int, int] | None,
    engine: Any,
    connection: Any,
    own_engine: bool,
) -> None:
    """Enrich a table entry with PostgreSQL-specific metadata.

    Extracts RLS, partitioning, storage parameters, triggers, policies,
    grants, table comments, and column storage/compression/statistics.
    """
    inspector_kw = {"schema": pg_schema} if pg_schema else {}

    _pg_table_comment(table_entry.get("_inspector"), table_entry, table_name, inspector_kw)

    _conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT") if own_engine and engine is not None else connection
    try:
        pg_table: dict[str, Any] = {"backend": "postgresql"}

        for update in (
            _pg_reloptions(_conn, _regclass_name),
            _pg_toast_params(_conn, _regclass_name),
            _pg_unlogged(_conn, _regclass_name),
            _pg_tablespace(_conn, _regclass_name),
            _pg_inheritance(_conn, _regclass_name),
            _pg_partition_strategy(_conn, _regclass_name),
            _pg_exclusion_constraints(_conn, _regclass_name),
            _pg_child_partitions(_conn, _regclass_name, pg_schema),
            _pg_rls(_conn, _regclass_name),
        ):
            for k, v in update.items():
                if k == "pg_storage_params" and k in pg_table:
                    pg_table[k].update(v)
                else:
                    pg_table[k] = v

        _pg_column_storage(_conn, _regclass_name, columns_dict, pg_version)

        for update in (
            _pg_policies(_conn, table_name, pg_schema),
            _pg_table_grants(_conn, _regclass_name),
            _pg_triggers(_conn, _regclass_name),
        ):
            table_entry.update((k, v) for k, v in update.items() if k not in table_entry)

        if pg_table:
            table_entry["pg_table"] = pg_table
    except Exception:
        _snapshot_logger.warning('PG table extraction failed for %s', _regclass_name)
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


def get_pg_constraint_index_names(
    engine: Any,
    connection: Any,
    own_engine: bool,
    _regclass_name: str,
) -> set[str]:
    """Get index names backing PG constraints (PK, UQ, EXCLUDE)."""
    from sqlalchemy import text

    _pg_c = None
    constraint_index_names: set[str] = set()
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
    return constraint_index_names


def enrich_pg_constraint_extras(
    constraints: dict[str, dict[str, Any]],
    table_name: str,
    _regclass_name: str,
    engine: Any,
    connection: Any,
    own_engine: bool,
) -> None:
    """Add no_inherit, deferrable, initially_deferred, and validated to PG constraints."""
    from sqlalchemy import text

    _pg_conn = engine.connect() if own_engine else connection
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


def _pg_extract_enums(inspector: Any) -> dict[str, Any]:
    enums: dict[str, Any] = {}
    try:
        for enum_info in inspector.get_enums():
            enums[enum_info["name"]] = list(enum_info.get("labels", []))
    except Exception:
        pass
    return enums


def _pg_extract_domains(conn: Any) -> dict[str, Any]:
    from sqlalchemy import text
    domains: dict[str, Any] = {}
    try:
        domain_rows = conn.execute(
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
        check_rows = conn.execute(
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
    return domains


def _pg_extract_views(inspector: Any, conn: Any, pg_schema: str | None, tables: dict[str, Any]) -> None:
    from sqlalchemy import text

    inspect_kw = {"schema": pg_schema} if pg_schema else {}
    view_names = inspector.get_view_names(**inspect_kw)
    for view_name in view_names:
        if view_name in tables:
            continue
        view_columns = inspector.get_columns(view_name, **inspect_kw)
        view_pk = inspector.get_pk_constraint(view_name, **inspect_kw)
        view_pk_columns = set(view_pk.get("constrained_columns", []) or [])

        from dbwarden.engine.snapshot.type_normalize import normalize_type
        from dbwarden.engine.backends.postgresql.extract import _is_autoincrement

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
            vrow = conn.execute(
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


def _pg_extract_matviews(conn: Any, inspector: Any, pg_schema: str | None, tables: dict[str, Any]) -> None:
    from sqlalchemy import text

    inspect_kw = {"schema": pg_schema} if pg_schema else {}

    from dbwarden.engine.snapshot.type_normalize import normalize_type
    from dbwarden.engine.backends.postgresql.extract import _is_autoincrement

    try:
        matview_q = "SELECT relname FROM pg_class WHERE relkind = 'm'"
        if pg_schema:
            matview_q += " AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = :schema)"
            matview_rows = conn.execute(text(matview_q), {"schema": pg_schema})
        else:
            matview_rows = conn.execute(text(matview_q + " AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = current_schema())"))
        for mrow in matview_rows:
            mv_name = mrow[0]
            if mv_name in tables:
                mv_entry = tables[mv_name]
                try:
                    vdef_row = conn.execute(
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
                    vdef_row = conn.execute(
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


def _pg_extract_sequences(conn: Any) -> dict[str, Any]:
    from sqlalchemy import text
    sequences: dict[str, Any] = {}
    try:
        seq_rows = conn.execute(
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
        _snapshot_logger.warning('sequences extraction failed: %s', e)
    return sequences


def _pg_extract_composite_types(conn: Any) -> dict[str, Any]:
    from sqlalchemy import text
    composite_types: dict[str, Any] = {}
    try:
        comp_rows = conn.execute(
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
        _snapshot_logger.warning('composite_types extraction failed: %s', e)
        try:
            conn.rollback()
        except Exception:
            pass
    return composite_types


def _pg_extract_functions(conn: Any) -> dict[str, Any]:
    from sqlalchemy import text
    functions: dict[str, Any] = {}
    try:
        func_rows = conn.execute(
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
        _snapshot_logger.warning('functions extraction failed: %s', e)
        try:
            conn.rollback()
        except Exception:
            pass
    return functions


def _pg_extract_roles(conn: Any) -> dict[str, Any]:
    from sqlalchemy import text
    roles: dict[str, Any] = {}
    try:
        role_rows = conn.execute(
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
        _snapshot_logger.warning('roles extraction failed: %s', e)
        try:
            conn.rollback()
        except Exception:
            pass
    return roles


def _pg_extract_default_privileges(conn: Any) -> dict[str, Any]:
    from sqlalchemy import text
    default_privileges: dict[str, Any] = {}
    try:
        dp_rows = conn.execute(
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
        _snapshot_logger.warning('default_privileges extraction failed: %s', e)
        try:
            conn.rollback()
        except Exception:
            pass
    return default_privileges


def _pg_extract_schema_grants(conn: Any) -> dict[str, Any]:
    from sqlalchemy import text
    schema_grants: dict[str, Any] = {}
    try:
        sg_rows = conn.execute(
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
        _snapshot_logger.warning('schema_grants extraction failed: %s', e)
        try:
            conn.rollback()
        except Exception:
            pass
    return schema_grants


def _pg_extract_event_triggers(conn: Any) -> dict[str, Any]:
    from sqlalchemy import text
    event_triggers: dict[str, Any] = {}
    try:
        et_rows = conn.execute(
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
        _snapshot_logger.warning('event_triggers extraction failed: %s', e)
        try:
            conn.rollback()
        except Exception:
            pass
    return event_triggers


def _pg_extract_extended_stats(conn: Any, pg_version: tuple[int, int] | None) -> dict[str, Any]:
    from sqlalchemy import text
    extended_stats: dict[str, Any] = {}
    try:
        if pg_version and pg_version >= (14, 0):
            stats_rows = conn.execute(
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
            stats_rows = conn.execute(
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
        _snapshot_logger.warning('extended_stats extraction failed: %s', e)
        try:
            conn.rollback()
        except Exception:
            pass
    return extended_stats


def extract_pg_database_objects(
    engine: Any,
    connection: Any,
    own_engine: bool,
    inspector: Any,
    pg_schema: str | None,
    pg_version: tuple[int, int] | None,
    tables: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Extract PostgreSQL-only database objects.

    Returns a dict with keys: enums, domains, sequences, composite_types,
    functions, roles, default_privileges, schema_grants, event_triggers,
    extended_stats.
    """
    enums = _pg_extract_enums(inspector)
    _pg_conn = None
    try:
        _pg_conn = engine.connect() if own_engine else connection
        domains = _pg_extract_domains(_pg_conn)
        _pg_extract_views(inspector, _pg_conn, pg_schema, tables)
        _pg_extract_matviews(_pg_conn, inspector, pg_schema, tables)
        sequences = _pg_extract_sequences(_pg_conn)
        composite_types = _pg_extract_composite_types(_pg_conn)
        functions = _pg_extract_functions(_pg_conn)
        roles = _pg_extract_roles(_pg_conn)
        default_privileges = _pg_extract_default_privileges(_pg_conn)
        schema_grants = _pg_extract_schema_grants(_pg_conn)
        event_triggers = _pg_extract_event_triggers(_pg_conn)
        extended_stats = _pg_extract_extended_stats(_pg_conn, pg_version)
    except Exception:
        pass
    finally:
        if own_engine and _pg_conn is not None:
            _pg_conn.close()
    return {
        "enums": enums,
        "domains": domains,
        "sequences": sequences,
        "composite_types": composite_types,
        "functions": functions,
        "roles": roles,
        "default_privileges": default_privileges,
        "schema_grants": schema_grants,
        "event_triggers": event_triggers,
        "extended_stats": extended_stats,
    }
