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

    engine, inspector, own_engine, connection, conn_context = _setup_connection(
        database, sqlalchemy_url, database_type
    )

    try:
        tables, pg_schema, pg_version = _extract_tables(
            inspector, engine, connection, own_engine, database_type, database
        )

        all_constraints: dict[str, dict[str, Any]] = {}
        all_indexes: dict[str, dict[str, Any]] = {}

        for table_name in list(tables.keys()):
            table_constraints, table_indexes = _extract_table_constraints(
                inspector, table_name, tables[table_name], engine, connection,
                own_engine, database_type, pg_schema,
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
) -> tuple[Any, Any, bool, Any, Any]:
    """Set up the database connection and inspector.

    Returns (engine, inspector, own_engine, connection, conn_context).
    """
    from sqlalchemy import inspect

    if sqlalchemy_url is not None and database_type is not None:
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool
        engine = create_engine(sqlalchemy_url, poolclass=NullPool)
        inspector = inspect(engine)
        return engine, inspector, True, None, None

    from dbwarden.connection.connection import get_db_connection
    conn_context = get_db_connection(database)
    connection = conn_context.__enter__()
    try:
        inspector = inspect(connection)
    except Exception:
        conn_context.__exit__(None, None, None)
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
) -> tuple[dict[str, Any], str | None, tuple[int, int] | None]:
    """Extract all tables with their columns and backend-specific metadata.

    Returns (tables, pg_schema, pg_version).
    """
    from .extract_common import (
        build_table_entry,
        introspect_columns,
    )

    pg_schema = None
    pg_version: tuple[int, int] | None = None
    if database_type == "postgresql":
        try:
            from dbwarden.config import get_database
            pg_schema = get_database(database).postgres_schema
        except Exception:
            pass
        try:
            from sqlalchemy import text
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

    tables: dict[str, Any] = {}
    for table_name in table_names:
        _regclass_name = f'"{pg_schema}"."{table_name}"' if pg_schema else f'"{table_name}"'
        pk_info = inspector.get_pk_constraint(table_name, **inspect_kw)
        pk_columns = set(pk_info.get("constrained_columns", []) or [])

        if database_type == "postgresql":
            _filter_pg_local_columns(inspector, engine, connection, own_engine, table_name, _regclass_name, pk_columns)

        columns_dict = introspect_columns(inspector, table_name, pk_columns, schema=pg_schema)

        if database_type == "postgresql":
            from .extract_pg import enrich_column_pg
            for col_name, col_entry in columns_dict.items():
                raw_type_str = str(col.get("type", "")) if (col := _find_col_info(inspector, table_name, col_name, pg_schema)) else ""
                from dbwarden.engine.snapshot.type_normalize import normalize_type
                normalized = normalize_type(raw_type_str)
                enrich_column_pg(col_entry, col if col else {}, raw_type_str, normalized, engine)

        if database_type in ("mysql", "mariadb"):
            from .extract_mysql import enrich_column_mysql
            for col_name, col_entry in columns_dict.items():
                col = _find_col_info(inspector, table_name, col_name, pg_schema)
                if col:
                    enrich_column_mysql(col_entry, col)

        table_comment = _get_table_comment(inspector, table_name, database_type, pg_schema)
        table_entry = build_table_entry(columns_dict, pk_columns, schema=pg_schema, comment=table_comment)

        if database_type == "postgresql":
            _enrich_pg_table(table_entry, columns_dict, table_name, _regclass_name, pg_schema, pg_version, engine, connection, own_engine, inspector)
        if database_type in ("mysql", "mariadb"):
            from .extract_mysql import enrich_table_mysql
            enrich_table_mysql(table_entry, columns_dict, table_name, engine, own_engine, connection)
        if database_type == "sqlite":
            from .extract_sqlite import enrich_table_sqlite
            enrich_table_sqlite(table_entry, columns_dict, table_name, engine, own_engine, connection)

        tables[table_name] = table_entry

    return tables, pg_schema, pg_version


def _filter_pg_local_columns(
    inspector: Any, engine: Any, connection: Any, own_engine: bool,
    table_name: str, _regclass_name: str, pk_columns: set[str],
) -> None:
    """Filter out inherited columns for PG tables (no-op, handled in introspect_columns)."""
    pass


def _find_col_info(inspector: Any, table_name: str, col_name: str, pg_schema: str | None) -> dict[str, Any] | None:
    """Find a column's info dict from the inspector."""
    inspect_kw = {"schema": pg_schema} if pg_schema else {}
    for col in inspector.get_columns(table_name, **inspect_kw):
        if col["name"] == col_name:
            return col
    return None


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
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Extract indexes and constraints for a single table.

    Returns (constraints, indexes).
    """
    from sqlalchemy import text
    from .extract_common import (
        introspect_check_constraints,
        introspect_foreign_keys,
        introspect_indexes,
        introspect_unique_constraints,
    )

    inspect_kw = {"schema": pg_schema} if pg_schema else {}
    _regclass_name = f'"{pg_schema}"."{table_name}"' if pg_schema else f'"{table_name}"'

    constraint_index_names: set[str] = set()
    if database_type == "postgresql":
        from .extract_pg import get_pg_constraint_index_names
        constraint_index_names = get_pg_constraint_index_names(engine, connection, own_engine, _regclass_name)

    pk_info = inspector.get_pk_constraint(table_name, **inspect_kw)
    pk_columns = set(pk_info.get("constrained_columns", []) or [])

    indexes = introspect_indexes(
        inspector, table_name, pk_columns, constraint_index_names, database_type,
        engine=engine, connection=connection, own_engine=own_engine, schema=pg_schema,
    )

    fk_constraints = introspect_foreign_keys(inspector, table_name, schema=pg_schema)
    uq_constraints = introspect_unique_constraints(inspector, table_name, schema=pg_schema)
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
    else:
        try:
            conn_context.__exit__(None, None, None)
        except Exception:
            pass
