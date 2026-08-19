from __future__ import annotations

import re
from typing import Any

from dbwarden.engine.shared.format_utils import _sanitize_identifier
from dbwarden.engine.snapshot.ch_utils import _pick_clickhouse_codec
from dbwarden.engine.backends.clickhouse.render import _parse_ch_type_wrappers


def _strip_codec_wrapper(codec_expr: str) -> str:
    m = re.match(r"^CODEC\((.+)\)$", codec_expr.strip(), re.IGNORECASE)
    return m.group(1) if m else codec_expr.strip()


def _render_ch_meta(columns: list[dict], options: dict, object_type: str) -> list[str]:
    lines: list[str] = []

    engine_raw = options.get("ch_engine_raw")
    if engine_raw is not None:
        if hasattr(engine_raw, "name"):
            eng_name = repr(getattr(engine_raw, "name"))
            eng_args = getattr(engine_raw, "args", None)
            zk = getattr(engine_raw, "zookeeper_path", None)
            replica = getattr(engine_raw, "replica_name", None)
            settings = getattr(engine_raw, "settings", None)
            parts = [f"        ch_engine = ChEngineSpec({eng_name}"]
            if eng_args:
                parts.append(f"args=({', '.join(repr(a) for a in eng_args)},)")
            if zk is not None:
                parts.append(f"zookeeper_path={zk!r}")
            if replica is not None:
                parts.append(f"replica_name={replica!r}")
            if settings is not None:
                parts.append(f"settings={settings!r}")
            lines.append(", ".join(parts) + ")")
        elif isinstance(engine_raw, dict) and engine_raw.get("name"):
            eng_name = repr(engine_raw["name"])
            eng_args = engine_raw.get("args")
            zk = engine_raw.get("zookeeper_path")
            replica = engine_raw.get("replica_name")
            settings = engine_raw.get("settings")
            parts = [f"        ch_engine = ChEngineSpec({eng_name}"]
            if eng_args:
                parts.append(f"args=({', '.join(repr(a) for a in eng_args)},)")
            if zk is not None:
                parts.append(f"zookeeper_path={zk!r}")
            if replica is not None:
                parts.append(f"replica_name={replica!r}")
            if settings is not None:
                parts.append(f"settings={settings!r}")
            lines.append(", ".join(parts) + ")")
        else:
            lines.append(f"        ch_engine = {engine_raw!r}")
    elif options.get("ch_engine"):
        lines.append(f"        ch_engine = {options['ch_engine']!r}")

    for key in (
        "ch_order_by",
        "ch_primary_key",
        "ch_partition_by",
        "ch_sample_by",
        "ch_ttl",
        "ch_settings",
        "ch_object_type",
        "ch_select_statement",
        "ch_to_table",
        "ch_dictionary",
        "ch_dict_layout",
        "ch_dict_source",
        "ch_dict_lifetime",
        "ch_dict_primary_key",
        "ch_zookeeper_path",
        "ch_replica_name",
    ):
        value = options.get(key)
        if value is None:
            continue
        if key == "ch_dictionary" and value is True:
            lines.append("        ch_dictionary = True")
        else:
            lines.append(f"        {key} = {value!r}")

    projections = options.get("ch_projections") or []
    if projections:
        lines.append("        ch_projections = [")
        for projection in projections:
            if isinstance(projection, dict):
                lines.append(
                    f"            ProjectionSpec({projection.get('name')!r}, {projection.get('query', '')!r}),"
                )
            else:
                lines.append(f"            ProjectionSpec({getattr(projection, 'name', '')!r}, {getattr(projection, 'query', '')!r}),")
        lines.append("        ]")

    _CH_FLAT_TO_SPEC: dict[str, str] = {
        "ch_codec": "codec",
        "ch_default_expression": "default_expression",
        "ch_materialized": "materialized",
        "ch_alias": "alias",
        "ch_ephemeral": "ephemeral",
        "ch_ttl": "ttl",
        "ch_low_cardinality": "low_cardinality",
        "ch_nullable": "nullable",
        "ch_type": "type",
    }

    for col in columns:
        ch_meta = col.get("ch_meta") or {}
        if not ch_meta and not col.get("comment"):
            continue
        lines.append("")
        lines.append(f"        class {_sanitize_identifier(col['name'])}(CHColumnMeta):")
        has_content = False
        if col.get("comment"):
            lines.append(f"            comment = {col['comment']!r}")
            has_content = True
        ch_kwargs = {}
        for flat_key, spec_key in _CH_FLAT_TO_SPEC.items():
            if flat_key not in ch_meta:
                continue
            val = ch_meta[flat_key]
            if flat_key == "ch_type" and not (
                isinstance(val, str)
                and (
                    val.startswith("UInt")
                    or val.startswith("AggregateFunction(")
                    or val.startswith("SimpleAggregateFunction(")
                )
            ):
                continue
            if spec_key in ("low_cardinality", "nullable"):
                if val:
                    ch_kwargs[spec_key] = val
            elif val is not None:
                ch_kwargs[spec_key] = val
        if ch_kwargs:
            kwargs_repr = ", ".join(f"{k}={v!r}" for k, v in ch_kwargs.items())
            lines.append(f"            ch = ch.field({kwargs_repr})")
            has_content = True
        if not has_content:
            lines.append("            pass")

    return lines


def _clean_engine_full(engine_full: str) -> str:
    engine_full = engine_full.strip()
    name_end = 0
    for ch in engine_full:
        if ch.isalnum() or ch == '_':
            name_end += 1
        else:
            break
    if name_end == 0:
        return engine_full
    rest = engine_full[name_end:]
    if rest.startswith("("):
        depth = 1
        for i, ch in enumerate(rest[1:], start=1):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = name_end + i + 1
                    return engine_full[:end]
        return engine_full
    return engine_full[:name_end]


def _extract_ch_meta(connection, table_name: str) -> dict:
    from sqlalchemy import text

    tables_result = connection.execute(
        text(
            "SELECT engine, engine_full, sorting_key, primary_key, partition_key, "
            "sampling_key, create_table_query "
            "FROM system.tables WHERE database = currentDatabase() AND name = :name"
        ),
        parameters={"name": table_name},
    )
    row = tables_result.fetchone()
    if not row:
        return {}

    from dbwarden.engine.backends.clickhouse.parse import (
        _clean_expression,
        parse_mv_query,
        parse_projection_queries,
        parse_replica_name,
        parse_ttl_expressions,
        parse_tuple_or_list,
        parse_zookeeper_path,
    )
    from dbwarden.databases.clickhouse.engine import ChEngineSpec

    options: dict = {}
    engine = getattr(row, "engine", "") or ""
    engine_full = getattr(row, "engine_full", "") or ""
    create_query = getattr(row, "create_table_query", "") or ""

    if engine_full:
        options["ch_engine_raw"] = ChEngineSpec.from_engine_string(
            _clean_engine_full(engine_full)
        )
    elif engine:
        options["ch_engine_raw"] = ChEngineSpec.from_engine_string(engine)
    options["ch_engine"] = engine

    sorting_key = parse_tuple_or_list(getattr(row, "sorting_key", None))
    if sorting_key:
        options["ch_order_by"] = sorting_key if isinstance(sorting_key, list) else [sorting_key]
    primary_key = parse_tuple_or_list(getattr(row, "primary_key", None))
    if primary_key:
        options["ch_primary_key"] = primary_key if isinstance(primary_key, list) else [primary_key]
    partition_key = _clean_expression(getattr(row, "partition_key", None))
    if partition_key:
        options["ch_partition_by"] = partition_key
    sampling_key = _clean_expression(getattr(row, "sampling_key", None))
    if sampling_key:
        options["ch_sample_by"] = sampling_key

    ttl = parse_ttl_expressions(create_query)
    if ttl:
        options["ch_ttl"] = ttl
    projections = parse_projection_queries(create_query)
    if projections:
        options["ch_projections"] = projections
    mv_query = parse_mv_query(create_query)
    if mv_query:
        options["ch_select_statement"] = mv_query
        options["ch_object_type"] = "materialized_view"
    zk_path = parse_zookeeper_path(create_query, engine)
    if zk_path:
        options["ch_zookeeper_path"] = zk_path
    replica = parse_replica_name(create_query, engine)
    if replica:
        options["ch_replica_name"] = replica

    if engine.upper() == "DICTIONARY":
        options["ch_dictionary"] = True
        options["ch_object_type"] = "dictionary"

    if create_query.strip().upper().startswith("CREATE MATERIALIZED VIEW"):
        options["ch_object_type"] = "materialized_view"

    from dbwarden.engine.backends.clickhouse.parse import (
        parse_dict_layout,
        parse_dict_lifetime,
        parse_dict_primary_key,
        parse_dict_source,
        parse_mv_to_table,
        parse_settings,
    )

    settings = parse_settings(create_query)
    if settings:
        options["ch_settings"] = settings

    if engine.upper() == "DICTIONARY":
        layout = parse_dict_layout(create_query)
        if layout:
            options["ch_dict_layout"] = layout
        source = parse_dict_source(create_query)
        if source:
            options["ch_dict_source"] = source
        lifetime = parse_dict_lifetime(create_query)
        if lifetime:
            options["ch_dict_lifetime"] = lifetime
        dict_pk = parse_dict_primary_key(create_query)
        if dict_pk:
            options["ch_dict_primary_key"] = dict_pk

    if options.get("ch_object_type") == "materialized_view":
        to_table = parse_mv_to_table(create_query)
        if to_table:
            options["ch_to_table"] = to_table

    columns_result = connection.execute(
        text(
            "SELECT name, type, default_kind, default_expression, compression_codec, "
            "comment, is_in_primary_key, is_in_sorting_key "
            "FROM system.columns "
            "WHERE database = currentDatabase() AND table = :tname "
            "ORDER BY position ASC"
        ),
        parameters={"tname": table_name},
    )
    ch_columns: list[dict] = []
    for c in columns_result.fetchall():
        cname = getattr(c, "name", "")
        raw_type = getattr(c, "type", "") or ""
        default_kind = getattr(c, "default_kind", None) or None
        default_expr = getattr(c, "default_expression", None) or None
        codec_expr = getattr(c, "compression_codec", None) or None
        col_comment = getattr(c, "comment", None) or None

        _, ch_nullable, ch_low_cardinality = _parse_ch_type_wrappers(str(raw_type))

        ch_materialized = None
        ch_alias = None
        if default_kind == "MATERIALIZED":
            ch_materialized = default_expr
        elif default_kind == "ALIAS":
            ch_alias = default_expr

        codec = _pick_clickhouse_codec(codec_expr)

        ch_col: dict = {
            "name": cname,
            "ch_meta": {
                "ch_codec": codec,
                "ch_default_expression": default_expr if default_kind == "DEFAULT" else None,
                "ch_materialized": ch_materialized,
                "ch_alias": ch_alias,
                "ch_low_cardinality": ch_low_cardinality,
                "ch_nullable": ch_nullable,
                "ch_type": raw_type.strip(),
            },
        }
        if col_comment:
            ch_col["comment"] = col_comment
        ch_columns.append(ch_col)

    if ch_columns:
        options["columns"] = ch_columns

    indices_result = connection.execute(
        text(
            "SELECT name, type, expr, granularity "
            "FROM system.data_skipping_indices "
            "WHERE database = currentDatabase() AND table = :tname"
        ),
        parameters={"tname": table_name},
    )
    skip_indexes: list[dict] = []
    for idx in indices_result.fetchall():
        skip_indexes.append({
            "name": getattr(idx, "name", ""),
            "columns": [getattr(idx, "expr", "")],
            "clickhouse_type": getattr(idx, "type", ""),
            "clickhouse_granularity": getattr(idx, "granularity", 1),
        })
    if skip_indexes:
        options["indexes"] = skip_indexes

    return options
