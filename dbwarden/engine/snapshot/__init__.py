"""Snapshot module - package split for maintainability."""

# Re-export backend-agnostic core items
from dbwarden.engine.core.dedup import (
    _filter_duplicates_from_snapshot_diff,
    _normalize_sql,
    _sql_into_statements,
)
from dbwarden.engine.core.models import IndexInfo, ModelColumn, ModelTable
from dbwarden.engine.core.rename import (
    RENAME_TABLE_OVERLAP_THRESHOLD,
    TableRenameIntent,
    _apply_rename_intents,
    _compute_table_overlap,
    detect_renames,
)
from dbwarden.engine.core.snapshot_io import (
    compute_checksum,
    extract_snapshot_tables,
    find_latest_snapshot,
    get_schemas_directory,
    read_snapshot,
    write_snapshot,
)
from dbwarden.engine.core.statement_order import (
    MigrationStatement,
    StatementOrder,
    _assemble_migration,
)

# Re-export backend imports
from dbwarden.engine.backends.clickhouse.parse import (
    parse_dict_layout as _parse_dict_layout,
    parse_dict_lifetime as _parse_dict_lifetime,
    parse_dict_primary_key as _parse_dict_primary_key,
    parse_dict_source as _parse_dict_source,
    parse_mv_query as _parse_mv_query,
    parse_mv_to_table as _parse_mv_to_table,
    parse_projection_names as _parse_projection_names,
    parse_projection_queries as _parse_projection_queries,
    parse_replica_name as _parse_replica_name,
    parse_settings as _parse_settings,
    parse_ttl_expressions as _parse_ttl_expressions,
    parse_tuple_or_list as _parse_tuple_or_list,
    parse_zookeeper_path as _parse_zookeeper_path,
)
from dbwarden.engine.backends.mysql.extract import (
    assert_complete_mysql_type as _assert_complete_mysql_type,
    mysql_column_definition_for_meta as _mysql_column_definition_for_meta,
    normalize_mysql_default as _normalize_mysql_default,
    normalize_mysql_table_value as _normalize_mysql_table_value,
)
from dbwarden.engine.backends.postgresql.extract import (
    _is_autoincrement,
    _get_generic_type_name,
    _normalize_view_def,
    _strip_pg_expr_parens,
)
from dbwarden.engine.backends.postgresql.render import _is_expression
from dbwarden.engine.backends.postgresql.sql_build import _build_pg_meta_sql

# Re-export submodule functions
from .ch_utils import (
    _CH_COLUMN_KEYS,
    _check_ch_engine_recreate_allowed,
    _clean_clickhouse_expression,
    _diff_ch_column_extras,
    _pick_clickhouse_codec,
    _serialize_clickhouse_engine,
)
from .diff import diff_models_against_snapshot
from .extract import extract_full_schema_snapshot
from .extract_ch import _extract_clickhouse_schema_snapshot
from .extract_common import (
    build_table_entry,
    introspect_check_constraints,
    introspect_columns,
    introspect_foreign_keys,
    introspect_indexes,
    introspect_unique_constraints,
)
from .extract_mysql import enrich_column_mysql, enrich_table_mysql
from .extract_pg import (
    enrich_column_pg,
    enrich_pg_constraint_extras,
    enrich_table_pg,
    extract_pg_database_objects,
    get_pg_constraint_index_names,
)
from .extract_sqlite import enrich_table_sqlite, fixup_sqlite_fk_actions
from .types import (  # noqa: F401
    Snapshot,
    SnapshotCheckConstraint,
    SnapshotColumn,
    SnapshotConstraint,
    SnapshotDomain,
    SnapshotEventTrigger,
    SnapshotExtendedStats,
    SnapshotForeignKey,
    SnapshotFunction,
    SnapshotGrant,
    SnapshotIndex,
    SnapshotMysqlColumnMeta,
    SnapshotMysqlTableMeta,
    SnapshotPgColumnMeta,
    SnapshotPgTableMeta,
    SnapshotPgType,
    SnapshotPolicy,
    SnapshotRole,
    SnapshotSchemaGrant,
    SnapshotSequence,
    SnapshotSqliteColumnMeta,
    SnapshotSqliteTableMeta,
    SnapshotTable,
    SnapshotTrigger,
    SnapshotUniqueConstraint,
)
from .index_utils import (
    _build_index_name,
    _index_op_from_info,
    _index_sig,
    _rename_table_sql,
)
from .sql_builders import (
    _build_alter_default_sql,
    _build_alter_nullable_sql,
    _build_alter_type_sql,
    _build_ch_projection_sql,
    _build_clickhouse_recreate_table_sql,
    _build_create_table_sequence,
    _build_index_sql,
    _build_safe_type_change_sql,
    _join_creation_sql,
)
from .sql_gen import IRREVERSIBLE_ANNOTATION, RollbackContractError, _find_model_table, snapshot_diff_to_sql
from .type_normalize import (
    _SNAP_TO_MODEL_KEY,
    _model_type_str,
    _normalize_default,
    _normalize_index_col,
    _strip_ch_type_wrappers,
    normalize_type,
    snap_to_model_key,
)
from .utils import _get_backend, _missing_def_placeholder, _quote_default_for_sql

from dbwarden.engine.backends.mysql.sql_build import (
    build_mysql_alter_default_sql as _build_mysql_alter_default_sql,
)

# Re-export TYPE_NORMALIZATION_MAP from type_normalize
from .type_normalize import TYPE_NORMALIZATION_MAP
