from __future__ import annotations

import json
from dataclasses import asdict

from dbwarden.engine.backends.clickhouse.parse import (
    _clean_expression,
    parse_mv_query as _parse_mv_query,
    parse_projection_names as _parse_projection_names,
    parse_projection_queries as _parse_projection_queries,
    parse_replica_name as _parse_replica_name,
    parse_ttl_expressions as _parse_ttl_expressions,
    parse_tuple_or_list as _parse_tuple_or_list,
    parse_zookeeper_path as _parse_zookeeper_path,
)
from dbwarden.engine.backends.clickhouse.safety import (
    CH_COLUMN_CRITICAL,
    CH_COLUMN_WARN,
    CH_OPTION_CRITICAL,
    _CH_OPTION_KEY_MAP,
    CH_OPTION_WARN,
    analyze_clickhouse_options,
    classify_ch_column_change,
    classify_ch_options_change,
    classify_ch_safety,
)
from dbwarden.engine.backends.postgresql.safety import (
    classify_enum_change,
    classify_pg_type_change,
)
from dbwarden.engine.discovery import (
    filter_model_tables_by_name,
    get_all_model_tables,
    validate_model_tables_exist,
)
from dbwarden.engine.safety.analyzer import (
    _analyze_table,
    analyze_schema,
)
from dbwarden.engine.safety.classifiers import (
    Safety,
    _snapshot_column_type_signature,
)
from dbwarden.engine.safety.snapshot import (
    _extract_generic_schema_snapshot,
    extract_schema_snapshot,
)
from dbwarden.models import SafetyIssue


def load_issues(database: str | None = None) -> list[SafetyIssue]:
    from dbwarden.config import get_database

    config = get_database(database)
    model_tables = get_all_model_tables(config.model_paths, db_name=database)
    validate_model_tables_exist(model_tables, config.model_tables, database or "default")
    model_tables = filter_model_tables_by_name(model_tables, config.model_tables)
    schema_snapshot = extract_schema_snapshot(database=database)
    return analyze_schema(model_tables, schema_snapshot)


def issues_to_json(issues: list[SafetyIssue]) -> str:
    return json.dumps([asdict(issue) for issue in issues], indent=2)
