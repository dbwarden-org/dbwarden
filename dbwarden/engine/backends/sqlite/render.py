"""SQL rendering for the SQLite backend.

SQLite is dynamically typed, but the declared type still matters: it selects the
column affinity, and in a ``STRICT`` table it is checked and restricted to a
fixed set of names.  Everything that turns a model column into SQLite DDL text
lives here so the rebuild builder and the CREATE TABLE path render identically -
a rebuild that renders columns differently from the original CREATE would show
up as a permanent diff.
"""

from __future__ import annotations

import re
from typing import Any

# The only type names a STRICT table accepts.
STRICT_TYPES: frozenset[str] = frozenset({"INT", "INTEGER", "REAL", "TEXT", "BLOB", "ANY"})

_RESERVED: frozenset[str] = frozenset({
    "abort", "action", "add", "after", "all", "alter", "always", "analyze", "and",
    "as", "asc", "attach", "autoincrement", "before", "begin", "between", "by",
    "cascade", "case", "cast", "check", "collate", "column", "commit", "conflict",
    "constraint", "create", "cross", "current", "current_date", "current_time",
    "current_timestamp", "database", "default", "deferrable", "deferred", "delete",
    "desc", "detach", "distinct", "do", "drop", "each", "else", "end", "escape",
    "except", "exclude", "exclusive", "exists", "explain", "fail", "filter",
    "first", "following", "for", "foreign", "from", "full", "generated", "glob",
    "group", "groups", "having", "if", "ignore", "immediate", "in", "index",
    "indexed", "initially", "inner", "insert", "instead", "intersect", "into",
    "is", "isnull", "join", "key", "last", "left", "like", "limit", "match",
    "materialized", "natural", "no", "not", "nothing", "notnull", "null", "nulls",
    "of", "offset", "on", "or", "order", "others", "outer", "over", "partition",
    "plan", "pragma", "preceding", "primary", "query", "raise", "range",
    "recursive", "references", "regexp", "reindex", "release", "rename",
    "replace", "restrict", "returning", "right", "rollback", "row", "rows",
    "savepoint", "select", "set", "table", "temp", "temporary", "then", "ties",
    "to", "transaction", "trigger", "unbounded", "union", "unique", "update",
    "using", "vacuum", "values", "view", "virtual", "when", "where", "window",
    "with", "without",
})

_SAFE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def quote_sq(name: str) -> str:
    """Quote a SQLite identifier, leaving plain lowercase names bare."""
    if _SAFE_IDENTIFIER.match(name) and name.lower() not in _RESERVED:
        return name
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def sqlite_affinity(type_str: str) -> str:
    """Return the SQLite affinity for a declared type, per the rules in the
    "Determination Of Column Affinity" section of the SQLite datatype docs."""
    upper = (type_str or "").upper()
    if "INT" in upper:
        return "INTEGER"
    if "CHAR" in upper or "CLOB" in upper or "TEXT" in upper:
        return "TEXT"
    if "BLOB" in upper or not upper:
        return "BLOB"
    if "REAL" in upper or "FLOA" in upper or "DOUB" in upper:
        return "REAL"
    return "NUMERIC"


def render_sqlite_column_type(type_str: str, *, strict: bool = False) -> str:
    """Render a declared column type for SQLite.

    Outside a STRICT table the declared type is passed through: SQLite accepts
    any type name and only reads it for affinity, so preserving ``VARCHAR(64)``
    keeps the schema readable and keeps round-trips stable.  Inside a STRICT
    table only :data:`STRICT_TYPES` are legal, so the type collapses to its
    affinity.
    """
    declared = (type_str or "").strip()
    if not strict:
        return declared or "BLOB"

    upper = declared.upper()
    base = upper.split("(", 1)[0].strip()
    if base in STRICT_TYPES:
        return base

    # BOOLEAN and the date/time types all have NUMERIC affinity, which STRICT
    # does not accept.  Route them to the representation SQLAlchemy already
    # reads and writes for them rather than to a bare NUMERIC.
    if base in ("BOOLEAN", "BOOL"):
        return "INTEGER"
    if base in ("DATE", "DATETIME", "TIMESTAMP", "TIME"):
        return "TEXT"

    affinity = sqlite_affinity(declared)
    if affinity == "NUMERIC":
        return "REAL"
    return affinity


def _base_type_name(type_str: str) -> str:
    return (type_str or "").split("(", 1)[0].strip().casefold()


def declared_type_for(col: Any) -> str:
    """The type to render for a column.

    A column read back from a live database carries the type exactly as it was
    declared, while the reflected type has already lost its length and its case:
    rendering the reflected form would rewrite ``VARCHAR(255)`` as ``varchar``.
    The declared form is only used when it still describes the same type - once
    a migration changes the column's type, the recorded declaration is stale and
    rendering it would drop the change on the floor.
    """
    sq_meta = getattr(col, "sq_meta", None) or {}
    declared = sq_meta.get("sq_declared_type")
    if not declared:
        return col.type
    if _base_type_name(declared) == _base_type_name(col.type):
        return declared
    return col.type


def is_generated(col: Any) -> bool:
    return bool((getattr(col, "sq_meta", None) or {}).get("sq_generated"))


def is_stored_generated(col: Any) -> bool:
    sq_meta = getattr(col, "sq_meta", None) or {}
    if not sq_meta.get("sq_generated"):
        return False
    return str(sq_meta.get("sq_generated_mode", "STORED")).upper() != "VIRTUAL"


def render_sqlite_column_def(
    col: Any,
    *,
    strict: bool = False,
    inline_primary_key: bool = False,
    autoincrement: bool = False,
    table_has_unique_constraint: bool = False,
) -> str:
    """Render one column definition for a CREATE TABLE body.

    ``table_has_unique_constraint`` says the table already declares a
    single-column UNIQUE for this column, so the inline keyword is left off and
    the table constraint carries it - otherwise the table gets two.
    """
    sq_meta = getattr(col, "sq_meta", None) or {}
    declared = declared_type_for(col)
    parts = [quote_sq(col.name), render_sqlite_column_type(declared, strict=strict)]

    generated = sq_meta.get("sq_generated")
    if generated:
        mode = str(sq_meta.get("sq_generated_mode", "STORED")).upper()
        if mode not in ("STORED", "VIRTUAL"):
            mode = "STORED"
        # A generated column takes no default and cannot be a key, so the
        # remaining constraints do not apply to it.
        parts.append(f"GENERATED ALWAYS AS ({generated}) {mode}")
        collate = sq_meta.get("sq_collate")
        if collate:
            parts.append(f"COLLATE {collate}")
        return " ".join(parts)

    if not col.nullable:
        parts.append("NOT NULL")

    if inline_primary_key and col.primary_key:
        parts.append("PRIMARY KEY")
        if autoincrement:
            parts.append("AUTOINCREMENT")
    elif col.unique and not table_has_unique_constraint:
        parts.append("UNIQUE")

    if col.default is not None and str(col.default) != "":
        parts.append(f"DEFAULT {render_sqlite_default(col.default)}")

    collate = sq_meta.get("sq_collate")
    if collate:
        parts.append(f"COLLATE {collate}")

    if col.foreign_key:
        parts.append(f"REFERENCES {col.foreign_key}")
        if col.fk_on_delete and col.fk_on_delete != "NO ACTION":
            parts.append(f"ON DELETE {col.fk_on_delete}")
        if col.fk_on_update and col.fk_on_update != "NO ACTION":
            parts.append(f"ON UPDATE {col.fk_on_update}")

    return " ".join(parts)


def render_sqlite_default(default: Any) -> str:
    """Render a DEFAULT value.  SQLite requires non-constant defaults to be
    parenthesised, so an expression is wrapped unless it already is."""
    text = str(default).strip()
    if not text:
        return "NULL"
    if re.match(r"^-?\d+(\.\d+)?$", text):
        return text
    if text.upper() in ("NULL", "TRUE", "FALSE", "CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME"):
        return text.upper()
    if text.startswith("'") and text.endswith("'"):
        return text
    if text.startswith("(") and text.endswith(")"):
        return text
    if re.match(r"^\w+\(.*\)$", text):
        return f"({text})"
    return text


def render_sqlite_table_suffix(table: Any) -> str:
    """Render the ``WITHOUT ROWID`` / ``STRICT`` table-options suffix."""
    sq_table = getattr(table, "sq_table", None) or {}
    options: list[str] = []
    if sq_table.get("sq_without_rowid"):
        options.append("WITHOUT ROWID")
    if sq_table.get("sq_strict"):
        options.append("STRICT")
    if not options:
        return ""
    return " " + ", ".join(options)


def is_strict_table(table: Any) -> bool:
    return bool((getattr(table, "sq_table", None) or {}).get("sq_strict"))


def sqlite_autoincrement_column(table: Any) -> str | None:
    """Return the column that should carry ``AUTOINCREMENT``, if any.

    SQLite only allows ``AUTOINCREMENT`` on a single-column ``INTEGER PRIMARY
    KEY``, and never on a ``WITHOUT ROWID`` table.  A model asking for it
    anywhere else gets a plain rowid alias, which is what SQLAlchemy expects.
    """
    if (getattr(table, "sq_table", None) or {}).get("sq_without_rowid"):
        return None
    pk_columns = [c for c in table.columns if c.primary_key]
    if len(pk_columns) != 1:
        return None
    col = pk_columns[0]
    if col.autoincrement is not True:
        return None
    if sqlite_affinity(declared_type_for(col)) != "INTEGER":
        return None
    return col.name
