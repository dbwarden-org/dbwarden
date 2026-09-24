__all__ = [
    "Safety",
    "analyze_clickhouse_options",
    "classify_ch_column_change",
    "classify_ch_options_change",
    "classify_ch_safety",
    "extract_balanced_parens",
    "parse_dict_layout",
    "parse_dict_lifetime",
    "parse_dict_primary_key",
    "parse_dict_source",
    "parse_mv_query",
    "parse_mv_to_table",
    "parse_projection_names",
    "parse_projection_queries",
    "parse_replica_name",
    "parse_settings",
    "parse_ttl_expressions",
    "parse_tuple_or_list",
    "parse_zookeeper_path",
]
from .parse import (
    extract_balanced_parens,
    parse_dict_layout,
    parse_dict_lifetime,
    parse_dict_primary_key,
    parse_dict_source,
    parse_mv_query,
    parse_mv_to_table,
    parse_projection_names,
    parse_projection_queries,
    parse_replica_name,
    parse_settings,
    parse_ttl_expressions,
    parse_tuple_or_list,
    parse_zookeeper_path,
)


def __getattr__(name):
    if name in {"Safety", "classify_ch_column_change", "classify_ch_options_change",
                "analyze_clickhouse_options", "classify_ch_safety"}:
        from . import safety
        return getattr(safety, name)
    raise AttributeError(name)
