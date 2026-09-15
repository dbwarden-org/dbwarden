"""Schema snapshot extraction orchestrator.

This module provides the public ``extract_full_schema_snapshot`` function
which dispatches to backend-specific extractors. The heavy lifting lives
in:

- ``extract_common.py`` -- shared column/index/constraint introspection
- ``extract_pg.py`` -- PostgreSQL enrichment and database objects
- ``extract_mysql.py`` -- MySQL/MariaDB enrichment
- ``extract_sqlite.py`` -- SQLite enrichment and FK fixups
- ``extract_ch.py`` -- ClickHouse extraction (fully self-contained)
"""
from __future__ import annotations

from typing import Any

from dbwarden.logging import get_component_logger

_snapshot_logger = get_component_logger("snapshot")

from .extract_ch import _extract_clickhouse_schema_snapshot


def extract_full_schema_snapshot(
    database: str | None = None,
    sqlalchemy_url: str | None = None,
    database_type: str | None = None,
) -> dict[str, Any]:
    """Extract a complete schema snapshot from a live database.

    Dispatches to the appropriate backend extractor based on
    ``database_type``. Returns a dict with ``format_version``,
    ``database_name``, ``database_type``, ``tables``, and backend-specific
    top-level keys (``enums``, ``domains``, etc. for PostgreSQL).
    """
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
        return _extract_clickhouse(database, sqlalchemy_url, db_name)

    engine, inspector, own_engine, conn, conn_context = _setup_connection(
        database, sqlalchemy_url, database_type, connection=connection
    )

    try:
        pg_schema = None
        pg_version: tuple[int, int] | None = None
        if database_type == "postgresql":
            try:
                from dbwarden.config import get_database
                pg_schema = get_database(database).postgres_schema
            except Exception:
                pass

        _schema_kw = {"schema": pg_schema} if pg_schema else {}
        _schema_key = pg_schema

        # Batch introspection: call get_multi_* once instead of per-table.
        multi_pks = inspector.get_multi_pk_constraint(**_schema_kw)
        multi_cols = inspector.get_multi_columns(**_schema_kw)
        multi_indexes = inspector.get_multi_indexes(**_schema_kw)
        multi_fks = inspector.get_multi_foreign_keys(**_schema_kw)
        multi_uqs = inspector.get_multi_unique_constraints(**_schema_kw)
        multi_cks = inspector.get_multi_check_constraints(**_schema_kw)
        multi_batch = {
            "pks": multi_pks, "cols": multi_cols, "indexes": multi_indexes,
            "fks": multi_fks, "uqs": multi_uqs, "cks": multi_cks,
            "schema_key": _schema_key,
        }

        if database_type == "postgresql":
            try:
                from sqlalchemy import text
                _vc = engine.connect() if own_engine and engine is not None else conn
                ver_row = _vc.execute(text("SELECT current_setting('server_version_num')")).scalar()
                if ver_row:
                    ver_int = int(ver_row)
                    pg_version = (ver_int // 10000, (ver_int // 100) % 100)
                if own_engine and engine is not None:
                    _vc.close()
            except Exception:
                pass

        tables = _extract_tables(
            inspector, engine, conn, own_engine, database_type, database,
            pg_schema, multi_batch,
        )

        all_constraints: dict[str, dict[str, Any]] = {}
        all_indexes: dict[str, dict[str, Any]] = {}

        for table_name in list(tables.keys()):
            table_constraints, table_indexes = _extract_table_constraints(
                inspector, table_name, tables[table_name], engine, conn,
                own_engine, database_type, pg_schema, multi_batch,
            )
            all_constraints.update(table_constraints)
            all_indexes.update(table_indexes)

        result: dict[str, Any] = {
            "format_version": 1,
            "migration_id": "",
            "database_name": db_name,
            "database_type": database_type or "",
            "applied_at": "",
            "tables": tables,
            "enums": {},
            "domains": {},
            "indexes": all_indexes,
            "constraints": all_constraints,
            "sequences": {},
            "composite_types": {},
            "functions": {},
            "roles": {},
            "default_privileges": {},
            "schema_grants": {},
            "event_triggers": {},
            "extended_stats": {},
        }

        if database_type == "postgresql":
            pg_objects = _extract_pg_objects(
                engine, connection, own_engine, inspector, pg_schema, pg_version, tables
            )
            result.update(pg_objects)

        return result
    finally:
        _cleanup(engine, own_engine, conn_context)


def _extract_clickhouse(
    database: str | None,
    sqlalchemy_url: str | None,
    db_name: str,
) -> dict[str, Any]:
    """Extract ClickHouse schema snapshot."""
    if sqlalchemy_url is not None:
        from sqlalchemy import create_engine
        engine = create_engine(sqlalchemy_url)
        try:
            with engine.connect() as connection:
                return _extract_clickhouse_schema_snapshot(connection, db_name)
        finally:
            engine.dispose()

    from dbwarden.connection.connection import get_db_connection
    conn_context = get_db_connection(database)
    connection = conn_context.__enter__()
    try:
        return _extract_clickhouse_schema_snapshot(connection, db_name)
    finally:
        conn_context.__exit__(None, None, None)


def _setup_connection(
    database: str | None,
    sqlalchemy_url: str | None,
    database_type: str | None,
    connection: Any | None = None,
) -> tuple[Any, Any, bool, Any, Any]:
    """Set up the database connection and inspector.

    Returns (engine, inspector, own_engine, connection, conn_context).
    When an existing ``connection`` is provided, it is reused directly
    and ``own_engine`` is False so callers skip connection cleanup.
    """
    from sqlalchemy import inspect

    if sqlalchemy_url is not None and database_type is not None:
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool
        engine = create_engine(sqlalchemy_url, poolclass=NullPool)
        inspector = inspect(engine)
        return engine, inspector, True, None, None

    if connection is not None:
        inspector = inspect(connection)
        return None, inspector, False, connection, None

    from dbwarden.connection.connection import get_db_connection
    conn_context = get_db_connection(database)
    conn = conn_context.__enter__()
    try:
        inspector = inspect(conn)
    except Exception:
        conn_context.__exit__(None, None, None)
        raise

    from dbwarden.config import get_database
    database_type = get_database(database).database_type
    return None, inspector, False, conn, conn_context
        raise

    from dbwarden.config import get_database
    database_type = get_database(database).database_type
    return None, inspector, False, connection, conn_context


def _extract_tables(
    inspector: Any,
    engine: Any,
    connection: Any,
    own_engine: bool,
    database_type: str,
    database: str | None,
    pg_schema: str | None = None,
    multi_batch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract all tables with their columns and backend-specific metadata."""
    from .extract_common import (
        build_table_entry,
        introspect_columns,
    )

    inspect_kw = {"schema": pg_schema} if pg_schema else {}
    table_names = inspector.get_table_names(**inspect_kw)
    _schema_key = multi_batch["schema_key"] if multi_batch else pg_schema

    tables: dict[str, Any] = {}
    for table_name in table_names:
        _regclass_name = f'"{pg_schema}"."{table_name}"' if pg_schema else f'"{table_name}"'

        if multi_batch is not None:
            pk_key = (_schema_key, table_name)
            pk_info = multi_batch["pks"].get(pk_key, {})
        else:
            pk_info = inspector.get_pk_constraint(table_name, **inspect_kw)
        pk_columns = set(pk_info.get("constrained_columns", []) or [])

        if database_type == "postgresql":
            _filter_pg_local_columns(inspector, engine, connection, own_engine, table_name, _regclass_name, pk_columns)

        if multi_batch is not None:
            col_key = (_schema_key, table_name)
            columns_raw = multi_batch["cols"].get(col_key, [])
        else:
            columns_raw = None

        if columns_raw is not None:
            columns_dict = _build_columns_dict(columns_raw, pk_columns)
        else:
            columns_dict = introspect_columns(inspector, table_name, pk_columns, schema=pg_schema)

        if database_type == "postgresql":
            from .extract_pg import enrich_column_pg
            _multi_cols_raw = columns_raw if columns_raw is not None else None
            for col_name, col_entry in columns_dict.items():
                raw_type_str, normalized, col_info = _resolve_col_type(
                    inspector, table_name, col_name, pg_schema, _multi_cols_raw,
                )
                enrich_column_pg(col_entry, col_info, raw_type_str, normalized, engine, connection=connection)

        if database_type in ("mysql", "mariadb"):
            from .extract_mysql import enrich_column_mysql
            _multi_cols_raw = columns_raw if columns_raw is not None else None
            for col_name, col_entry in columns_dict.items():
                col = _find_col_info_batch(table_name, col_name, pg_schema, _multi_cols_raw) or _find_col_info(inspector, table_name, col_name, pg_schema)
                if col:
                    enrich_column_mysql(col_entry, col)

        table_comment = _get_table_comment(inspector, table_name, database_type, pg_schema)
        table_entry = build_table_entry(columns_dict, pk_columns, schema=pg_schema, comment=table_comment)

        if database_type == "postgresql":
            _enrich_pg_table(table_entry, columns_dict, table_name, _regclass_name, pg_schema, None, engine, connection, own_engine, inspector)
        if database_type in ("mysql", "mariadb"):
            from .extract_mysql import enrich_table_mysql
            enrich_table_mysql(table_entry, columns_dict, table_name, engine, own_engine, connection)
        if database_type == "sqlite":
            from .extract_sqlite import enrich_table_sqlite
            enrich_table_sqlite(table_entry, columns_dict, table_name, engine, own_engine, connection)

        tables[table_name] = table_entry

    return tables


def _filter_pg_local_columns(
    inspector: Any, engine: Any, connection: Any, own_engine: bool,
    table_name: str, _regclass_name: str, pk_columns: set[str],
) -> None:
    """Filter out inherited columns for PG tables (no-op, handled in introspect_columns)."""
    pass


def _build_columns_dict(
    columns_raw: list[dict[str, Any]],
    pk_columns: set[str],
) -> dict[str, Any]:
    """Build columns_dict from pre-fetched get_multi_columns result."""
    from dbwarden.engine.snapshot.type_normalize import normalize_type
    from dbwarden.engine.backends.postgresql.extract import _is_autoincrement

    columns_dict: dict[str, Any] = {}
    for col in columns_raw:
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
        columns_dict[col_name] = col_entry
    return columns_dict


def _find_col_info_batch(
    table_name: str, col_name: str, pg_schema: str | None,
    columns_raw: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Find a column's info dict from pre-fetched batch results."""
    if columns_raw is None:
        return None
    for col in columns_raw:
        if col["name"] == col_name:
            return col
    return None


def _find_col_info(inspector: Any, table_name: str, col_name: str, pg_schema: str | None) -> dict[str, Any] | None:
    """Find a column's info dict from the inspector (fallback when batch results unavailable)."""
    inspect_kw = {"schema": pg_schema} if pg_schema else {}
    for col in inspector.get_columns(table_name, **inspect_kw):
        if col["name"] == col_name:
            return col
    return None


def _resolve_col_type(
    inspector: Any, table_name: str, col_name: str, pg_schema: str | None,
    columns_raw: list[dict[str, Any]] | None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Resolve a column's raw type, normalized form, and info dict."""
    from dbwarden.engine.snapshot.type_normalize import normalize_type

    col = _find_col_info_batch(table_name, col_name, pg_schema, columns_raw)
    if col is None:
        col = _find_col_info(inspector, table_name, col_name, pg_schema)
    raw_type_str = str(col.get("type", "")) if col else ""
    normalized = normalize_type(raw_type_str)
    return raw_type_str, normalized, col if col else {}


def _build_indexes_dict(
    raw_indexes: list[dict[str, Any]],
    table_name: str,
    pk_columns: set[str],
    constraint_index_names: set[str],
    database_type: str,
) -> dict[str, dict[str, Any]]:
    """Build indexes dict from pre-fetched get_multi_indexes result."""
    from dbwarden.engine.backends.postgresql.render import _is_expression

    indexes: dict[str, dict[str, Any]] = {}
    for idx in raw_indexes:
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
        for k in ("postgresql_where", "sqlite_where"):
            val = idx_dialect.get(k)
            if val is None:
                continue
            where_sql = val if isinstance(val, str) else str(val)
            if where_sql:
                idx_entry["where"] = where_sql
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
        indexes[f"{table_name}.{idx_name}"] = idx_entry
    return indexes


def _build_fk_dict(
    raw_fks: list[dict[str, Any]],
    table_name: str,
) -> dict[str, dict[str, Any]]:
    """Build FK constraints dict from pre-fetched get_multi_foreign_keys result."""
    constraints: dict[str, dict[str, Any]] = {}
    for fk in raw_fks:
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
    return constraints


def _build_uq_dict(
    raw_uqs: list[dict[str, Any]],
    table_name: str,
) -> dict[str, dict[str, Any]]:
    """Build unique constraints dict from pre-fetched get_multi_unique_constraints result."""
    constraints: dict[str, dict[str, Any]] = {}
    for uq in raw_uqs:
        uq_name = uq.get("name", "")
        if not uq_name:
            columns = list(uq.get("column_names", []) or [])
            if not columns:
                continue
            uq_name = f"uq_{table_name}_{'_'.join(columns)}"
        constraints[f"{table_name}.{uq_name}"] = {
            "type": "unique",
            "name": uq_name,
            "table": table_name,
            "columns": list(uq.get("column_names", [])),
        }
    return constraints


def _build_ck_dict(
    raw_cks: list[dict[str, Any]],
    table_name: str,
) -> dict[str, dict[str, Any]]:
    """Build check constraints dict from pre-fetched get_multi_check_constraints result."""
    import hashlib
    constraints: dict[str, dict[str, Any]] = {}
    for ck in raw_cks:
        ck_name = ck.get("name", "")
        if not ck_name:
            digest = hashlib.sha1(
                str(ck.get("sqltext", "")).encode("utf-8")
            ).hexdigest()[:12]
            ck_name = f"ck_{table_name}_{digest}"
        constraints[f"{table_name}.{ck_name}"] = {
            "type": "check",
            "name": ck_name,
            "table": table_name,
            "columns": [],
            "expression": ck.get("sqltext", ""),
        }
    return constraints


def _get_table_comment(inspector: Any, table_name: str, database_type: str, pg_schema: str | None) -> str | None:
    """Get table comment from the inspector."""
    if database_type != "postgresql":
        return None
    inspect_kw = {"schema": pg_schema} if pg_schema else {}
    try:
        tc = inspector.get_table_comment(table_name, **inspect_kw)
        if tc and tc.get("text"):
            return tc["text"]
    except Exception:
        pass
    return None


def _enrich_pg_table(
    table_entry: dict[str, Any],
    columns_dict: dict[str, Any],
    table_name: str,
    _regclass_name: str,
    pg_schema: str | None,
    pg_version: tuple[int, int] | None,
    engine: Any,
    connection: Any,
    own_engine: bool,
    inspector: Any,
) -> None:
    """Enrich a table entry with PG-specific metadata."""
    from .extract_pg import enrich_table_pg
    table_entry["_inspector"] = inspector
    try:
        enrich_table_pg(table_entry, columns_dict, table_name, _regclass_name, pg_schema, pg_version, engine, connection, own_engine)
    finally:
        table_entry.pop("_inspector", None)


def _extract_table_constraints(
    inspector: Any,
    table_name: str,
    table_entry: dict[str, Any],
    engine: Any,
    connection: Any,
    own_engine: bool,
    database_type: str,
    pg_schema: str | None,
    multi_batch: dict[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Extract indexes and constraints for a single table."""
    from .extract_common import (
        introspect_check_constraints,
        introspect_foreign_keys,
        introspect_indexes,
        introspect_unique_constraints,
    )

    inspect_kw = {"schema": pg_schema} if pg_schema else {}
    _regclass_name = f'"{pg_schema}"."{table_name}"' if pg_schema else f'"{table_name}"'
    _schema_key = multi_batch["schema_key"] if multi_batch else pg_schema

    constraint_index_names: set[str] = set()
    if database_type == "postgresql":
        from .extract_pg import get_pg_constraint_index_names
        constraint_index_names = get_pg_constraint_index_names(engine, connection, own_engine, _regclass_name)

    if multi_batch is not None:
        pk_key = (_schema_key, table_name)
        pk_info = multi_batch["pks"].get(pk_key, {})
    else:
        pk_info = inspector.get_pk_constraint(table_name, **inspect_kw)
    pk_columns = set(pk_info.get("constrained_columns", []) or [])

    if multi_batch is not None:
        idx_key = (_schema_key, table_name)
        raw_indexes = multi_batch["indexes"].get(idx_key, [])
    else:
        raw_indexes = None

    if raw_indexes is not None:
        indexes = _build_indexes_dict(raw_indexes, table_name, pk_columns, constraint_index_names, database_type)
    else:
        indexes = introspect_indexes(
            inspector, table_name, pk_columns, constraint_index_names, database_type,
            engine=engine, connection=connection, own_engine=own_engine, schema=pg_schema,
        )

    if multi_batch is not None:
        fk_key = (_schema_key, table_name)
        raw_fks = multi_batch["fks"].get(fk_key, [])
        raw_uqs = multi_batch["uqs"].get(fk_key, [])
        raw_cks = multi_batch["cks"].get(fk_key, [])
    else:
        raw_fks = raw_uqs = raw_cks = None

    if raw_fks is not None:
        fk_constraints = _build_fk_dict(raw_fks, table_name)
    else:
        fk_constraints = introspect_foreign_keys(inspector, table_name, schema=pg_schema)
    if raw_uqs is not None:
        uq_constraints = _build_uq_dict(raw_uqs, table_name)
    else:
        uq_constraints = introspect_unique_constraints(inspector, table_name, schema=pg_schema)
    if raw_cks is not None:
        ck_constraints = _build_ck_dict(raw_cks, table_name)
    else:
        ck_constraints = introspect_check_constraints(inspector, table_name, schema=pg_schema)

    all_constraints = {**fk_constraints, **uq_constraints, **ck_constraints}

    if database_type == "sqlite":
        from .extract_sqlite import fixup_sqlite_fk_actions
        fixup_sqlite_fk_actions(all_constraints, table_name, engine, own_engine, connection)

    if database_type == "postgresql":
        from .extract_pg import enrich_pg_constraint_extras
        enrich_pg_constraint_extras(all_constraints, table_name, _regclass_name, engine, connection, own_engine)

    return all_constraints, indexes


def _extract_pg_objects(
    engine: Any,
    connection: Any,
    own_engine: bool,
    inspector: Any,
    pg_schema: str | None,
    pg_version: tuple[int, int] | None,
    tables: dict[str, Any],
) -> dict[str, Any]:
    """Extract PostgreSQL-only database objects."""
    from .extract_pg import extract_pg_database_objects
    return extract_pg_database_objects(engine, connection, own_engine, inspector, pg_schema, pg_version, tables)


def _cleanup(engine: Any, own_engine: bool, conn_context: Any) -> None:
    """Clean up connections and engines."""
    if own_engine and engine is not None:
        engine.dispose()
    elif conn_context is not None:
        try:
            conn_context.__exit__(None, None, None)
        except Exception:
            pass
