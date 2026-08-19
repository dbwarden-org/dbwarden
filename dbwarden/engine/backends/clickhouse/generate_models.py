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


def _render_ch_mv_meta(columns: list[dict], options: dict) -> list[str]:
    """Render a CHViewMeta block that reconstructs a materialized_view(...) spec."""
    lines: list[str] = []
    kwargs: list[str] = []

    select = options.get("ch_select_statement")
    if select:
        kwargs.append(f"select={select!r}")

    to_table = options.get("ch_to_table")
    if to_table:
        kwargs.append(f"to={to_table!r}")

    refresh = options.get("ch_refresh")
    if refresh:
        kwargs.append(f"refresh={refresh!r}")

    populate = options.get("ch_populate")
    if populate:
        kwargs.append(f"populate={populate!r}")

    if not to_table:
        engine_raw = options.get("ch_engine_raw")
        if engine_raw is not None:
            kwargs.append(f"engine={_render_ch_engine_spec(engine_raw)}")
        elif options.get("ch_engine"):
            kwargs.append(f"engine=ChEngineSpec({options['ch_engine']!r})")

        order_by = options.get("ch_order_by")
        if order_by:
            kwargs.append(f"order_by={order_by!r}")

        partition_by = options.get("ch_partition_by")
        if partition_by:
            kwargs.append(f"partition_by={partition_by!r}")

        ttl = options.get("ch_ttl")
        if ttl:
            kwargs.append(f"ttl={ttl!r}")

    settings = options.get("ch_settings")
    if settings:
        kwargs.append(f"settings={settings!r}")

    if kwargs:
        lines.append("    class Meta(CHViewMeta):")
        lines.append("        ch = materialized_view(")
        for kw in kwargs:
            lines.append(f"            {kw},")
        lines.append("        )")
    else:
        lines.append("    class Meta(CHViewMeta):")
        lines.append("        pass")

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


def _render_ch_engine_spec(engine_raw: Any) -> str:
    """Return a generated ChEngineSpec(...) constructor call as a string."""
    if hasattr(engine_raw, "name"):
        name = getattr(engine_raw, "name")
        args = getattr(engine_raw, "args", None)
        zk = getattr(engine_raw, "zookeeper_path", None)
        replica = getattr(engine_raw, "replica_name", None)
        settings = getattr(engine_raw, "settings", None)
    elif isinstance(engine_raw, dict):
        name = engine_raw.get("name")
        args = engine_raw.get("args")
        zk = engine_raw.get("zookeeper_path")
        replica = engine_raw.get("replica_name")
        settings = engine_raw.get("settings")
    else:
        return f"ChEngineSpec({engine_raw!r})"

    parts = [f"ChEngineSpec({name!r}"]
    if args:
        parts.append(f"args=({', '.join(repr(a) for a in args)},)")
    if zk is not None:
        parts.append(f"zookeeper_path={zk!r}")
    if replica is not None:
        parts.append(f"replica_name={replica!r}")
    if settings is not None:
        parts.append(f"settings={settings!r}")
    return ", ".join(parts) + ")"


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

    # Use the actual ClickHouse database name for qualifier stripping; models
    # are normally authored without the current-db qualifier.
    try:
        actual_db = connection.execute(text("SELECT currentDatabase()")).scalar()
        if not isinstance(actual_db, str):
            actual_db = ""
    except Exception:
        actual_db = ""

    tables_result = connection.execute(
        text(
            "SELECT engine, engine_full, sorting_key, primary_key, partition_key, "
            "sampling_key, create_table_query, uuid "
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
    from dbwarden.engine.snapshot.extract_ch import _strip_clickhouse_db_qualifier

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
        options["ch_select_statement"] = _strip_clickhouse_db_qualifier(mv_query, actual_db)
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

    is_mode_b_mv = False
    if options.get("ch_object_type") == "materialized_view":
        to_table = parse_mv_to_table(create_query)
        if to_table:
            options["ch_to_table"] = _strip_clickhouse_db_qualifier(to_table, actual_db)
            is_mode_b_mv = True

    # Mode B MVs write into an existing target table; the class API forbids
    # declaring columns on the MV itself, so skip column extraction for them.
    if is_mode_b_mv:
        return options

    # Mode A materialized view with inner storage: system.tables reports the
    # engine as "MaterializedView" and leaves engine_full empty.  The actual
    # MergeTree-family table is stored as `.inner_id.<mv_uuid>`; read its
    # engine, keys, and settings so reverse-engineered models can reconstruct
    # the target table spec.
    if engine.upper() == "MATERIALIZEDVIEW":
        mv_uuid = getattr(row, "uuid", None)
        if mv_uuid:
            inner_result = connection.execute(
                text(
                    "SELECT engine, engine_full, sorting_key, primary_key, partition_key, "
                    "sampling_key, create_table_query "
                    "FROM system.tables WHERE database = currentDatabase() AND name = :inner_name"
                ),
                parameters={"inner_name": f".inner_id.{mv_uuid}"},
            )
            inner = inner_result.fetchone()
            if inner:
                inner_engine = getattr(inner, "engine", "") or ""
                inner_engine_full = getattr(inner, "engine_full", "") or ""
                if inner_engine_full:
                    options["ch_engine_raw"] = ChEngineSpec.from_engine_string(
                        _clean_engine_full(inner_engine_full)
                    )
                elif inner_engine:
                    options["ch_engine_raw"] = ChEngineSpec.from_engine_string(inner_engine)
                options["ch_engine"] = inner_engine
                inner_sorting = parse_tuple_or_list(getattr(inner, "sorting_key", None))
                if inner_sorting:
                    options["ch_order_by"] = inner_sorting if isinstance(inner_sorting, list) else [inner_sorting]
                inner_pk = parse_tuple_or_list(getattr(inner, "primary_key", None))
                if inner_pk:
                    options["ch_primary_key"] = inner_pk if isinstance(inner_pk, list) else [inner_pk]
                inner_partition = _clean_expression(getattr(inner, "partition_key", None))
                if inner_partition:
                    options["ch_partition_by"] = inner_partition
                inner_sampling = _clean_expression(getattr(inner, "sampling_key", None))
                if inner_sampling:
                    options["ch_sample_by"] = inner_sampling
                inner_create = getattr(inner, "create_table_query", "") or ""
                inner_ttl = parse_ttl_expressions(inner_create)
                if inner_ttl:
                    options["ch_ttl"] = inner_ttl
                inner_settings = parse_settings(inner_create)
                if inner_settings:
                    options["ch_settings"] = inner_settings

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
