from __future__ import annotations

from typing import Any


def render_mysql_column_type(type_str: str, meta: dict[str, Any]) -> str:
    rendered = type_str
    if meta.get("my_unsigned") and "UNSIGNED" not in rendered.upper():
        rendered = f"{rendered} UNSIGNED"
    return rendered


_AUTO_INCREMENT_TYPES: frozenset[str] = frozenset({
    "tinyint",
    "smallint",
    "mediumint",
    "int",
    "int4",
    "integer",
    "bigint",
    "biginteger",
    "smallinteger",
})


def mysql_auto_increment_clause(column: Any, *, is_sole_primary_key: bool) -> str:
    """Return ``" AUTO_INCREMENT"`` for a generated integer primary key.

    MySQL and MariaDB have no ``SERIAL`` column type to carry this the way
    PostgreSQL does, and SQLite infers it from ``INTEGER PRIMARY KEY``. Without
    an explicit clause the same model renders as a plain
    ``id INTEGER NOT NULL PRIMARY KEY`` on these backends, and every insert that
    omits the key fails with "Field 'id' doesn't have a default value" - at
    runtime, long after the migration applied cleanly.

    The condition matches :func:`_postgres_serial_type` so one model produces
    generated keys on every backend that supports them.
    """
    if not is_sole_primary_key or not column.primary_key:
        return ""
    if getattr(column, "autoincrement", None) not in (True, "auto"):
        return ""
    base_type = str(column.type).split("(")[0].strip().lower()
    if base_type not in _AUTO_INCREMENT_TYPES:
        return ""
    return " AUTO_INCREMENT"


def append_mysql_column_attrs(sql: str, meta: dict[str, Any]) -> str:
    if meta.get("my_charset"):
        sql += f" CHARACTER SET {meta['my_charset']}"
    if meta.get("my_collate"):
        sql += f" COLLATE {meta['my_collate']}"
    if meta.get("my_on_update"):
        sql += f" ON UPDATE {meta['my_on_update']}"
    return sql
