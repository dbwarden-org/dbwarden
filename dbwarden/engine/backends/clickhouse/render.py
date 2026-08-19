from __future__ import annotations

import re
from typing import Any


def _apply_ch_nullable_low_cardinality(
    base_type: str,
    nullable: bool,
    low_cardinality: bool,
) -> str:
    """Wrap a base ClickHouse type with Nullable/LowCardinality in CH order.

    ClickHouse forbids ``Nullable(LowCardinality(...))``; the valid form is
    ``LowCardinality(Nullable(...))``.  This helper applies ``Nullable`` first
    and ``LowCardinality`` second so the two flags can be combined safely.
    """
    result = base_type
    if nullable:
        result = f"Nullable({result})"
    if low_cardinality:
        result = f"LowCardinality({result})"
    return result


def _make_clickhouse_key_type(type_str: str) -> str:
    """Return a non-nullable version of ``type_str`` while preserving LowCardinality.

    ClickHouse key columns (ORDER BY / PRIMARY KEY / PARTITION BY / SAMPLE BY)
    must not be nullable.  This strips any ``Nullable(...)`` wrappers and keeps
    an outer ``LowCardinality(...)`` if it was present.
    """
    s = type_str.strip()

    # Unwrap an outer Nullable first, then re-evaluate the inner type.
    if s.startswith("Nullable(") and s.endswith(")"):
        inner = s[len("Nullable("):-1].strip()
        return _make_clickhouse_key_type(inner)

    # Preserve LowCardinality, but ensure its inner argument is not nullable.
    if s.startswith("LowCardinality(") and s.endswith(")"):
        inner = s[len("LowCardinality("):-1].strip()
        return f"LowCardinality({_make_clickhouse_key_type(inner)})"

    return s


def _parse_ch_type_wrappers(type_str: str) -> tuple[str, bool, bool]:
    """Return ``(base_type, is_nullable, is_low_cardinality)`` for a CH type.

    Handles nested wrappers such as ``Nullable(LowCardinality(String))``.
    """
    s = type_str.strip()
    nullable = False
    low_cardinality = False
    while s.startswith(("Nullable(", "LowCardinality(")) and s.endswith(")"):
        if s.startswith("Nullable("):
            nullable = True
            s = s[len("Nullable("):-1].strip()
        elif s.startswith("LowCardinality("):
            low_cardinality = True
            s = s[len("LowCardinality("):-1].strip()
    return s, nullable, low_cardinality


def _extract_clickhouse_identifiers(expr: str) -> set[str]:
    """Return the bare identifier-like tokens in a CH expression.

    Used to discover which columns are referenced by expressions such as
    ``toYYYYMM(event_date)`` or ``intHash64(region)``.
    """
    # Drop string literals so identifiers inside quotes are not picked up.
    cleaned = re.sub(r"'[^']*'", "", expr)
    cleaned = re.sub(r'"[^"]*"', "", cleaned)
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", cleaned))


def _ch_key_columns(options: dict[str, Any] | None) -> set[str]:
    """Return the set of column names used in CH key clauses."""
    columns: set[str] = set()
    if not options:
        return columns

    for key in ("ch_order_by", "ch_primary_key"):
        value = options.get(key)
        if isinstance(value, str):
            columns.update(_extract_clickhouse_identifiers(value))
        elif isinstance(value, (list, tuple)):
            for part in value:
                columns.update(_extract_clickhouse_identifiers(str(part)))

    for key in ("ch_partition_by", "ch_sample_by"):
        value = options.get(key)
        if value:
            columns.update(_extract_clickhouse_identifiers(str(value)))

    return columns


def _render_clickhouse_projection(projection: dict | Any) -> str:
    if isinstance(projection, dict):
        return f"PROJECTION {projection['name']} ({projection['query']})"
    return f"PROJECTION {projection.name} ({projection.query})"


def _render_clickhouse_projections(table: Any) -> list[str]:
    projections = table.clickhouse_options.get("ch_projections") or []
    return [_render_clickhouse_projection(projection) for projection in projections]


def _format_clickhouse_expression(value: str | list[str] | tuple[str, ...]) -> str:
    if isinstance(value, str):
        return value
    return "(" + ", ".join(value) + ")"


def _format_clickhouse_engine(
    value: str | tuple | list,
    zookeeper_path: str | None = None,
    replica_name: str | None = None,
) -> str:
    if isinstance(value, str):
        engine_name = value
        extra_args = []
    elif isinstance(value, (tuple, list)) and value:
        engine_name = value[0]
        if not isinstance(engine_name, str) or not engine_name.strip():
            raise ValueError("ch_engine must start with a non-empty engine name")
        extra_args = list(str(item) for item in value[1:])
    else:
        raise ValueError("ch_engine must be a string or tuple/list")

    def same_argument(left: str, right: str) -> bool:
        return left.strip("'\" ") == right.strip("'\" ")

    has_zookeeper_path = (
        zookeeper_path is not None
        and bool(extra_args)
        and same_argument(extra_args[0], zookeeper_path)
    )
    if zookeeper_path is not None and not has_zookeeper_path:
        extra_args.insert(0, zookeeper_path)
    replica_index = 1 if zookeeper_path is not None else 0
    has_replica_name = (
        replica_name is not None
        and len(extra_args) > replica_index
        and same_argument(extra_args[replica_index], replica_name)
    )
    if replica_name is not None and not has_replica_name:
        extra_args.insert(replica_index, replica_name)

    if extra_args:
        return f"{engine_name}({', '.join(extra_args)})"
    return f"{engine_name}()"


def _render_clickhouse_table_suffix(table: Any) -> str:
    options = table.clickhouse_options
    if not options:
        return " ENGINE = MergeTree()"

    parts: list[str] = []

    engine_raw = options.get("ch_engine", "MergeTree")
    parts.append(
        "ENGINE = " + _format_clickhouse_engine(
            engine_raw,
            options.get("ch_zookeeper_path"),
            options.get("ch_replica_name"),
        )
    )

    order_by = options.get("ch_order_by")
    if order_by is not None:
        parts.append(f"ORDER BY {_format_clickhouse_expression(order_by)}")

    primary_key = options.get("ch_primary_key")
    if primary_key:
        parts.append(f"PRIMARY KEY {_format_clickhouse_expression(primary_key)}")

    partition_by = options.get("ch_partition_by")
    if partition_by:
        parts.append(f"PARTITION BY {partition_by}")

    sample_by = options.get("ch_sample_by")
    if sample_by:
        parts.append(f"SAMPLE BY {sample_by}")

    ttl = options.get("ch_ttl")
    if ttl:
        parts.append("TTL " + ", ".join(ttl) if isinstance(ttl, list) else f"TTL {ttl}")

    settings = options.get("ch_settings")
    if settings:
        settings_str = ", ".join(f"{k}={v}" for k, v in settings.items())
        parts.append(f"SETTINGS {settings_str}")

    return "\n" + "\n".join(parts)


def _generate_clickhouse_materialized_view_sql(
    table: Any,
    columns_sql: str,
) -> str:
    options = table.clickhouse_options
    parts = [f"CREATE MATERIALIZED VIEW IF NOT EXISTS {table.name}"]
    to_table = options.get("ch_to_table")
    settings = options.get("ch_settings")
    settings_str = ", ".join(f"{k}={v}" for k, v in settings.items()) if settings else ""
    refresh = options.get("ch_refresh")
    populate = bool(options.get("ch_populate"))

    # Refreshable MV syntax requires REFRESH before the TO/ENGINE clauses.
    if refresh:
        parts.append(f"REFRESH {refresh}")
        if settings_str:
            parts.append(f"SETTINGS {settings_str}")

    if to_table:
        parts.append(f"TO {to_table}")

    if columns_sql and not to_table:
        parts.append(f"(\n{columns_sql}\n)")

    if not to_table:
        engine_raw = options.get("ch_engine", "MergeTree")
        if isinstance(engine_raw, str):
            engine_name = engine_raw
            engine_args = []
        elif isinstance(engine_raw, tuple):
            engine_name = engine_raw[0] if engine_raw else "MergeTree"
            engine_args = list(str(a) for a in engine_raw[1:])
        else:
            engine_name = "MergeTree"
            engine_args = []
        if engine_args:
            parts.append(f"ENGINE = {engine_name}({', '.join(engine_args)})")
        else:
            parts.append(f"ENGINE = {engine_name}()")

        order_by = options.get("ch_order_by")
        if order_by is not None:
            parts.append(f"ORDER BY {_format_clickhouse_expression(order_by)}")

        primary_key = options.get("ch_primary_key")
        if primary_key:
            parts.append(f"PRIMARY KEY {_format_clickhouse_expression(primary_key)}")

        partition_by = options.get("ch_partition_by")
        if partition_by:
            parts.append(f"PARTITION BY {partition_by}")

        sample_by = options.get("ch_sample_by")
        if sample_by:
            parts.append(f"SAMPLE BY {sample_by}")

        ttl = options.get("ch_ttl")
        if ttl:
            parts.append("TTL " + ", ".join(ttl) if isinstance(ttl, list) else f"TTL {ttl}")

        # Engine-level SETTINGS for implicit-storage MVs.
        if settings_str and not refresh:
            parts.append(f"SETTINGS {settings_str}")

    if populate and not refresh:
        parts.append("POPULATE")

    select = options.get("ch_select_statement")
    if select:
        parts.append(f"AS {select}")
    return "\n".join(parts)


def generate_create_dictionary_sql(table: Any) -> str:
    options = table.clickhouse_options
    columns_sql = ",\n".join(
        f"    {col.name} {col.ch_meta.get('ch_type', col.type)}"
        for col in table.columns
    )
    pk = options.get("ch_dict_primary_key")
    if pk is None and table.columns:
        pk = table.columns[0].name
    primary_key_sql = f"PRIMARY KEY {_format_clickhouse_expression(pk)}"
    lifetime = options["ch_dict_lifetime"]
    lifetime_sql = f"LIFETIME({lifetime})" if isinstance(lifetime, str) else f"LIFETIME({lifetime})"
    return (
        f"CREATE DICTIONARY IF NOT EXISTS {table.name} (\n"
        f"{columns_sql}\n"
        f")\n"
        f"{primary_key_sql}\n"
        f"SOURCE({options['ch_dict_source']})\n"
        f"{lifetime_sql}\n"
        f"LAYOUT({options['ch_dict_layout']})"
    )
