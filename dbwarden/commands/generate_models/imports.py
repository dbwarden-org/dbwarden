from __future__ import annotations

from typing import Any

from dbwarden.engine.backends.mysql.generate_models import (
    resolve_mysql_imports as _resolve_mysql_imports,
)
from dbwarden.engine.backends.postgresql.generate_models import (
    _format_pg_type,
    _resolve_postgresql_imports,
)
from dbwarden.engine.core.type_parsing import _parse_type


def _resolve_imports(columns: list[dict], has_relationships: bool) -> set[str]:
    imports: set[str] = {"Column"}
    for col in columns:
        sa_type = _format_pg_type(col) or _parse_type(col["type"], col.get("dialect"))
        base_type = sa_type.split("(")[0].strip().upper()
        if base_type in ("STRING", "TEXT"):
            imports.add("String")
            imports.add("Text")
        elif base_type == "CHAR":
            imports.add("CHAR")
        elif base_type == "INTEGER":
            imports.add("Integer")
        elif base_type == "BIGINTEGER":
            imports.add("BigInteger")
        elif base_type == "SMALLINTEGER":
            imports.add("SmallInteger")
        elif base_type == "BOOLEAN":
            imports.add("Boolean")
        elif base_type == "FLOAT":
            imports.add("Float")
        elif base_type == "NUMERIC":
            imports.add("Numeric")
        elif base_type == "DATETIME":
            imports.add("DateTime")
        elif base_type == "DATE":
            imports.add("Date")
        elif base_type == "TIME":
            imports.add("Time")
        elif base_type == "LARGEBINARY":
            imports.add("LargeBinary")
        elif base_type == "JSON":
            imports.add("JSON")
        elif base_type == "ENUM":
            imports.add("Enum")
        elif base_type == "ARRAY":
            imports.add("ARRAY")
            inner = sa_type[6:-1]
            if inner.startswith("Text"):
                imports.add("Text")
            elif inner.startswith("String"):
                imports.add("String")
            elif inner.startswith("Integer"):
                imports.add("Integer")
        if col.get("default"):
            raw_default = str(col["default"]).strip()
            raw_default_upper = raw_default.upper()
            if raw_default.lower() == "func.now()" or raw_default_upper == "CURRENT_TIMESTAMP" or raw_default_upper == "CURRENT_DATE" or raw_default_upper == "CURRENT_TIME":
                imports.add("func")
        if col.get("server_default"):
            imports.add("text")
        if col.get("foreign_key"):
            imports.add("ForeignKey")
    return imports


def _render_imports(imports: set[str]) -> set[str]:
    result: set[str] = set()
    for imp in sorted(imports):
        if imp == "Column":
            continue
        result.add(imp)
    result.add("Column")
    return result
