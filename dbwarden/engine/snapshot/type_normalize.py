from __future__ import annotations

import functools
import re
from typing import Any


TYPE_NORMALIZATION_MAP: dict[str, str | None] = {
    "int": "integer",
    "integer": "integer",
    "int4": "integer",
    "tinyint": "integer",
    "smallint": "integer",
    "int2": "integer",
    "bigint": "biginteger",
    "int8": "biginteger",
    "serial": "integer",
    "bigserial": "biginteger",
    "smallserial": "integer",
    "char": "char",
    "nchar": "char",
    "varchar": "varchar",
    "character varying": "varchar",
    "character": "char",
    "text": "text",
    "longtext": "text",
    "clob": "text",
    "mediumtext": "text",
    "boolean": "boolean",
    "bool": "boolean",
    "bit": "boolean",
    "timestamp": "timestamp",
    "timestamptz": "timestamptz",
    "timestamp with time zone": "timestamptz",
    "datetime": "timestamp",
    "date": "date",
    "time": "time",
    "float": "float",
    "float32": "float32",
    "real": "float32",
    "double precision": "float",
    "double": "float",
    "numeric": "numeric",
    "decimal": "numeric",
    "json": "json",
    "jsonb": "json",
    "uuid": "uuid",
    "bytea": "bytes",
    "blob": "bytes",
    "binary": "bytes",
    "varbinary": "bytes",
    "longblob": "bytes",
    "enum": "enum",
    "geometry": "geometry",
    "tsvector": "tsvector",
    "tstzrange": "tstzrange",
    "tsrange": "tsrange",
    "daterange": "daterange",
    "int4range": "int4range",
    "int8range": "int8range",
    "numrange": "numrange",
    # ClickHouse native type mappings (non-conflicting with PG names)
    "float64": "float",
    "string": "varchar",
    "fixedstring": "varchar",
    "datetime64": "timestamp",
    "date32": "date",
    "ipv4": "varchar",
    "ipv6": "varchar",
    "map": "map",
    "enum8": "enum",
    "enum16": "enum",
    "tuple": "tuple",
}

_RAW_TYPE_PATTERN = re.compile(r"^([a-zA-Z][a-zA-Z0-9_]*)(?:\((\d+)(?:\s*,\s*(\d+))?\))?\s*$")

_SNAP_TO_MODEL_KEY = {
    "collation": "pg_collation",
    "storage": "pg_storage",
    "compression": "pg_compression",
    "generated": "pg_generated",
    "identity": "pg_identity",
    "identity_start": "pg_identity_start",
    "identity_increment": "pg_identity_increment",
    "identity_min": "pg_identity_min",
    "identity_max": "pg_identity_max",
}


def snap_to_model_key(snap_key: str) -> str:
    return _SNAP_TO_MODEL_KEY.get(snap_key, snap_key)


def _strip_ch_type_wrappers(raw_type: str) -> str:
    result = raw_type.strip()
    while result.startswith(("Nullable(", "LowCardinality(")) and result.endswith(")"):
        if result.startswith("Nullable("):
            result = result[9:-1].strip()
        else:
            result = result[15:-1].strip()
    return result


def _model_type_str(sa_type) -> str:
    if hasattr(sa_type, "enums") and sa_type.enums:
        return f"Enum({', '.join(repr(v) for v in sa_type.enums)})"
    return str(sa_type)


@functools.lru_cache(maxsize=512)
def normalize_type(raw_type: str) -> dict[str, Any]:
    raw_type = re.sub(r'\s+COLLATE\s+"[^"]*"', "", raw_type, flags=re.IGNORECASE)
    raw_type = re.sub(r"\s+COLLATE\s+'[^']*'", "", raw_type, flags=re.IGNORECASE)
    raw_clean = raw_type.strip().lower()
    raw_no_parens = re.sub(r"\(.*?\)", "", raw_clean).strip()
    raw_no_parens_clean = re.sub(r"\s+", " ", raw_no_parens).strip()
    normalized = TYPE_NORMALIZATION_MAP.get(raw_no_parens_clean)
    if normalized is None:
        base = re.sub(r"[^a-z0-9]", "", raw_no_parens_clean) if raw_no_parens_clean else ""
        normalized = TYPE_NORMALIZATION_MAP.get(base)
    if normalized is None:
        base = re.sub(r"[^a-z]", "", raw_no_parens_clean) if raw_no_parens_clean else ""
        normalized = TYPE_NORMALIZATION_MAP.get(base)
    if normalized is not None:
        result: dict[str, Any] = {"type": normalized}
        if normalized == "timestamptz":
            result["type"] = "timestamp"
            result["has_timezone"] = True
        if normalized == "float32":
            result["type"] = "float"
        full_lower = raw_type.strip().lower()
        length_match = re.search(r"\((\d+)\)", full_lower)
        if length_match and normalized in ("varchar",):
            result["length"] = int(length_match.group(1))
        precision_scale = re.search(r"\((\d+),\s*(\d+)\)", full_lower)
        if precision_scale and normalized in ("numeric",):
            result["precision"] = int(precision_scale.group(1))
            result["scale"] = int(precision_scale.group(2))
        return result
    fallback_match = _RAW_TYPE_PATTERN.match(raw_type.strip())
    if fallback_match:
        return {"type": raw_type.strip(), "raw": True}
    return {"type": raw_type.strip(), "raw": True}


def _normalize_default(d: Any) -> str | None:
    if d is None:
        return None
    s = str(d).strip()
    while len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1]
    s = s.replace("\\'", "'").replace('\\"', '"')
    if s.upper() in ("TRUE", "FALSE"):
        s = s.upper()
    if s.upper() in ("NOW()", "CURRENT_TIMESTAMP()", "CURRENT_TIMESTAMP"):
        s = "CURRENT_TIMESTAMP"
    return s


def _normalize_index_col(col: str) -> str:
    import re
    col = re.sub(r'::\w+(\.\w+)*(\[\])?', '', col)
    col = ' '.join(col.split())
    return col
