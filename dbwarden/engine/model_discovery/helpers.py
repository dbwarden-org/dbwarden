import os
import re
from typing import Any, List

from dbwarden.engine.core.models import ModelTable
from .sql_generation import _qualified_name, generate_add_column_sql
from dbwarden.exceptions import DBWardenConfigError
from dbwarden.models import SchemaDifference


def compare_model_to_database(
    model_tables: List[ModelTable],
    db_tables: dict,
) -> List[SchemaDifference]:
    differences = []

    model_table_names = {t.name for t in model_tables}
    db_table_names = set(db_tables.keys())

    new_tables = model_table_names - db_table_names
    dropped_tables = db_table_names - model_table_names

    for table_name in new_tables:
        for table in model_tables:
            if table.name == table_name:
                for col in table.columns:
                    differences.append(
                        SchemaDifference(
                            type="add_column",
                            table_name=table_name,
                            column_name=col.name,
                            sql=generate_add_column_sql(table_name, col),
                        )
                    )

    for table_name in dropped_tables:
        differences.append(
            SchemaDifference(
                type="drop_table",
                table_name=table_name,
                sql=f"DROP TABLE {table_name}",
            )
        )

    return differences


def extract_tables_from_database(sqlalchemy_url: str) -> dict[str, set[str]]:
    from sqlalchemy import create_engine, inspect

    tables: dict[str, set[str]] = {}

    engine = create_engine(sqlalchemy_url)
    try:
        inspector = inspect(engine)

        for table_name in inspector.get_table_names():
            columns = inspector.get_columns(table_name)
            column_names = {col["name"].lower() for col in columns}
            tables[table_name] = column_names

    except Exception:
        pass
    finally:
        engine.dispose()

    return tables


def _extract_create_table_columns(create_stmt: str) -> tuple[str | None, set[str]]:
    create_match = re.search(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(",
        create_stmt,
        re.IGNORECASE,
    )
    if not create_match:
        return None, set()

    table_name = create_match.group(1)
    start = create_match.end() - 1

    depth = 1
    i = start + 1
    while i < len(create_stmt) and depth > 0:
        if create_stmt[i] == '(':
            depth += 1
        elif create_stmt[i] == ')':
            depth -= 1
        i += 1

    if depth != 0:
        return table_name, set()

    columns_str = create_stmt[start + 1 : i - 1]
    column_names: set[str] = set()

    column_parts = re.split(r",\s*(?![^()]*\))", columns_str)
    for part in column_parts:
        part = part.strip()
        col_match = re.match(r"(\w+)", part, re.IGNORECASE)
        if col_match:
            col_name = col_match.group(1).lower()
            if col_name not in (
                "primary",
                "foreign",
                "unique",
                "check",
                "constraint",
            ):
                column_names.add(col_name)

    return table_name, column_names


def filter_model_tables_by_name(
    tables: list[ModelTable],
    allowed_names: list[str] | None,
) -> list[ModelTable]:
    if allowed_names is None:
        return tables
    allowed = set(allowed_names)
    return [t for t in tables if t.name in allowed]


def validate_model_tables_exist(
    discovered_tables: list[ModelTable],
    configured_names: list[str] | None,
    db_name: str,
) -> None:
    if configured_names is None:
        return
    discovered = {t.name for t in discovered_tables}
    unknown = [n for n in configured_names if n not in discovered]
    if unknown:
        unknown_str = ", ".join(sorted(unknown))
        discovered_str = ", ".join(sorted(discovered)) if discovered else "(none)"
        raise DBWardenConfigError(
            f"Configured model_tables for database '{db_name}' contain unknown tables: "
            f"{unknown_str}. Discovered tables: {discovered_str}"
        )


def extract_tables_from_migrations(migrations_dir: str) -> dict[str, set[str]]:
    from dbwarden.engine.file_parser import parse_upgrade_statements

    tables: dict[str, set[str]] = {}

    if not os.path.exists(migrations_dir):
        return tables

    for filename in sorted(os.listdir(migrations_dir)):
        if not filename.endswith(".sql"):
            continue
        filepath = os.path.join(migrations_dir, filename)
        statements = parse_upgrade_statements(filepath)

        for stmt in statements:
            table_name, column_names = _extract_create_table_columns(stmt)
            if table_name and column_names:
                if table_name in tables:
                    tables[table_name].update(column_names)
                else:
                    tables[table_name] = column_names

    return tables
