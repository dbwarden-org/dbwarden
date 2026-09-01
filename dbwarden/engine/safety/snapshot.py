from __future__ import annotations

from typing import Any

from dbwarden.connection.connection import get_db_connection
from dbwarden.engine.backends.clickhouse.safety import (
    _CH_OPTION_KEY_MAP,
)


def extract_schema_snapshot(database: str | None = None) -> dict[str, dict[str, Any]]:
    from dbwarden.config import get_database
    from dbwarden.engine.snapshot.extract import extract_full_schema_snapshot

    config = get_database(database)
    full = extract_full_schema_snapshot(database=database)
    if config.database_type == "clickhouse":
        snapshot: dict[str, dict[str, Any]] = {}
        for table_name, table in full.get("tables", {}).items():
            ch_opts = table.get("ch_options", {})
            mapped_opts: dict[str, Any] = {}
            for ch_key, value in ch_opts.items():
                ck = _CH_OPTION_KEY_MAP.get(ch_key, ch_key)
                if value is not None:
                    mapped_opts[ck] = value
            snapshot[table_name] = {
                "database_type": "clickhouse",
                "object_type": table.get("object_type", "table"),
                "comment": table.get("comment"),
                "columns": table.get("columns", {}),
                "ch_options": mapped_opts,
            }
        return snapshot
    if config.database_type == "postgresql":
        snapshot: dict[str, dict[str, Any]] = {}
        for table_name, table in full.get("tables", {}).items():
            snapshot[table_name] = {
                "database_type": "postgresql",
                "object_type": "table",
                "comment": table.get("comment"),
                "columns": table.get("columns", {}),
                "pg_table": table.get("pg_table", {}),
                "ch_options": {},
            }
        return snapshot
    return _extract_generic_schema_snapshot(database)


def _extract_generic_schema_snapshot(database: str | None = None) -> dict[str, dict[str, Any]]:
    from sqlalchemy import inspect

    snapshot: dict[str, dict[str, Any]] = {}
    with get_db_connection(database) as connection:
        inspector = inspect(connection)
        for table_name in inspector.get_table_names():
            columns = inspector.get_columns(table_name)
            snapshot[table_name] = {
                "object_type": "table",
                "columns": {
                    col["name"]: {
                        "type": str(col["type"]),
                        "nullable": bool(col.get("nullable", True)),
                        "default": col.get("default"),
                    }
                    for col in columns
                },
                "ch_options": {},
            }
    return snapshot
