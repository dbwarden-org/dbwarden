"""MySQL/MariaDB-specific schema extraction helpers."""
from __future__ import annotations

import re
from typing import Any

from dbwarden.logging import get_component_logger

_snapshot_logger = get_component_logger("snapshot")


def enrich_column_mysql(
    col_entry: dict[str, Any],
    col: dict[str, Any],
) -> None:
    """Enrich a column entry with MySQL/MariaDB-specific metadata.

    Adds my_charset, my_collate, and my_unsigned when present.
    """
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


def enrich_table_mysql(
    table_entry: dict[str, Any],
    columns_dict: dict[str, Any],
    table_name: str,
    engine_conn: Any,
    own_engine: bool,
    connection: Any,
) -> None:
    """Enrich a table entry with MySQL/MariaDB-specific metadata.

    Extracts engine, collation, charset, auto_increment, row_format,
    and column-level extras (charset, collation, unsigned, on_update).
    """
    from sqlalchemy import text

    _conn = engine_conn.connect() if own_engine and engine_conn is not None else connection
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
