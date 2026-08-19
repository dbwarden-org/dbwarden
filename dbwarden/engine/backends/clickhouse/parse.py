from __future__ import annotations

import re
from typing import Any


def extract_balanced_parens(match: re.Match) -> str | None:
    start = match.end() - 1
    depth = 0
    for i in range(start, len(match.string)):
        ch = match.string[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return match.string[start + 1 : i]
    return None


def parse_ttl_expressions(create_query: str) -> list[str]:
    ttl_match = re.search(
        r"\bTTL\s+(.+?)(?:\s+SETTINGS\b|\s+COMMENT\b|\s+AS\b|$)",
        create_query,
        re.IGNORECASE | re.DOTALL,
    )
    if not ttl_match:
        return []
    ttl_body = ttl_match.group(1).strip()
    return _split_top_level_commas(ttl_body)


def parse_projection_queries(create_query: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    pattern = re.compile(r"PROJECTION\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", re.IGNORECASE)
    pos = 0
    while True:
        match = pattern.search(create_query, pos)
        if not match:
            break
        name = match.group(1)
        query = extract_balanced_parens(match)
        results.append({"name": name, "query": (query or "").strip()})
        pos = match.end()
    return results


def parse_projection_names(create_query: str) -> list[str]:
    return [p["name"] for p in parse_projection_queries(create_query)]


def parse_mv_query(create_query: str) -> str | None:
    mv_match = re.search(r"\bAS\s+(SELECT\s+.+)$", create_query, re.IGNORECASE | re.DOTALL)
    if not mv_match:
        return None
    return mv_match.group(1).strip()


def parse_mv_to_table(create_query: str) -> str | None:
    match = re.search(r"\bTO\s+([a-zA-Z_][a-zA-Z0-9_\.]*)", create_query, re.IGNORECASE)
    return match.group(1) if match else None


def parse_mv_refresh(create_query: str) -> str | None:
    """Parse REFRESH clause from a materialized view CREATE statement.

    Matches ``REFRESH ... `` up to the next keyword (TO, AS, ENGINE, etc.).
    Returns the full refresh schedule string, e.g. ``"EVERY 1 HOUR"``,
    or ``None`` if no REFRESH clause is present.
    """
    match = re.search(
        r"\bREFRESH\s+(.+?)(?=\s+(?:TO|AS|ENGINE|SETTINGS|ORDER\s+BY|PRIMARY\s+KEY|PARTITION\s+BY|TTL|POPULATE)\b)",
        create_query,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1).strip().strip("'\"")
    # Handle REFRESH at end of statement (no following keywords)
    match = re.search(r"\bREFRESH\s+(.+?)\s*$", create_query, re.IGNORECASE)
    if match:
        return match.group(1).strip().strip("'\"")
    return None


def parse_zookeeper_path(create_query: str, engine: str) -> str | None:
    if "Replicated" not in engine:
        return None
    match = re.search(r"\bReplicated\w+\s*\(([^,]+),", create_query)
    if match:
        return match.group(1).strip().strip("'\"")
    return None


def parse_replica_name(create_query: str, engine: str) -> str | None:
    if "Replicated" not in engine:
        return None
    match = re.search(r"\bReplicated\w+\s*\([^,]+,\s*([^)]+)", create_query)
    if match:
        return match.group(1).strip().strip("'\"")
    return None


def parse_tuple_or_list(value: Any) -> str | list[str] | None:
    value_str = _clean_expression(value)
    if value_str is None:
        return None
    if value_str.startswith("tuple(") and value_str.endswith(")"):
        inner = value_str[6:-1].strip()
        if not inner:
            return []
        return _split_top_level_commas(inner)
    if value_str.startswith("(") and value_str.endswith(")"):
        inner = value_str[1:-1].strip()
        if not inner:
            return []
        return _split_top_level_commas(inner)
    if "," in value_str:
        parts = _split_top_level_commas(value_str)
        if len(parts) > 1:
            return parts
    return value_str


def parse_settings(create_query: str) -> dict[str, str] | None:
    settings_match = re.search(
        r"\bSETTINGS\s+(.+?)(?:\s+(?:COMMENT|AS)\b|$)", create_query, re.IGNORECASE
    )
    if not settings_match:
        return None
    settings: dict[str, str] = {}
    for item in settings_match.group(1).split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        settings[key.strip()] = value.strip().strip("'\"")
    return settings or None


def parse_dict_layout(create_query: str) -> str | None:
    match = re.search(r"\bLAYOUT\s*\(", create_query, re.IGNORECASE)
    return extract_balanced_parens(match) if match else None


def parse_dict_source(create_query: str) -> str | None:
    match = re.search(r"\bSOURCE\s*\(", create_query, re.IGNORECASE)
    return extract_balanced_parens(match) if match else None


def parse_dict_lifetime(create_query: str) -> int | str | None:
    lifetime_match = re.search(r"\bLIFETIME\s*\(", create_query, re.IGNORECASE)
    if not lifetime_match:
        return None
    inner = extract_balanced_parens(lifetime_match)
    if inner is None:
        return None
    inner = inner.strip()
    if inner.isdigit():
        return int(inner)
    return inner


def parse_dict_primary_key(create_query: str) -> str | None:
    match = re.search(
        r"\bPRIMARY\s+KEY\s+(.+?)(?=\bSOURCE\b|\bLAYOUT\b|\bLIFETIME\b)",
        create_query,
        re.IGNORECASE | re.DOTALL,
    )
    return match.group(1).strip() if match else None


def _clean_expression(value: Any) -> str | None:
    if value is None:
        return None
    value_str = str(value).strip()
    if not value_str:
        return None
    return value_str


def _split_top_level_commas(value: str) -> list[str]:
    """Split comma-separated SQL expressions without splitting function arguments."""
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    for index, char in enumerate(value):
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            part = value[start:index].strip()
            if part:
                parts.append(part)
            start = index + 1
    part = value[start:].strip()
    if part:
        parts.append(part)
    return parts
