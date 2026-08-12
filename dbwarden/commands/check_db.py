from typing import Any

from sqlalchemy import inspect

from dbwarden.config import get_database
from dbwarden.database.connection import get_db_connection
from dbwarden.exceptions import DBDisconnectedError
from dbwarden.logging import get_logger
from dbwarden.output import data_table, plain, render, section, warning


def check_db_cmd(output_format: str = "txt", database: str | None = None) -> None:
    """
    Inspect the live database schema.

    Args:
        output_format: Output format (json, yaml, sql, txt).
        database: Target database name.
    """
    logger = get_logger()
    config = get_database(database)
    db_name = database or "default"

    try:
        with get_db_connection(database) as connection:
            inspector = inspect(connection)
            tables = inspector.get_table_names()

            schema_info = {}
            for table in tables:
                columns = inspector.get_columns(table)
                indexes = inspector.get_indexes(table)
                foreign_keys = inspector.get_foreign_keys(table)

                schema_info[table] = {
                    "columns": [
                        {
                            "name": col["name"],
                            "type": str(col["type"]),
                            "nullable": col["nullable"],
                            "default": str(col.get("default", None)),
                        }
                        for col in columns
                    ],
                    "indexes": [
                        {"name": idx["name"], "columns": idx["column_names"]}
                        for idx in indexes
                    ],
                    "foreign_keys": [
                        {
                            "name": fk["name"],
                            "columns": fk["constrained_columns"],
                            "referred_table": fk["referred_table"],
                            "referred_columns": fk["referred_columns"],
                        }
                        for fk in foreign_keys
                    ],
                }
    except DBDisconnectedError:
        raise

    section(f"Database Schema: {db_name}")

    if output_format == "json":
        import json

        plain(json.dumps(schema_info, indent=2, default=str))
    elif output_format == "yaml":
        import yaml

        plain(yaml.dump(schema_info, default_flow_style=False))
    elif output_format == "txt":
        _print_txt(schema_info)
    else:
        raise ValueError(f"Unknown output format: {output_format}")


def _print_txt(schema_info: dict[str, Any]) -> None:
    """Print schema in text format."""
    for table, info in schema_info.items():
        section(f"Table: {table}")

        render(
            data_table(
                "Columns",
                ("Name", "Type", "Nullable", "Default"),
                (
                    (
                        col["name"],
                        col["type"],
                        "NULL" if col["nullable"] else "NOT NULL",
                        col["default"] if col["default"] and col["default"] != "None" else "",
                    )
                    for col in info["columns"]
                ),
            )
        )

        if info["indexes"]:
            render(
                data_table(
                    "Indexes",
                    ("Name", "Columns"),
                    ((idx["name"], ", ".join(idx["columns"])) for idx in info["indexes"]),
                )
            )

        if info["foreign_keys"]:
            render(
                data_table(
                    "Foreign Keys",
                    ("Name", "Columns", "Reference"),
                    (
                        (
                            fk["name"],
                            ", ".join(fk["columns"]),
                            f"{fk['referred_table']}({', '.join(fk['referred_columns'])})",
                        )
                        for fk in info["foreign_keys"]
                    ),
                )
            )
