"""SQLite-specific schema extraction helpers."""
from __future__ import annotations

from typing import Any

from dbwarden.logging import get_component_logger

_snapshot_logger = get_component_logger("snapshot")


def enrich_table_sqlite(
    table_entry: dict[str, Any],
    columns_dict: dict[str, Any],
    table_name: str,
    engine_conn: Any,
    own_engine: bool,
    connection: Any,
) -> None:
    """Enrich a table entry with SQLite-specific metadata.

    Parses CREATE TABLE SQL for table options and column metadata,
    extracts index DDL, and fixes up FK actions.
    """
    from dbwarden.engine.backends.sqlite.extract import (
        get_sqlite_index_ddl,
        get_sqlite_table_sql,
        parse_sqlite_column_meta,
        parse_sqlite_table_options,
    )

    for col_entry in columns_dict.values():
        if col_entry.get("primary_key"):
            col_entry["nullable"] = False

    _conn = engine_conn.connect() if own_engine and engine_conn is not None else connection
    try:
        create_sql = get_sqlite_table_sql(_conn, table_name)
        sq_table = parse_sqlite_table_options(create_sql)
        if sq_table:
            table_entry["sq_table"] = sq_table
        index_ddl = get_sqlite_index_ddl(_conn, table_name)
        if index_ddl:
            table_entry["sq_index_ddl"] = index_ddl
        for col_name, sq_column in parse_sqlite_column_meta(create_sql).items():
            col_entry = columns_dict.get(col_name)
            if col_entry is None:
                continue
            if sq_column.pop("sq_autoincrement", False):
                col_entry["autoincrement"] = True
            if sq_column:
                col_entry["sq_column"] = sq_column
    finally:
        if own_engine and _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass


def fixup_sqlite_fk_actions(
    constraints: dict[str, dict[str, Any]],
    table_name: str,
    engine_conn: Any,
    own_engine: bool,
    connection: Any,
) -> None:
    """Fix up ON DELETE/ON UPDATE actions for SQLite foreign keys.

    SQLAlchemy reports SQLite foreign keys with empty options, so we
    parse the CREATE TABLE SQL directly to get the real actions.
    """
    from dbwarden.engine.backends.sqlite.extract import (
        get_sqlite_foreign_key_actions,
    )

    _fk_conn = engine_conn.connect() if own_engine and engine_conn is not None else connection
    try:
        actions = get_sqlite_foreign_key_actions(_fk_conn, table_name)
    finally:
        if own_engine and _fk_conn is not None:
            try:
                _fk_conn.close()
            except Exception:
                pass
    for constraint in constraints.values():
        if constraint.get("type") != "foreign_key" or constraint.get("table") != table_name:
            continue
        action = actions.get(tuple(constraint.get("columns", [])))
        if action:
            constraint["on_delete"] = action["on_delete"]
            constraint["on_update"] = action["on_update"]
