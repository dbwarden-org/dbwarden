from __future__ import annotations

from typing import Any


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

    if zookeeper_path is not None:
        extra_args.insert(0, zookeeper_path)
    if replica_name is not None:
        extra_args.insert(1 if zookeeper_path is not None else 0, replica_name)

    if extra_args:
        return f"{engine_name}({', '.join(extra_args)})"
    return f"{engine_name}()"


def _render_clickhouse_table_suffix(table: Any) -> str:
    options = table.clickhouse_options
    if not options:
        return " ENGINE = MergeTree()"

    parts: list[str] = []

    engine_raw = options.get("ch_engine", "MergeTree")
    if isinstance(engine_raw, str):
        engine_name = engine_raw
        engine_args = []
    elif isinstance(engine_raw, tuple):
        engine_name = engine_raw[0] if engine_raw else "MergeTree"
        engine_args = list(str(a) for a in engine_raw[1:])
    elif isinstance(engine_raw, (list, tuple)):
        engine_name = engine_raw[0] if engine_raw else "MergeTree"
        engine_args = list(str(a) for a in engine_raw[1:])
    else:
        engine_name = "MergeTree"
        engine_args = []
    zk_path = options.get("ch_zookeeper_path")
    replica_name = options.get("ch_replica_name")
    if zk_path is not None:
        engine_args.insert(0, zk_path)
    if replica_name is not None:
        engine_args.insert(1 if zk_path is not None else 0, replica_name)
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
    if to_table:
        parts[0] += f" TO {to_table}"
    if columns_sql and not to_table:
        parts[0] += f" (\n{columns_sql}\n)"

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
