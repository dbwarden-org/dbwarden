from __future__ import annotations

from dbwarden.config import get_database
from dbwarden.connection.connection import get_db_connection
from dbwarden.exceptions import DBDisconnectedError
from dbwarden.output import error, sql, subsection, warning


def snapshot_cmd(
    table_name: str,
    database: str | None = None,
) -> None:
    config = get_database(database)
    db_type = config.database_type

    try:
        with get_db_connection(database) as connection:
            if db_type == "clickhouse":
                _snapshot_clickhouse(connection, table_name)
            else:
                _snapshot_generic(connection, table_name)
    except DBDisconnectedError:
        raise


def _snapshot_generic(connection, table_name: str) -> None:
    from sqlalchemy import inspect

    inspector = inspect(connection)
    all_tables = inspector.get_table_names()
    if table_name not in all_tables:
        error(f"Table '{table_name}' not found in database.")
        return

    columns = inspector.get_columns(table_name)
    indexes = inspector.get_indexes(table_name)
    foreign_keys = inspector.get_foreign_keys(table_name)

    lines = [f"CREATE TABLE {table_name} ("]
    col_defs = []
    for col in columns:
        col_type = str(col["type"])
        nullable = "" if col.get("nullable", True) else " NOT NULL"
        default = ""
        if col.get("default") is not None and str(col["default"]) != "None":
            default = f" DEFAULT {col['default']}"
        col_defs.append(f"    {col['name']} {col_type}{nullable}{default}")
    lines.append(",\n".join(col_defs))
    lines.append(");")

    sql("\n".join(lines))

    if indexes:
        subsection("Indexes")
        for idx in indexes:
            cols = ", ".join(idx["column_names"])
            unique = "UNIQUE " if idx.get("unique") else ""
            sql(
                f"CREATE {unique}INDEX {idx['name']} ON {table_name} ({cols});",
            )

    if foreign_keys:
        subsection("Foreign Keys")
        for fk in foreign_keys:
            cols = ", ".join(fk["constrained_columns"])
            ref_cols = ", ".join(fk["referred_columns"])
            sql(
                f"ALTER TABLE {table_name} ADD CONSTRAINT {fk['name']} "
                f"FOREIGN KEY ({cols}) REFERENCES {fk['referred_table']} ({ref_cols});",
            )


def _snapshot_clickhouse(connection, table_name: str) -> None:
    from sqlalchemy import text

    result = connection.execute(
        text(
            "SELECT create_table_query FROM system.tables "
            "WHERE database = currentDatabase() AND name = :name"
        ),
        parameters={"name": table_name},
    )
    row = result.fetchone()
    if not row:
        error(f"Table '{table_name}' not found in database.")
        return

    sql(row.create_table_query)
