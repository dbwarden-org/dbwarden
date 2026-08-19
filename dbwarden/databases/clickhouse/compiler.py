from __future__ import annotations

import re
from typing import Any

from sqlalchemy import ColumnElement


def render_expr(expr: ColumnElement | str | Any) -> str:
    """Render a SQLAlchemy expression or raw fragment to ClickHouse SQL.

    Accepts:
        ColumnElement, compiled via the default SA dialect.
            ``func.toYYYYMM(Event.event_date)`` → ``"toYYYYMM(event_date)"``
            Simple column references emit the bare column name (no table prefix).
        Label         , emits the compiled expression; the ``AS alias`` is
            added by the caller (select-item context).
        str           , emitted verbatim (legacy equivalent to an unwrapped
            :class:`ChRaw`, prefer ``ch_raw()`` for explicitness).

    Returns:
        A ClickHouse SQL string suitable for use in DDL/DML.

    NOTE, canonicalization risk:
        ``func.toYYYYMM(x)`` compiles to ``"toYYYYMM(x)"``, but the server reports
        ``"toYYYYMM(event_date)"`` in ``system.tables.partition_key``.  A mismatch
        here produces perpetual drift.  Every expression site needs an audit case
        (see spec Part 7).
    """
    from sqlalchemy.sql.elements import ColumnClause, Label
    from sqlalchemy.sql.functions import Function

    if isinstance(expr, str):
        return expr

    from dbwarden.databases.clickhouse.raw import ChRaw

    if isinstance(expr, ChRaw):
        return expr.sql

    if isinstance(expr, ColumnClause) and not isinstance(expr, (Label, Function)):
        return expr.name

    compiled = expr.compile()
    return compiled.string


def render_expr_list(
    items: list[ColumnElement | str | Any] | None,
) -> list[str]:
    """Render a list of expressions to ClickHouse SQL strings."""
    if items is None:
        return []
    return [render_expr(item) for item in items]


def column_name_from_expr(expr: ColumnElement | str | Any) -> str:
    """Derive the output column name from an expression.

    For a Label, uses the label name.
    For a ColumnClause, uses the column name.
    For a raw string / ChRaw, extracts the alias or last identifier.
    """
    from sqlalchemy.sql.elements import Label

    if isinstance(expr, Label):
        return expr.name

    from sqlalchemy.sql.elements import ColumnClause

    if isinstance(expr, ColumnClause):
        return expr.name

    from dbwarden.databases.clickhouse.raw import ChRaw

    raw: str
    if isinstance(expr, ChRaw):
        raw = expr.sql
    else:
        raw = str(expr)
    return _bare_name(raw)


def _bare_name(expr: str) -> str:
    """Extract the last identifier from an expression.

    ``"toDate(event_time) AS day"`` → ``"day"``
    ``"user_id"`` → ``"user_id"``
    """
    alias_parts = re.split(r"\s+AS\s+", expr, flags=re.IGNORECASE)
    if len(alias_parts) > 1:
        return alias_parts[-1].strip()
    return expr.strip()
