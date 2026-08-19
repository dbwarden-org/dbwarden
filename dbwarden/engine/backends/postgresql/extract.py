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
