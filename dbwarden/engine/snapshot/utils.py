from __future__ import annotations

import re
from typing import Any


def _get_backend(db_name: str | None = None) -> str:
    from dbwarden.engine.model_discovery.type_mapping import _get_backend_name
    return _get_backend_name(db_name)


def _quote_default_for_sql(default: str) -> str:
    stripped = default.strip()
    if not stripped:
        return "NULL"
    if re.match(r"^-?\d+(\.\d+)?$", stripped):
        return stripped
    if re.match(r"^[A-Z_][A-Z0-9_]*$", stripped):
        return stripped
    if re.match(r"^\w+\(.*\)$", stripped):
        return stripped
    escaped = stripped.replace("'", "''")
    return f"'{escaped}'"


def _missing_def_placeholder(backend: str) -> str:
    if backend == "mysql":
        return "/* REQUIRES MANUAL COLUMN DEFINITION - type info unavailable */"
    return "<original_def_unavailable>"
