from __future__ import annotations

import re
from typing import Any


def _strip_pg_expr_parens(expr: str | None) -> str | None:
    while expr and expr.startswith('(') and expr.endswith(')'):
        depth = 0
        for i, ch in enumerate(expr):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            if depth == 0 and i < len(expr) - 1:
                return expr
        expr = expr[1:-1].strip()
    return expr


def _normalize_view_def(sql: str | None) -> str | None:
    if not sql:
        return sql
    sql = re.sub(r'\s+', ' ', sql).strip()
    sql = sql.rstrip(';').strip()
    sql = re.sub(r'(\w+\([^)]*\))\s+AS\s+\w+', r'\1', sql, flags=re.IGNORECASE)
    return sql.lower()


def _is_autoincrement(column: dict[str, Any]) -> bool:
    type_str = str(column.get("type", "")).lower()
    if any(kw in type_str for kw in ("serial", "bigserial", "smallserial")):
        return True
    if column.get("autoincrement"):
        return True
    default = column.get("default")
    if isinstance(default, str) and "nextval" in default.lower():
        return True
    return False


def _get_generic_type_name(col_type: Any) -> str:
    type_str = str(col_type)
    if hasattr(col_type, "display_args") and hasattr(col_type, "as_generic"):
        try:
            generic = col_type.as_generic()
            if generic is not None:
                return str(generic)
        except Exception:
            pass
    return type_str


def pg_index_sort_option(is_asc: bool | None, nulls_first: bool | None) -> str | None:
    """Render an index column's sort options, or ``None`` when they are default.

    PostgreSQL's null ordering follows the sort direction: ``NULLS LAST`` is
    implied by ``ASC`` and ``NULLS FIRST`` by ``DESC``.  Recording the implied
    value would make a plain index differ from a model that says nothing about
    sorting, and indexes are compared by full content - so every such index
    would be dropped and recreated on every run.
    """
    descending = is_asc is False
    parts: list[str] = []
    if descending:
        parts.append("DESC")
    if nulls_first is False and descending:
        parts.append("NULLS LAST")
    elif nulls_first is True and not descending:
        parts.append("NULLS FIRST")
    return " ".join(parts) or None
