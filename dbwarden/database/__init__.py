"""Backward-compatibility shim. Use ``dbwarden.connection`` instead."""
from __future__ import annotations

import warnings
from typing import Any


def __getattr__(name: str) -> Any:
    warnings.warn(
        "dbwarden.database is deprecated; use dbwarden.connection instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    import dbwarden.connection as _conn

    return getattr(_conn, name)


__all__ = [
    "get_db_connection",
    "reset_connection_logging",
    "QueryMethod",
    "get_query",
]
