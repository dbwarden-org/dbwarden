"""Read SQLite-specific schema facts out of a live database.

``WITHOUT ROWID``, ``STRICT``, generated columns and column collations are not
reported by the SQLAlchemy inspector (and its generated-column reflection
mis-parses a table with more than one of them), so they are read straight from
the ``CREATE TABLE`` text that SQLite stores in ``sqlite_master``.
"""

from __future__ import annotations

import re
from typing import Any


def _split_top_level(text: str, separator: str = ",") -> list[str]:
    """Split on ``separator`` outside of parentheses and quotes."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    for char in text:
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in "'\"`":
            quote = char
            current.append(char)
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == separator and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def _split_body_and_tail(create_sql: str) -> tuple[str, str]:
    """Return the parenthesised column list and the trailing table options."""
    start = create_sql.find("(")
    if start == -1:
        return "", ""
    depth = 0
    quote: str | None = None
    for index in range(start, len(create_sql)):
        char = create_sql[index]
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in "'\"`":
            quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return create_sql[start + 1:index], create_sql[index + 1:]
    return create_sql[start + 1:], ""


_IDENTIFIER = re.compile(r'^\s*(?:"([^"]+)"|`([^`]+)`|\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_$]*))')

_TABLE_CONSTRAINT_START = re.compile(
    r"^\s*(CONSTRAINT|PRIMARY\s+KEY|UNIQUE|CHECK|FOREIGN\s+KEY)\b", re.IGNORECASE
)

_GENERATED = re.compile(
    r"\b(?:GENERATED\s+ALWAYS\s+)?AS\s*\((.*)\)\s*(STORED|VIRTUAL)?\s*$",
    re.IGNORECASE | re.DOTALL,
)

_COLLATE = re.compile(r"\bCOLLATE\s+(\w+)", re.IGNORECASE)

_AUTOINCREMENT = re.compile(r"\bAUTOINCREMENT\b", re.IGNORECASE)

# The declared type sits between the column name and the first column
# constraint keyword.
_CONSTRAINT_KEYWORD = re.compile(
    r"\b(CONSTRAINT|PRIMARY|NOT|NULL|UNIQUE|CHECK|DEFAULT|COLLATE|REFERENCES|"
    r"GENERATED|AS)\b",
    re.IGNORECASE,
)


def _column_name(definition: str) -> str | None:
    match = _IDENTIFIER.match(definition)
    if not match:
        return None
    return next(group for group in match.groups() if group is not None)


def parse_sqlite_table_options(create_sql: str) -> dict[str, Any]:
    """Return the ``WITHOUT ROWID`` / ``STRICT`` options of a CREATE TABLE.

    Only the text after the column list is inspected, so a column named
    ``strict`` does not read as a STRICT table.
    """
    _, tail = _split_body_and_tail(create_sql or "")
    options: dict[str, Any] = {}
    upper = tail.upper()
    if re.search(r"\bWITHOUT\s+ROWID\b", upper):
        options["sq_without_rowid"] = True
    if re.search(r"\bSTRICT\b", upper):
        options["sq_strict"] = True
    return options


def _declared_type(definition: str, name_end: int) -> str:
    """The type text between the column name and its first constraint."""
    rest = definition[name_end:]
    match = _CONSTRAINT_KEYWORD.search(rest)
    if match:
        rest = rest[: match.start()]
    return " ".join(rest.split())


def parse_sqlite_column_meta(create_sql: str) -> dict[str, dict[str, Any]]:
    """Return per-column metadata SQLAlchemy reflection does not report.

    Covers the declared type as written, ``AUTOINCREMENT``, generated columns
    and collation.  A table rebuild renders the table from this, so anything
    missing here is silently dropped by the rebuild.
    """
    body, _ = _split_body_and_tail(create_sql or "")
    result: dict[str, dict[str, Any]] = {}
    for definition in _split_top_level(body):
        if not definition.strip() or _TABLE_CONSTRAINT_START.match(definition):
            continue
        name_match = _IDENTIFIER.match(definition)
        if not name_match:
            continue
        name = next(group for group in name_match.groups() if group is not None)
        meta: dict[str, Any] = {}

        declared = _declared_type(definition, name_match.end())
        if declared:
            meta["sq_declared_type"] = declared

        if _AUTOINCREMENT.search(definition):
            meta["sq_autoincrement"] = True

        generated = _GENERATED.search(definition.strip())
        if generated:
            meta["sq_generated"] = generated.group(1).strip()
            mode = (generated.group(2) or "STORED").upper()
            if mode != "STORED":
                meta["sq_generated_mode"] = mode

        collate = _COLLATE.search(definition)
        if collate:
            meta["sq_collate"] = collate.group(1)

        if meta:
            result[name] = meta
    return result


def get_sqlite_index_ddl(connection, table_name: str) -> dict[str, str]:
    """The stored ``CREATE INDEX`` text for every index on a table.

    SQLAlchemy's SQLite reflection drops expression indexes entirely and loses
    ``DESC`` and ``COLLATE`` inside an index, so a rebuild recreates indexes
    from this text rather than from the reflected shape.  Implicit indexes
    (``sqlite_autoindex_*``, which back UNIQUE constraints) have no DDL and are
    recreated by the constraint itself.
    """
    from sqlalchemy import text

    try:
        rows = connection.execute(
            text(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='index' AND tbl_name=:t AND sql IS NOT NULL"
            ),
            {"t": table_name},
        ).fetchall()
    except Exception:
        return {}
    return {str(row[0]): str(row[1]) for row in rows if row[0] and row[1]}


def get_sqlite_foreign_key_actions(
    connection, table_name: str
) -> dict[tuple[str, ...], dict[str, str]]:
    """``ON DELETE`` / ``ON UPDATE`` per foreign key, keyed by column tuple.

    SQLAlchemy reports SQLite foreign keys with an empty ``options`` dict, so
    the referential actions are read from ``PRAGMA foreign_key_list``.
    """
    from sqlalchemy import text

    try:
        rows = connection.execute(
            text(f'PRAGMA foreign_key_list("{table_name.replace(chr(34), chr(34) * 2)}")')
        ).fetchall()
    except Exception:
        return {}

    by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        mapping = row._mapping
        entry = by_id.setdefault(
            int(mapping["id"]),
            {
                "columns": [],
                "on_update": str(mapping["on_update"] or "NO ACTION"),
                "on_delete": str(mapping["on_delete"] or "NO ACTION"),
            },
        )
        entry["columns"].append((int(mapping["seq"]), str(mapping["from"])))

    result: dict[tuple[str, ...], dict[str, str]] = {}
    for entry in by_id.values():
        columns = tuple(name for _, name in sorted(entry["columns"]))
        result[columns] = {
            "on_delete": entry["on_delete"],
            "on_update": entry["on_update"],
        }
    return result


def get_sqlite_table_sql(connection, table_name: str) -> str:
    """The stored ``CREATE TABLE`` text for a table, or an empty string."""
    from sqlalchemy import text

    try:
        row = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table_name},
        ).fetchone()
    except Exception:
        return ""
    if not row or not row[0]:
        return ""
    return str(row[0])


def extract_sqlite_column_meta(
    inspector,
    connection,
    table_name: str,
    raw_columns: list[dict] | None = None,
) -> dict[str, dict[str, Any]]:
    """Extract SQLite-specific column metadata (generated columns, collation)."""
    return parse_sqlite_column_meta(get_sqlite_table_sql(connection, table_name))


def extract_sqlite_table_meta(
    connection,
    table_name: str,
) -> dict[str, Any]:
    """Extract SQLite-specific table metadata (WITHOUT ROWID, STRICT)."""
    return parse_sqlite_table_options(get_sqlite_table_sql(connection, table_name))
