from __future__ import annotations

import warnings
from typing import Any

from dbwarden.databases.clickhouse import (
    AggregatingViewSpec, ChTableSpec, MaterializedViewSpec,
)
from dbwarden.databases.clickhouse.engine import ChEngineSpec
from dbwarden.databases.clickhouse.projection import ProjectionSpec
from dbwarden.engine.backends.clickhouse.render import _apply_ch_nullable_low_cardinality
from dbwarden.exceptions import DBWardenConfigError
from dbwarden.schema._base import read_meta


def _ch_options_from_meta(model_class: type) -> dict:
    dw_meta = read_meta(model_class)
    if not dw_meta:
        return {}

    raw = dw_meta.backend_table
    if raw is None:
        return {}

    options: dict[str, Any] = {}

    if dw_meta.table_attrs.get("_ch_from_loose"):
        is_view = bool(dw_meta.table_attrs.get("ch_select_statement"))
        if is_view:
            warnings.warn(
                "Loose ch_* attributes are deprecated for materialized views. "
                "Use `ch = materialized_view(...)` in class Meta instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        else:
            warnings.warn(
                "Loose ch_* attributes are deprecated. Use `ch = ch_table(...)` instead.",
                DeprecationWarning,
                stacklevel=2,
            )

    if isinstance(raw, dict) and "ch_agg_target" in raw and "ch_agg_mv" in raw:
        options.update(raw["ch_agg_mv"])
        _validate_ch_options(options)
        return options

    if isinstance(raw, dict):
        for key in (
            "ch_engine", "ch_order_by", "ch_primary_key", "ch_partition_by",
            "ch_sample_by", "ch_ttl", "ch_object_type", "ch_select_statement",
            "ch_to_table", "ch_dictionary", "ch_dict_layout", "ch_dict_source",
            "ch_dict_lifetime", "ch_dict_primary_key", "ch_zookeeper_path",
            "ch_replica_name", "ch_settings",
        ):
            if key in raw:
                options[key] = raw[key]
        ch_projections = raw.get("ch_projections") or []
        options["ch_projections"] = [
            p.to_dict() if isinstance(p, ProjectionSpec) else p
            for p in ch_projections
        ]
        _validate_ch_options(options)
        return options

    if isinstance(raw, MaterializedViewSpec):
        mvd = raw.to_dict()
        options.update(mvd)
        options["ch_object_type"] = "materialized_view"
        if raw.engine is not None:
            options["ch_engine_raw"] = raw.engine
            engine_name = raw.engine.name if hasattr(raw.engine, "name") else str(raw.engine)
            from dbwarden.databases.clickhouse.views import _validate_mv_engine
            from dbwarden.databases.clickhouse.raw import ChRaw
            select_str = mvd.get("ch_select_statement")
            select_is_raw = isinstance(select_str, (str, ChRaw))
            _validate_mv_engine(engine_name, select_is_raw=select_is_raw)
        if raw.settings is not None:
            options["ch_settings"] = dict(raw.settings)
        _validate_ch_options(options)
        return options

    if isinstance(raw, AggregatingViewSpec):
        agg_dict = raw.to_dict()
        options.update(agg_dict["ch_agg_mv"])
        options["ch_object_type"] = "materialized_view"
        _validate_ch_options(options)
        return options

    if not isinstance(raw, ChTableSpec):
        return {}

    if raw.engine:
        options["ch_engine_raw"] = raw.engine
        options["ch_engine"] = _serialize_ch_engine(raw.engine)
    if raw.order_by is not None:
        options["ch_order_by"] = raw.order_by
    if raw.primary_key is not None:
        options["ch_primary_key"] = raw.primary_key
    if raw.partition_by is not None:
        options["ch_partition_by"] = raw.partition_by
    if raw.sample_by is not None:
        options["ch_sample_by"] = raw.sample_by
    if raw.ttl is not None:
        options["ch_ttl"] = raw.ttl
    if raw.settings is not None:
        options["ch_settings"] = raw.settings
    elif isinstance(raw.engine, ChEngineSpec) and raw.engine.settings is not None:
        options["ch_settings"] = raw.engine.settings
    if raw.zookeeper_path is not None:
        options["ch_zookeeper_path"] = raw.zookeeper_path
    if raw.replica_name is not None:
        options["ch_replica_name"] = raw.replica_name
    ch_object_type = dw_meta.table_attrs.get("ch_object_type", "table")
    options["ch_object_type"] = ch_object_type if ch_object_type is not None else "table"
    if raw.select_statement is not None:
        options["ch_select_statement"] = raw.select_statement
    if getattr(raw, "to", None) is not None:
        options["ch_to_table"] = raw.to
    elif getattr(raw, "to_table", None) is not None:
        options["ch_to_table"] = raw.to_table
    if raw.projections is not None:
        options["ch_projections"] = [
            p.to_dict() if isinstance(p, ProjectionSpec) else p
            for p in raw.projections
        ]

    attrs = dw_meta.table_attrs
    if attrs.get("ch_dictionary"):
        options["ch_dictionary"] = True
    for key in ("ch_dict_layout", "ch_dict_source", "ch_dict_lifetime", "ch_dict_primary_key"):
        if key in attrs:
            options[key] = attrs[key]

    # Fallback: if ch_projections was set via loose attrs (not ch_table spec)
    if "ch_projections" not in options:
        projections = attrs.get("ch_projections") or []
        options["ch_projections"] = [
            p.to_dict() if isinstance(p, ProjectionSpec) else p
            for p in projections
        ]

    _validate_ch_options(options)
    return options


def _serialize_ch_engine(engine: Any) -> str | tuple | None:
    if isinstance(engine, ChEngineSpec):
        args = [engine.name]
        if engine.zookeeper_path is not None:
            args.append(engine.zookeeper_path)
        if engine.replica_name is not None:
            args.append(engine.replica_name)
        args.extend(engine.args)
        return tuple(args)
    if isinstance(engine, (str, tuple, list)):
        return engine
    return None


def _is_merge_tree_family(engine_name: str) -> bool:
    return engine_name.endswith("MergeTree")


def _get_engine_name(options: dict) -> str | None:
    engine = options.get("ch_engine")
    if isinstance(engine, str):
        return engine
    if isinstance(engine, (tuple, list)):
        return str(engine[0]) if engine else None
    # Plain string in ch_engine_raw
    raw = options.get("ch_engine_raw")
    if isinstance(raw, str):
        return raw
    return None


def _validate_ch_options(options: dict) -> None:
    order_by = options.get("ch_order_by")
    primary_key = options.get("ch_primary_key")
    projections = options.get("ch_projections")
    settings = options.get("ch_settings")

    if order_by is not None and not isinstance(order_by, (str, list, tuple)):
        raise ValueError("ch_order_by must be a string or list/tuple of strings")
    if isinstance(order_by, (list, tuple)):
        if not order_by:
            raise ValueError("ch_order_by cannot be empty")
        if not all(isinstance(item, str) and item.strip() for item in order_by):
            raise ValueError("ch_order_by entries must be non-empty strings")

    if primary_key is not None and not isinstance(primary_key, (str, list, tuple)):
        raise ValueError("ch_primary_key must be a string or list/tuple of strings")
    if isinstance(primary_key, (list, tuple)):
        if not primary_key:
            raise ValueError("ch_primary_key cannot be empty")
        if not all(isinstance(item, str) and item.strip() for item in primary_key):
            raise ValueError("ch_primary_key entries must be non-empty strings")

    if isinstance(order_by, (list, tuple)) and primary_key:
        normalized = [item.strip() for item in order_by]
        if isinstance(primary_key, str):
            pk_parts = [primary_key]
        else:
            pk_parts = [item.strip() for item in primary_key]
        if normalized[:len(pk_parts)] != pk_parts:
            raise ValueError("ch_primary_key must be a prefix of ch_order_by")

    ttl = options.get("ch_ttl")
    if ttl is not None:
        if not isinstance(ttl, list):
            raise ValueError("ch_ttl must be a list of expressions")
        if not all(isinstance(item, str) and item.strip() for item in ttl):
            raise ValueError("ch_ttl entries must be non-empty strings")

    if projections is not None:
        if not isinstance(projections, list):
            raise ValueError("ch_projections must be a list")
        for proj in projections:
            if isinstance(proj, dict):
                if not proj.get("name"):
                    raise ValueError("ch_projections entries require name")
            elif not hasattr(proj, "name") or not hasattr(proj, "query"):
                raise ValueError("ch_projections entries must be ProjectionSpec or dict with name/query")

    if settings is not None:
        if not isinstance(settings, dict):
            raise ValueError("ch_settings must be a dict")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in settings.items()):
            raise ValueError("ch_settings keys and values must be strings")

    if options.get("ch_dictionary") or any(options.get(k) for k in ("ch_dict_layout", "ch_dict_source", "ch_dict_lifetime")):
        if not options.get("ch_dict_layout"):
            raise ValueError("ch_dict_layout is required for dictionary tables")
        if not options.get("ch_dict_source"):
            raise ValueError("ch_dict_source is required for dictionary tables")
        if not options.get("ch_dict_lifetime"):
            raise ValueError("ch_dict_lifetime is required for dictionary tables")

    zk = options.get("ch_zookeeper_path")
    if zk is not None and not isinstance(zk, str):
        raise ValueError("ch_zookeeper_path must be a string")
    replica = options.get("ch_replica_name")
    if replica is not None and not isinstance(replica, str):
        raise ValueError("ch_replica_name must be a string")


def _detect_ch_object_type(options: dict) -> str:
    explicit = options.get("ch_object_type")
    auto: str = "table"

    has_select = bool(options.get("ch_select_statement"))
    has_dict = bool(options.get("ch_dictionary"))
    has_dict_fields = any(
        options.get(k) for k in ("ch_dict_layout", "ch_dict_source", "ch_dict_lifetime", "ch_dict_primary_key")
    )

    if has_select:
        auto = "materialized_view"
    if has_dict or has_dict_fields:
        if auto == "materialized_view":
            raise DBWardenConfigError(
                "Conflicting ClickHouse object type: both ch_select_statement "
                "and dictionary fields (ch_dict_layout, etc.) are set"
            )
        auto = "dictionary"

    if explicit is not None:
        if explicit != auto and not (explicit == "table" and auto == "table"):
            raise DBWardenConfigError(
                f"Explicit ch_object_type={explicit!r} conflicts with "
                f"detected type {auto!r} based on other ch_* options"
            )
        return explicit

    return auto


def _maybe_float32(sa_type) -> str:
    precision = getattr(sa_type, "precision", None)
    if precision is None:
        return "Float64"
    if precision <= 24:
        return "Float32"
    return "Float64"


def _map_numeric_to_decimal(name: str) -> str:
    import re
    m = re.match(r"NUMERIC\s*\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)", name, re.IGNORECASE)
    if not m:
        m = re.match(r"DECIMAL\s*\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)", name, re.IGNORECASE)
    if m:
        p = m.group(1)
        s = m.group(2) or "0"
        return f"Decimal({p}, {s})"
    return "Decimal(38, 0)"


def _map_datetime(sa_type, name: str) -> str:
    import re
    m = re.match(r"DATETIME(?:64)?\s*\(\s*(\d+)\s*\)", name, re.IGNORECASE)
    if m:
        return f"DateTime64({m.group(1)})"
    timezone = getattr(sa_type, "timezone", None)
    if timezone:
        return "DateTime64(3)"
    return "DateTime"


def _render_ch_type_from_sa(sa_type, raw_type_str: str) -> str:
    item_type = getattr(sa_type, "item_type", None)
    if item_type is not None:
        inner = _render_ch_type_from_sa(item_type, str(item_type).upper().strip())
        return f"Array({inner})"

    enums = getattr(sa_type, "enums", None)
    if enums is not None and enums:
        count = len(enums)
        values = ", ".join(repr(v) for v in enums)
        if count <= 127:
            return f"Enum8({values})"
        return f"Enum16({values})"

    name = raw_type_str
    base = name.split("(")[0] if "(" in name else name

    if base in ("VARCHAR", "CHAR", "CHARACTER VARYING", "TEXT", "CLOB", "STRING"):
        return "String"
    if base in ("INT", "INTEGER", "INT32"):
        return "Int32"
    if base in ("BIGINT", "BIGINTEGER", "INT64"):
        return "Int64"
    if base in ("SMALLINT", "SMALLINTEGER", "INT16", "TINYINT"):
        return "Int16"
    if base in ("FLOAT", "FLOAT32", "REAL"):
        return _maybe_float32(sa_type)
    if base == "DOUBLE_PRECISION":
        return "Float64"
    if base in ("NUMERIC", "DECIMAL", "NUMBER"):
        return _map_numeric_to_decimal(name)
    if base == "BOOLEAN":
        return "Bool"
    if base == "DATE":
        return "Date"
    if base in ("DATETIME", "TIMESTAMP"):
        return _map_datetime(sa_type, name)
    if base in ("BLOB", "BYTEA", "BINARY", "LARGEBINARY"):
        return "String"
    if base == "JSON":
        return "JSON"
    if base in ("UUID",):
        return "UUID"
    if base in ("TIME", "INTERVAL"):
        return "String"

    return name


def _map_sa_type_to_clickhouse(column) -> str:
    raw_type_str = str(column.type).upper().strip()
    ch_type = _render_ch_type_from_sa(column.type, raw_type_str)

    ch_meta_attrs = getattr(column, "info", {})
    return _apply_ch_nullable_low_cardinality(
        ch_type,
        bool(ch_meta_attrs.get("ch_nullable")),
        bool(ch_meta_attrs.get("ch_low_cardinality")),
    )
