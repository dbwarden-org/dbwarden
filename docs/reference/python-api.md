# Python API inventory

This inventory covers declared package exports, configuration, and runtime handles.
Signatures and declared fields come from the current source. Internal engine exports
are contributor interfaces, not a compatibility promise. Use the
[plugin development guide](../plugins/developing/overview.md) when choosing extension points.

For usage, see [configuration](configuration-api.md), [models](../models.md),
[plugin hooks](../plugins/reference/hook-catalog.md), and the backend guides.

Regenerate this inventory with `python scripts/check-docs.py --api-reference`.

## `dbwarden`

### `database_config`

```text
database_config(*, database_name: 'str', database_type: 'DatabaseType' = 'sqlite', database_url_sync: 'str | None' = None, database_url_async: 'str | None' = None, secure_values: 'bool' = False, skip_if_missing: 'bool' = False, default: 'bool' = False, migrations_dir: 'str | None' = None, migration_table: 'str | None' = None, model_paths: 'list[str] | None' = None, model_tables: 'list[str] | None' = None, data_paths: 'list[str] | None' = None, data_snapshot_dir: 'str' = '.dbwarden/data', snapshot_registry: 'str' = '.dbwarden/snapshots/registry.json', dev_database_type: 'DatabaseType | None' = None, dev_database_url: 'str | None' = None, overlap_models: 'bool' = False, auto_apply_seeds: 'bool' = False, seed_table: 'str | None' = None, pg_schema: 'str | None' = None, pg_migration_lock_timeout: 'int | None' = None, ch_cluster: 'str | None' = None, ch_replicated_database: 'bool' = False, clickhouse_lock_ttl: 'int | None' = None, lock_namespace: 'str | None' = None, migration_hooks: 'dict[str, list[Callable[..., Any]]] | None' = None, environments: 'list[Any] | None' = None, split_at_severity: 'str | None' = None, max_severity: 'str' = 'CRITICAL', strict_pending: 'bool' = False, **plugin_config: 'Any') -> 'DatabaseHandle'
```

### `EnvironmentConfig`

```text
EnvironmentConfig(name: 'str', url_env: 'str', persistent: 'bool') -> None
```

Declared fields: `name`, `url_env`, `persistent`.

### `DbwardenConfig`

```text
DbwardenConfig()
```

### `DbwardenDatabase`

```text
DbwardenDatabase()
```

### `ChEngineSpec`

```text
ChEngineSpec(name: 'str', args: 'tuple[str, ...]' = (), zookeeper_path: 'str | None' = None, replica_name: 'str | None' = None, settings: 'dict[str, str] | None' = None) -> None
```

Declared fields: `name`, `args`, `zookeeper_path`, `replica_name`, `settings`.

Methods declared on this class:

```text
to_dict(self) -> 'dict[str, Any]'
from_dict(d: 'dict') -> 'ChEngineSpec'
from_engine_string(engine_str: 'str') -> 'ChEngineSpec'
```

### `CHTableMeta`

```text
CHTableMeta()
```

Declared fields: `comment`, `indexes`, `checks`, `uniques`, `primary_key`, `ch_indexes`, `ch`, `ch_engine`, `ch_order_by`, `ch_primary_key`, `ch_partition_by`, `ch_sample_by`, `ch_ttl`, `ch_settings`, `ch_object_type`, `ch_select_statement`, `ch_to_table`, `ch_dictionary`, `ch_dict_layout`, `ch_dict_source`, `ch_dict_lifetime`, `ch_dict_primary_key`, `ch_projections`, `ch_zookeeper_path`, `ch_replica_name`.

### `MyColumnMeta`

```text
MyColumnMeta()
```

Declared fields: `comment`, `public`, `my`.

### `MyTableMeta`

```text
MyTableMeta()
```

Declared fields: `comment`, `indexes`, `checks`, `uniques`, `primary_key`, `my_engine`, `my_charset`, `my_collate`, `my_row_format`, `my_auto_increment`, `my_indexes`, `my_checks`, `my_uniques`.

### `PGViewMeta`

```text
PGViewMeta()
```

Declared fields: `comment`, `indexes`, `checks`, `uniques`, `primary_key`, `pg_view_query`, `pg_view_materialized`, `pg_view_auto_refresh`, `pg_schema`.

### `SqColumnMeta`

```text
SqColumnMeta()
```

Declared fields: `comment`, `public`, `sq`.

### `SqTableMeta`

```text
SqTableMeta()
```

Declared fields: `comment`, `indexes`, `checks`, `uniques`, `primary_key`, `sq_without_rowid`, `sq_strict`, `sq_indexes`.

### `load_plugins`

```text
load_plugins(*, interactive: 'bool' = False) -> 'None'
```

### `register_migration_hooks`

```text
register_migration_hooks(*, pre_migration_run: 'list[Callable[..., Any]] | None' = None, pre_migration: 'list[Callable[..., Any]] | None' = None, migration_progress: 'list[Callable[..., Any]] | None' = None, post_migration: 'list[Callable[..., Any]] | None' = None, on_migration_failure: 'list[Callable[..., Any]] | None' = None, post_migration_run: 'list[Callable[..., Any]] | None' = None) -> 'None'
```

### `Seed`

```text
Seed()
```

Declared fields: `model`, `rows`.

### `SeedRow`

```text
SeedRow(**kwargs: 'Any') -> 'None'
```

Declared fields: .

Methods declared on this class:

```text
to_dict(self) -> 'dict'
```

### `seed_data`

```text
seed_data(database: 'str', version: 'str' = '', description: 'str' = '', on_conflict: 'str' = 'ignore', conflict_columns: 'list[str] | None' = None)
```

## `dbwarden.cli`

### `app`

```text
app(*args: Any, **kwargs: Any) -> Any
```

## `dbwarden.commands.make_migrations`

### `RenameIntent`

```text
RenameIntent(table: str, old_name: str, new_name: str) -> None
```

Declared fields: `table`, `old_name`, `new_name`.

### `auto_discover_model_paths`

```text
auto_discover_model_paths() -> List[str]
```

### `build_migration_plan`

```text
build_migration_plan(migration_id: str, changes: list[dbwarden.engine.migration_name.Change], upgrade_sql: str, *, typed_ops: list[dict] | None = None, content: str | None = None, backend: str = '') -> dict[str, object]
```

### `generate_migration_sql`

```text
generate_migration_sql(tables: list, migrations_dir: str | None = None, database: str | None = None, db_name: str | None = None, confirmed_renames: set[tuple[str, str, str]] | None = None, resolved_from_map: dict[tuple[str, str, str], str] | None = None, safe_type_change: bool = False, confirmed_table_intents: set[tuple[str, str]] | None = None, table_resolved_from_map: dict[tuple[str, str], str] | None = None, concurrent: bool = True, clickhouse_engine_recreate: bool = False, drop_preserved_clickhouse_table: bool | None = None, postgres_auto_using: bool = False) -> tuple[str, str, list[dbwarden.engine.migration_name.Change]]
```

### `get_all_model_tables`

```text
get_all_model_tables(model_paths: Optional[List[str]] = None, db_name: str | None = None) -> List[dbwarden.engine.core.models.ModelTable]
```

### `get_current_model_state_path`

```text
get_current_model_state_path(db_name: str | None = None) -> pathlib.Path
```

### `get_database`

```text
get_database(name: 'str | None' = None) -> 'DatabaseConfig'
```

### `get_model_state_path`

```text
get_model_state_path(db_name: str | None = None, legacy: bool = False) -> pathlib.Path
```

### `get_pending_migration_statements`

```text
get_pending_migration_statements(migrations_dir: str) -> set[str]
```

### `make_migrations_cmd`

```text
make_migrations_cmd(description: str | None = None, verbose: bool = False, database: str | None = None, output_plan: bool = False, output_sql: bool = False, rename_flags: list[str] | None = None, safe_type_change: bool = False, rename_table_flags: list[str] | None = None, concurrent: bool = True, offline: bool = False, migration_type: str = 'versioned', clickhouse_engine_recreate: bool = False, drop_preserved_clickhouse_table: bool | None = None, postgres_auto_using: bool = False, perf: bool = False, split_at_severity: str | None = None, strict_pending: bool | None = None, dry_run: bool = False, parameters: dict | None = None, show_managed_values: bool = False) -> None
```

### `new_migration_cmd`

```text
new_migration_cmd(description: str, version: str | None = None, database: str | None = None, migration_type: str = 'versioned') -> None
```

## `dbwarden.config_registry`

### `register_reset_hook`

```text
register_reset_hook(hook: 'Callable[[], None]') -> 'None'
```

### `reset_registry`

```text
reset_registry() -> 'None'
```

### `registered_entries`

```text
registered_entries() -> 'list[DatabaseEntry]'
```

### `registered_project_config`

```text
registered_project_config() -> 'ProjectConfigEntry | None'
```

### `database_config`

Re-export of `dbwarden.config_registry.database_config`.

### `DbwardenDatabase`

Re-export of `dbwarden.config_registry.DbwardenDatabase`.

### `DbwardenConfig`

Re-export of `dbwarden.config_registry.DbwardenConfig`.

## `dbwarden.config_schema`

### `DatabaseEntry`

```text
DatabaseEntry(database_name: 'str', database_type: 'DatabaseType', database_url_sync: 'str | None' = None, database_url_async: 'str | None' = None, secure_values: 'bool' = False, skip_if_missing: 'bool' = False, default: 'bool' = False, migrations_dir: 'str | None' = None, migration_table: 'str | None' = None, model_paths: 'list[str] | None' = None, model_tables: 'list[str] | None' = None, data_paths: 'list[str]' = NOTHING, data_snapshot_dir: 'str' = '.dbwarden/data', snapshot_registry: 'str' = '.dbwarden/snapshots/registry.json', dev_database_type: 'DatabaseType | None' = None, dev_database_url: 'str | None' = None, overlap_models: 'bool' = False, auto_apply_seeds: 'bool' = False, seed_table: 'str | None' = None, pg_schema: 'str | None' = None, pg_migration_lock_timeout: 'int | None' = None, ch_cluster: 'str | None' = None, ch_replicated_database: 'bool' = False, clickhouse_lock_ttl: 'int | None' = None, lock_namespace: 'str | None' = None, recovery_policy: 'str' = 'halt', assume_session_pooling: 'bool' = False, tcp_keepalive: 'bool' = True, sqlite_busy_timeout: 'int | None' = None, per_statement_history: 'bool' = False, rename_policy: 'str' = 'prompt', split_at_severity: 'str | None' = None, max_severity: 'str' = 'CRITICAL', strict_pending: 'bool' = False, migration_hooks: 'dict[str, list] | None' = None, environments: 'list[Any] | None' = None, plugin_config: 'dict[str, Any]' = NOTHING) -> None
```

Declared fields: `database_name`, `database_type`, `database_url_sync`, `database_url_async`, `secure_values`, `skip_if_missing`, `default`, `migrations_dir`, `migration_table`, `model_paths`, `model_tables`, `data_paths`, `data_snapshot_dir`, `snapshot_registry`, `dev_database_type`, `dev_database_url`, `overlap_models`, `auto_apply_seeds`, `seed_table`, `pg_schema`, `pg_migration_lock_timeout`, `ch_cluster`, `ch_replicated_database`, `clickhouse_lock_ttl`, `lock_namespace`, `recovery_policy`, `assume_session_pooling`, `tcp_keepalive`, `sqlite_busy_timeout`, `per_statement_history`, `rename_policy`, `split_at_severity`, `max_severity`, `strict_pending`, `migration_hooks`, `environments`, `plugin_config`.

### `ProjectConfigEntry`

```text
ProjectConfigEntry(pre_migrate_safety: 'ProjectPolicy' = 'off', pre_migrate_impact: 'ProjectPolicy' = 'off', missing_plan: 'ProjectPolicy' = 'off', impact_paths: 'list[str]' = NOTHING) -> None
```

Declared fields: `pre_migrate_safety`, `pre_migrate_impact`, `missing_plan`, `impact_paths`.

### `MultiDatabaseConfig`

```text
MultiDatabaseConfig(default: 'str', databases: 'dict[str, DatabaseEntry]') -> None
```

Declared fields: `default`, `databases`.

### `structure_database_entry`

```text
structure_database_entry(kwargs: 'dict') -> 'DatabaseEntry'
```

### `structure_project_config`

```text
structure_project_config(kwargs: 'dict[str, Any]') -> 'ProjectConfigEntry'
```

## `dbwarden.connection`

### `get_db_connection`

```text
get_db_connection(db_name: str | None = None) -> collections.abc.Generator[typing.Any, None, None]
```

### `reset_connection_logging`

```text
reset_connection_logging() -> None
```

### `QueryMethod`

```text
QueryMethod(*values)
```

### `get_query`

```text
get_query(method: dbwarden.connection.queries.store.QueryMethod, db_name: str | None = None, **kwargs) -> str
```

## `dbwarden.data`

### `ArchiveTable`

```text
ArchiveTable(table: 'str', schema: 'str | None' = None) -> None
```

Declared fields: `table`, `schema`.

### `DataMeta`

```text
DataMeta()
```

### `DataTransition`

```text
DataTransition()
```

### `aggregate`

```text
aggregate(policy, expression)
```

### `archive_table`

```text
archive_table(table: 'str', *, schema=None) -> 'ArchiveTable'
```

### `batch`

```text
batch(*, size, key, max_duration=None, statement_timeout=None, lock_timeout=None, max_replication_lag=None)
```

### `case`

```text
case(*whens: 'tuple[Any, Any]', else_: 'Any' = <object object>) -> 'Expr'
```

### `cast`

```text
cast(value: 'Any', type_: 'Any') -> 'Expr'
```

### `col`

```text
col(name: 'str', table: 'str | None' = None) -> 'Expr'
```

### `derive`

```text
derive(target, expression, *, rollback, when=None, on_unmatched='error', max_rows=None, category=None, settings=None, execution=None) -> 'Derivation'
```

### `from_source`

```text
from_source(source, *, identity, map, priority)
```

### `func`

```text
func
```

### `historical_table`

```text
historical_table(table: 'str', *, snapshot: 'str | None' = None, schema=None) -> 'HistoricalTable'
```

### `into`

```text
into(model, *, map, key=None, where=None, on_conflict='error', acknowledge_overwrite=False, priority=None) -> 'TransitionTarget'
```

### `literal`

```text
literal(value: 'Any') -> 'Expr'
```

### `mapping`

```text
mapping(value: 'Any', values: 'Mapping[Any, Any]', *, else_: 'Any' = <object object>) -> 'Expr'
```

### `merge_sources`

```text
merge_sources(*inputs, key, values, allow_empty=False)
```

### `param`

```text
param(name: 'str', type: 'Any' = None) -> 'Expr'
```

### `rows`

```text
rows(*, key=None, rows=None, source=None, owned_columns=None, on_missing='keep', scope=None, acknowledge_delete=False, archive_to=None, acknowledge_archive=False, rollback='irreversible', category=None) -> 'ManagedRows'
```

### `validate`

```text
validate(expression, *, message='Data validation failed') -> 'Validation'
```

### `winner`

```text
winner(expression)
```

## `dbwarden.database`

### `get_db_connection`

Re-export of `dbwarden.connection.connection.get_db_connection`.

### `reset_connection_logging`

Re-export of `dbwarden.connection.connection.reset_connection_logging`.

### `QueryMethod`

Re-export of `dbwarden.connection.queries.store.QueryMethod`.

### `get_query`

Re-export of `dbwarden.connection.queries.get_query`.

## `dbwarden.databases`

### `CheckSpec`

```text
CheckSpec(expression: 'str', name: 'str | None' = None, no_inherit: 'bool' = False) -> None
```

Declared fields: `expression`, `name`, `no_inherit`.

Methods declared on this class:

```text
as_dict(self) -> 'dict[str, Any]'
```

### `DBWardenMeta`

```text
DBWardenMeta(comment: 'str | None' = None, indexes: 'list[Any]' = <factory>, checks: 'list[Any]' = <factory>, uniques: 'list[Any]' = <factory>, primary_key: 'list[str]' = <factory>, partition: 'Any' = None, backend_table: 'Any' = None, pg_indexes: 'list[Any]' = <factory>, pg_checks: 'list[Any]' = <factory>, pg_uniques: 'list[Any]' = <factory>, pg_excludes: 'list[Any]' = <factory>, ch_indexes: 'list[Any]' = <factory>, my_indexes: 'list[Any]' = <factory>, my_checks: 'list[Any]' = <factory>, my_uniques: 'list[Any]' = <factory>, sq_indexes: 'list[Any]' = <factory>, table_attrs: 'dict[str, Any]' = <factory>) -> None
```

Declared fields: `comment`, `indexes`, `checks`, `uniques`, `primary_key`, `partition`, `backend_table`, `pg_indexes`, `pg_checks`, `pg_uniques`, `pg_excludes`, `ch_indexes`, `my_indexes`, `my_checks`, `my_uniques`, `sq_indexes`, `table_attrs`.

### `IndexSpec`

```text
IndexSpec(columns: 'list[str]', name: 'str | None' = None, unique: 'bool' = False, using: 'str | None' = None, where: 'str | None' = None, include: 'list[str] | None' = None, with_params: 'dict[str, Any] | None' = None, tablespace: 'str | None' = None, nulls_not_distinct: 'bool' = False, column_sorting: 'dict[str, str] | None' = None, comment: 'str | None' = None, concurrently: 'bool' = True, clickhouse_type: 'str | None' = None, clickhouse_granularity: 'int | None' = None) -> None
```

Declared fields: `columns`, `name`, `unique`, `using`, `where`, `include`, `with_params`, `tablespace`, `nulls_not_distinct`, `column_sorting`, `comment`, `concurrently`, `clickhouse_type`, `clickhouse_granularity`.

Methods declared on this class:

```text
to_dict(self) -> 'dict[str, Any]'
from_dict(d: 'dict') -> 'IndexSpec'
```

### `Seed`

Re-export of `dbwarden.seed.Seed`.

### `SeedRow`

Re-export of `dbwarden.seed.SeedRow`.

### `TableMeta`

```text
TableMeta()
```

Declared fields: `comment`, `indexes`, `checks`, `uniques`, `primary_key`.

### `UniqueSpec`

```text
UniqueSpec(columns: 'list[str]', name: 'str | None' = None, nulls_not_distinct: 'bool' = False, deferrable: 'bool' = False, initially_deferred: 'bool' = False, include: 'list[str] | None' = None) -> None
```

Declared fields: `columns`, `name`, `nulls_not_distinct`, `deferrable`, `initially_deferred`, `include`.

Methods declared on this class:

```text
as_dict(self) -> 'dict[str, Any]'
```

### `apply_meta`

```text
apply_meta(cls: 'type') -> 'None'
```

### `attach_meta`

```text
attach_meta(cls, incoming: 'DBWardenMeta') -> 'None'
```

### `ch`

```text
ch
```

### `check`

```text
check(name: 'str', expression: 'str', *, no_inherit: 'bool' = False) -> 'dict[str, Any]'
```

### `mdb`

```text
mdb
```

### `my`

```text
my
```

### `pg`

```text
pg
```

### `read_meta`

```text
read_meta(cls) -> 'DBWardenMeta | None'
```

### `seed_data`

Re-export of `dbwarden.seed.seed_data`.

### `sq`

```text
sq
```

### `unique`

```text
unique(name: 'str', columns: 'list[str]', *, nulls_not_distinct: 'bool' = False, deferrable: 'bool' = False, initially_deferred: 'bool' = False, include: 'list[str] | None' = None) -> 'dict[str, Any]'
```

## `dbwarden.databases.clickhouse`

### `AggregatingView`

```text
AggregatingView()
```

Declared fields: .

### `AggregatingViewSpec`

```text
AggregatingViewSpec(source: 'Any' = '', group_by: 'tuple[Any, ...]' = (), aggregates: 'tuple[Any, ...]' = (), order_by: 'tuple[Any, ...]' = (), partition_by: 'Any | None' = None, ttl: 'tuple[Any, ...] | None' = None, settings: 'dict[str, str] | None' = None, target_name: 'str | None' = None) -> None
```

Declared fields: `source`, `group_by`, `aggregates`, `order_by`, `partition_by`, `ttl`, `settings`, `target_name`.

Methods declared on this class:

```text
mv_name (property)
target_columns (property)
to_dict(self) -> 'dict[str, Any]'
```

### `AggExpr`

```text
AggExpr(func: 'str', arg: 'Any', arg_types: 'tuple[str, ...]', alias: 'str | None' = None) -> None
```

Declared fields: `func`, `arg`, `arg_types`, `alias`.

Methods declared on this class:

```text
as_(self, alias: 'str') -> 'AggExpr'
target_type(self) -> 'str'
state_combinator(self, source_column_type: 'str | None' = None) -> 'str'
```

### `ChAggStateType`

```text
ChAggStateType(func: 'str', *types: 'str') -> 'None'
```

### `ChEngineSpec`

Re-export of `dbwarden.databases.clickhouse.engine.ChEngineSpec`.

### `ChFieldSpec`

```text
ChFieldSpec(codec: 'str | None' = None, default_expression: 'str | None' = None, materialized: 'str | None' = None, alias: 'str | None' = None, ephemeral: 'str | None' = None, ttl: 'str | None' = None, low_cardinality: 'bool' = False, nullable: 'bool' = False, type: 'str | None' = None) -> None
```

Declared fields: `codec`, `default_expression`, `materialized`, `alias`, `ephemeral`, `ttl`, `low_cardinality`, `nullable`, `type`.

Methods declared on this class:

```text
to_col_info(self) -> 'dict[str, Any]'
```

### `ChGrantSpec`

```text
ChGrantSpec(privileges: 'tuple[str, ...]', on: 'str', to: 'str', with_grant_option: 'bool' = False) -> None
```

Declared fields: `privileges`, `on`, `to`, `with_grant_option`.

### `ChIndexSpec`

```text
ChIndexSpec(name: 'str', columns: 'list[str]', type: "Literal['minmax', 'set', 'bloom_filter', 'ngrambf_v1', 'tokenbf_v1', 'hypothesis'] | str" = 'minmax', granularity: 'int' = 1, expr: 'str | None' = None) -> None
```

Declared fields: `name`, `columns`, `type`, `granularity`, `expr`.

Methods declared on this class:

```text
to_dict(self) -> 'dict[str, Any]'
from_dict(d: 'dict') -> 'ChIndexSpec'
```

### `ChQuotaSpec`

```text
ChQuotaSpec(name: 'str', interval: 'str', limits: 'dict[str, int]', to_roles: 'tuple[str, ...]' = ('ALL',)) -> None
```

Declared fields: `name`, `interval`, `limits`, `to_roles`.

### `ChRaw`

```text
ChRaw(sql: 'str') -> None
```

Declared fields: `sql`.

### `ChRoleSpec`

```text
ChRoleSpec(name: 'str', settings: 'dict[str, str] | None' = None) -> None
```

Declared fields: `name`, `settings`.

### `ChRowPolicySpec`

```text
ChRowPolicySpec(name: 'str', table: 'str', using: 'str', to_roles: 'tuple[str, ...]' = ('ALL',), permissive: 'bool' = True) -> None
```

Declared fields: `name`, `table`, `using`, `to_roles`, `permissive`.

### `ChSettingsProfileSpec`

```text
ChSettingsProfileSpec(name: 'str', settings: 'dict[str, str]', to_roles: 'tuple[str, ...]' = ()) -> None
```

Declared fields: `name`, `settings`, `to_roles`.

### `ChTableSpec`

```text
ChTableSpec(engine: 'ChEngineSpec | str | None' = None, order_by: 'list[Any] | Any | None' = None, primary_key: 'list[Any] | Any | None' = None, partition_by: 'Any | None' = None, sample_by: 'Any | None' = None, ttl: 'list[Any] | Any | None' = None, settings: 'MergeTreeSettings | None' = None, projections: 'list[ProjectionSpec] | None' = None, indexes: 'list[ChIndexSpec] | None' = None, zookeeper_path: 'str | None' = None, replica_name: 'str | None' = None, select_statement: 'Any | None' = None, to_table: 'str | None' = None) -> None
```

Declared fields: `engine`, `order_by`, `primary_key`, `partition_by`, `sample_by`, `ttl`, `settings`, `projections`, `indexes`, `zookeeper_path`, `replica_name`, `select_statement`, `to_table`.

### `ChUserSpec`

```text
ChUserSpec(name: 'str', auth: 'str' = 'no_password', roles: 'tuple[str, ...]' = (), default_roles: 'tuple[str, ...]' = (), host: 'str' = 'ANY', settings_profile: 'str | None' = None) -> None
```

Declared fields: `name`, `auth`, `roles`, `default_roles`, `host`, `settings_profile`.

### `CHColumnMeta`

```text
CHColumnMeta()
```

Declared fields: `comment`, `public`, `ch`.

### `CHTableMeta`

Re-export of `dbwarden.schema.table_meta.CHTableMeta`.

### `CHViewMeta`

```text
CHViewMeta()
```

Declared fields: `comment`, `indexes`, `checks`, `uniques`, `primary_key`, `ch`.

### `ChView`

```text
ChView()
```

Declared fields: .

### `ClusterMode`

```text
ClusterMode(*values)
```

### `DataOp`

```text
DataOp(name: 'str', forward: 'str', rollback: 'str | None' = None, requires_confirmation: 'bool' = False) -> None
```

Declared fields: `name`, `forward`, `rollback`, `requires_confirmation`.

### `DictSpec`

```text
DictSpec(layout: 'str', source: 'dict[str, Any] | str', lifetime: 'int | str | None' = None, primary_key: 'str | list[str] | None' = None) -> None
```

Declared fields: `layout`, `source`, `lifetime`, `primary_key`.

Methods declared on this class:

```text
to_dict(self) -> 'dict[str, Any]'
```

### `KafkaSettings`

```text
KafkaSettings
```

Declared fields: `kafka_broker_list`, `kafka_topic_list`, `kafka_group_name`, `kafka_format`, `kafka_row_delimiter`, `kafka_schema`, `kafka_num_consumers`, `kafka_max_block_size`, `kafka_skip_broken_messages`, `kafka_commit_every_batch`, `kafka_client_id`, `kafka_poll_timeout_ms`, `kafka_flush_interval_ms`, `kafka_thread_per_consumer`, `kafka_handle_error_mode`.

### `MaterializedView`

```text
MaterializedView()
```

Declared fields: .

### `MaterializedViewSpec`

```text
MaterializedViewSpec(name: 'str | None' = None, select: 'Any' = None, to: 'str | None' = None, refresh: 'str | None' = None, populate: 'bool' = False, engine: 'Any' = None, order_by: 'Any' = None, partition_by: 'Any' = None, ttl: 'Any' = None, settings: 'dict[str, str] | None' = None) -> None
```

Declared fields: `name`, `select`, `to`, `refresh`, `populate`, `engine`, `order_by`, `partition_by`, `ttl`, `settings`.

Methods declared on this class:

```text
to_dict(self) -> 'dict[str, Any]'
```

### `MergeTreeSettings`

```text
MergeTreeSettings
```

Declared fields: `index_granularity`, `index_granularity_bytes`, `min_index_granularity_bytes`, `enable_mixed_granularity_parts`, `ttl_only_drop_parts`, `merge_with_ttl_timeout`, `merge_with_recompression_ttl_timeout`, `write_final_mark`, `merge_max_block_size`, `max_part_loading_threads`, `max_parts_in_total`, `replicated_deduplication_window`, `replicated_deduplication_window_seconds`, `replicated_can_become_leader`, `min_bytes_for_wide_part`, `min_rows_for_wide_part`, `max_parts_in_memory`, `max_bytes_to_merge_at_max_space_in_pool`, `min_bytes_to_use_direct_io`, `merge_tree_clear_old_broken_detached`, `parts_to_throw_insert`, `parts_to_delay_insert`, `inactive_parts_to_throw_in_insert`, `inactive_parts_to_delay_in_insert`, `max_delay_to_insert`, `max_suspicious_broken_parts`.

### `NamedCollectionSpec`

```text
NamedCollectionSpec(name: 'str', entries: 'dict[str, str]' = <factory>, overridable: 'dict[str, bool] | None' = None) -> None
```

Declared fields: `name`, `entries`, `overridable`.

Methods declared on this class:

```text
to_dict(self) -> 'dict'
```

### `NATSSettings`

```text
NATSSettings
```

Declared fields: `nats_url`, `nats_subjects`, `nats_format`, `nats_num_consumers`, `nats_max_block_size`, `nats_skip_broken_messages`, `nats_flush_interval_ms`, `nats_commit_every_batch`, `nats_username`, `nats_password`.

### `ProjectionSpec`

```text
ProjectionSpec(name: 'str', query: 'str') -> None
```

Declared fields: `name`, `query`.

Methods declared on this class:

```text
to_dict(self) -> 'dict[str, Any]'
from_dict(d: 'dict') -> 'ProjectionSpec'
```

### `RabbitMQSettings`

```text
RabbitMQSettings
```

Declared fields: `rabbitmq_host`, `rabbitmq_port`, `rabbitmq_virtual_host`, `rabbitmq_username`, `rabbitmq_password`, `rabbitmq_routing_key_list`, `rabbitmq_exchange_name`, `rabbitmq_format`, `rabbitmq_exchange_type`, `rabbitmq_routing_key`, `rabbitmq_num_consumers`, `rabbitmq_max_block_size`, `rabbitmq_flush_interval_ms`, `rabbitmq_skip_broken_messages`, `rabbitmq_commit_every_batch`, `rabbitmq_queue_base`, `rabbitmq_persistent`.

### `RedisSettings`

```text
RedisSettings
```

Declared fields: `redis_host`, `redis_port`, `redis_password`, `redis_storage`.

### `S3QueueSettings`

```text
S3QueueSettings
```

Declared fields: `s3queue_buckets`, `s3queue_processing_threads`, `s3queue_tracking_poll_timeout_ms`, `s3queue_tracking_window_ms`, `s3queue_last_processed_node`.

### `S3Settings`

```text
S3Settings
```

Declared fields: `s3_access_key_id`, `s3_secret_access_key`, `s3_format`, `s3_compression`, `s3_compression_method`, `s3_compression_level`.

### `URLSettings`

```text
URLSettings
```

Declared fields: `url`, `url_format`.

### `agg`

```text
agg
```

### `aggregating_merge_tree`

```text
aggregating_merge_tree(*, settings: 'MergeTreeSettings | None' = None) -> 'ChEngineSpec'
```

### `aggregating_view`

```text
aggregating_view(*, source: 'Any', group_by: 'Any' = None, aggregates: 'Any' = None, order_by: 'Any' = None, partition_by: 'Any' = None, ttl: 'Any' = None, settings: 'dict[str, str] | None' = None, name: 'str | None' = None, **deprecated: 'Any') -> 'AggregatingViewSpec'
```

### `buffer`

```text
buffer(database: 'str', table: 'str', num_layers: 'int', min_time: 'int', max_time: 'int', min_rows: 'int', max_rows: 'int', min_bytes: 'int', max_bytes: 'int', *, settings: 'MergeTreeSettings | None' = None) -> 'ChEngineSpec'
```

### `ch`

```text
ch
```

### `ch_agg_state`

```text
ch_agg_state(func: 'str', *types: 'str') -> 'ChAggStateType'
```

### `ch_raw`

```text
ch_raw(sql: 'str') -> 'ChRaw'
```

### `ch_table`

```text
ch_table(*, engine: 'ChEngineSpec | str | None' = None, order_by: 'Any' = None, primary_key: 'Any' = None, partition_by: 'Any' = None, sample_by: 'Any' = None, ttl: 'Any' = None, settings: 'MergeTreeSettings | None' = None, projections: 'list[ProjectionSpec] | None' = None, indexes: 'list[ChIndexSpec] | None' = None, zookeeper_path: 'str | None' = None, replica_name: 'str | None' = None) -> 'ChTableSpec'
```

### `ch_view_tables_from_models`

```text
ch_view_tables_from_models(model_paths: 'list[str] | None' = None, db_name: 'str | None' = None) -> 'list[ModelTable]'
```

### `collapsing_merge_tree`

```text
collapsing_merge_tree(sign_col: 'str', *, settings: 'MergeTreeSettings | None' = None) -> 'ChEngineSpec'
```

### `column_name_from_expr`

```text
column_name_from_expr(expr: 'ColumnElement | str | Any') -> 'str'
```

### `data_op`

```text
data_op(*, name: 'str', forward: 'str', rollback: 'str | None' = None, requires_confirmation: 'bool' = False) -> 'DataOp'
```

### `data_ops`

```text
data_ops
```

### `data_ops_populate`

```text
data_ops_populate(spec: 'MaterializedViewSpec | AggregatingViewSpec | dict[str, Any]', *, name: 'str | None' = None, rollback: 'str | None' = None) -> 'DataOp'
```

### `derive_agg_target_columns`

```text
derive_agg_target_columns(agg_result: 'AggregatingViewSpec | dict') -> 'list[str]'
```

### `dictionary`

```text
dictionary(*, layout: 'str', source: 'dict[str, Any] | str', lifetime: 'int | str | None' = None, primary_key: 'str | list[str] | None' = None) -> 'DictSpec'
```

### `dictionary_engine`

```text
dictionary_engine(dict_name: 'str') -> 'ChEngineSpec'
```

### `distributed`

```text
distributed(cluster: 'str', database: 'str', table: 'str', sharding_key: 'str | None' = None, *, policy_name: 'str | None' = None, settings: 'MergeTreeSettings | None' = None) -> 'ChEngineSpec'
```

### `field`

```text
field(*, codec: 'str | None' = None, default_expression: 'str | None' = None, materialized: 'str | None' = None, alias: 'str | None' = None, ephemeral: 'str | None' = None, ttl: 'str | None' = None, low_cardinality: 'bool' = False, nullable: 'bool' = False, type: 'str | None' = None) -> 'ChFieldSpec'
```

### `file_engine`

```text
file_engine(format: 'str', path: 'str | None' = None) -> 'ChEngineSpec'
```

### `get_all_ch_views`

```text
get_all_ch_views(model_paths: 'list[str] | None' = None, db_name: 'str | None' = None) -> 'list[dict[str, Any]]'
```

### `graphite_merge_tree`

```text
graphite_merge_tree(config_section: 'str' = 'default', *, settings: 'MergeTreeSettings | None' = None) -> 'ChEngineSpec'
```

### `hdfs`

```text
hdfs(uri: 'str', format: 'str') -> 'ChEngineSpec'
```

### `join_engine`

```text
join_engine(strictness: 'str', kind: 'str', *key_cols: 'str') -> 'ChEngineSpec'
```

### `kafka`

```text
kafka(*, named_collection: 'str | None' = None, broker_list: 'str | None' = None, topic_list: 'str | None' = None, group_name: 'str | None' = None, format: 'str | None' = None, num_consumers: 'int | None' = None) -> 'ChEngineSpec'
```

### `log`

```text
log() -> 'ChEngineSpec'
```

### `materialized_view`

```text
materialized_view(*, name: 'str | None' = None, select: 'Any' = None, to: 'str | None' = None, refresh: 'str | None' = None, populate: 'bool' = False, engine: 'Any' = None, order_by: 'Any' = None, partition_by: 'Any' = None, ttl: 'Any' = None, settings: 'dict[str, str] | None' = None, **deprecated: 'Any') -> 'MaterializedViewSpec'
```

### `memory`

```text
memory() -> 'ChEngineSpec'
```

### `merge`

```text
merge(db_regex: 'str', table_regex: 'str') -> 'ChEngineSpec'
```

### `merge_tree`

```text
merge_tree(*, settings: 'MergeTreeSettings | None' = None) -> 'ChEngineSpec'
```

### `mongodb`

```text
mongodb(host: 'str', port: 'int', database: 'str', table: 'str', user: 'str', password: 'str') -> 'ChEngineSpec'
```

### `mysql_engine`

```text
mysql_engine(host: 'str', port: 'int', database: 'str', table: 'str', user: 'str', password: 'str') -> 'ChEngineSpec'
```

### `named_collection`

```text
named_collection(name: 'str', *, overridable: 'dict[str, bool] | None' = None, **entries: 'str') -> 'NamedCollectionSpec'
```

### `nats`

```text
nats(*, named_collection: 'str | None' = None, url: 'str | None' = None, subjects: 'str | None' = None, format: 'str | None' = None) -> 'ChEngineSpec'
```

### `null`

```text
null() -> 'ChEngineSpec'
```

### `postgresql_engine`

```text
postgresql_engine(host: 'str', port: 'int', database: 'str', table: 'str', user: 'str', password: 'str') -> 'ChEngineSpec'
```

### `projection`

```text
projection(name: 'str', query: 'str') -> 'ProjectionSpec'
```

### `rabbitmq`

```text
rabbitmq(*, named_collection: 'str | None' = None, host: 'str | None' = None, format: 'str | None' = None, exchange_name: 'str | None' = None, routing_key: 'str | None' = None) -> 'ChEngineSpec'
```

### `redis`

```text
redis(host: 'str', port: 'int', password: 'str', storage: 'str | int') -> 'ChEngineSpec'
```

### `render_expr`

```text
render_expr(expr: 'ColumnElement | str | Any') -> 'str'
```

### `render_expr_list`

```text
render_expr_list(items: 'list[ColumnElement | str | Any] | None') -> 'list[str]'
```

### `replicated_merge_tree`

```text
replicated_merge_tree(zookeeper_path: 'str', replica_name: 'str', *args: 'str', settings: 'MergeTreeSettings | None' = None) -> 'ChEngineSpec'
```

### `replacing_merge_tree`

```text
replacing_merge_tree(version_col: 'str | None' = None, *, settings: 'MergeTreeSettings | None' = None) -> 'ChEngineSpec'
```

### `s3`

```text
s3(*, named_collection: 'str | None' = None, path: 'str | None' = None, format: 'str | None' = None, compression: 'str | None' = None, access_key_id: 'str | None' = None, secret_access_key: 'str | None' = None) -> 'ChEngineSpec'
```

### `s3_queue`

```text
s3_queue(*, named_collection: 'str | None' = None, path: 'str | None' = None, format: 'str | None' = None, compression: 'str | None' = None) -> 'ChEngineSpec'
```

### `set_engine`

```text
set_engine() -> 'ChEngineSpec'
```

### `skip_index`

```text
skip_index(name: 'str', columns: 'list[str]', type: "Literal['minmax', 'set', 'bloom_filter', 'ngrambf_v1', 'tokenbf_v1', 'hypothesis'] | str" = 'minmax', *, granularity: 'int' = 1, expr: 'str | None' = None) -> 'ChIndexSpec'
```

### `stripe_log`

```text
stripe_log() -> 'ChEngineSpec'
```

### `summing_merge_tree`

```text
summing_merge_tree(*columns: 'str', settings: 'MergeTreeSettings | None' = None) -> 'ChEngineSpec'
```

### `tiny_log`

```text
tiny_log() -> 'ChEngineSpec'
```

### `url_engine`

```text
url_engine(url: 'str', format: 'str') -> 'ChEngineSpec'
```

### `versioned_collapsing_merge_tree`

```text
versioned_collapsing_merge_tree(sign_col: 'str', version_col: 'str', *, settings: 'MergeTreeSettings | None' = None) -> 'ChEngineSpec'
```

## `dbwarden.databases.mariadb`

### `MdbColumnMeta`

```text
MdbColumnMeta()
```

Declared fields: `comment`, `public`, `mdb`.

### `MdbFieldSpec`

```text
MdbFieldSpec(invisible: 'bool' = False, without_overlaps: 'bool' = False, sequence: 'str | None' = None) -> None
```

Declared fields: `invisible`, `without_overlaps`, `sequence`.

Methods declared on this class:

```text
to_col_info(self) -> 'dict[str, Any]'
```

### `MdbTableMeta`

```text
MdbTableMeta()
```

Declared fields: `comment`, `indexes`, `checks`, `uniques`, `primary_key`, `my_engine`, `my_charset`, `my_collate`, `my_row_format`, `my_auto_increment`, `my_indexes`, `my_checks`, `my_uniques`, `mdb_page_compressed`, `mdb_page_compression_level`.

### `MdbTableSpec`

```text
MdbTableSpec(engine: 'str' = 'InnoDB', charset: 'str' = 'utf8mb4', collate: 'str' = 'utf8mb4_unicode_ci', row_format: 'str | None' = None, auto_increment: 'int | None' = None, page_compressed: 'bool' = False, page_compression_level: 'int | None' = None) -> None
```

Declared fields: `engine`, `charset`, `collate`, `row_format`, `auto_increment`, `page_compressed`, `page_compression_level`.

### `field`

```text
field(*, invisible: 'bool' = False, without_overlaps: 'bool' = False, sequence: 'str | None' = None) -> 'MdbFieldSpec'
```

### `mdb`

```text
mdb
```

## `dbwarden.databases.mysql`

### `MyColumnMeta`

Re-export of `dbwarden.schema.table_meta.MyColumnMeta`.

### `MyFieldSpec`

```text
MyFieldSpec(charset: 'str | None' = None, collate: 'str | None' = None, unsigned: 'bool' = False, on_update: 'str | None' = None) -> None
```

Declared fields: `charset`, `collate`, `unsigned`, `on_update`.

Methods declared on this class:

```text
to_col_info(self) -> 'dict[str, Any]'
```

### `MyTableMeta`

Re-export of `dbwarden.schema.table_meta.MyTableMeta`.

### `MyTableSpec`

```text
MyTableSpec(engine: 'str' = 'InnoDB', charset: 'str' = 'utf8mb4', collate: 'str' = 'utf8mb4_unicode_ci', row_format: 'str | None' = None, auto_increment: 'int | None' = None) -> None
```

Declared fields: `engine`, `charset`, `collate`, `row_format`, `auto_increment`.

### `field`

```text
field(*, charset: 'str | None' = None, collate: 'str | None' = None, unsigned: 'bool' = False, on_update: 'str | None' = None) -> 'MyFieldSpec'
```

### `my`

```text
my
```

## `dbwarden.databases.pgsql`

### `ExcludeSpec`

```text
ExcludeSpec(name: 'str | None' = None, using: 'str' = 'gist', where: 'str | None' = None, elements: 'list[dict[str, Any]] | None' = None) -> None
```

Declared fields: `name`, `using`, `where`, `elements`.

Methods declared on this class:

```text
as_dict(self) -> 'dict[str, Any]'
```

### `PGColumnMeta`

```text
PGColumnMeta()
```

Declared fields: `comment`, `public`, `pg`.

### `PGTableMeta`

```text
PGTableMeta()
```

Declared fields: `comment`, `indexes`, `checks`, `uniques`, `primary_key`, `pg_schema`, `pg_fillfactor`, `pg_tablespace`, `pg_inherits`, `pg_partition_of`, `pg_partition_bound`, `pg_unlogged`, `pg_checks`, `pg_uniques`, `pg_excludes`, `pg_indexes`, `pg_partition`, `pg_rls`, `pg_rls_force`, `pg_policies`, `pg_grants`, `pg_storage_params`.

### `PGViewMeta`

Re-export of `dbwarden.schema.table_meta.PGViewMeta`.

### `PgFieldSpec`

```text
PgFieldSpec(collation: 'str | None' = None, storage: 'str | None' = None, compression: 'str | None' = None, generated: 'str | None' = None, identity: 'str | None' = None, identity_start: 'int | None' = None, identity_increment: 'int | None' = None, identity_min: 'int | None' = None, identity_max: 'int | None' = None) -> None
```

Declared fields: `collation`, `storage`, `compression`, `generated`, `identity`, `identity_start`, `identity_increment`, `identity_min`, `identity_max`.

Methods declared on this class:

```text
to_col_info(self) -> 'dict[str, Any]'
```

### `PgIndexSpec`

```text
PgIndexSpec(name: 'str', columns: 'list[str]', unique: 'bool' = False, using: 'str | None' = None, where: 'str | None' = None, include: 'list[str] | None' = None, with_params: 'dict[str, Any] | None' = None, tablespace: 'str | None' = None, nulls_not_distinct: 'bool' = False, column_sorting: 'dict[str, str] | None' = None, postgresql_ops: 'dict[str, str] | None' = None, concurrently: 'bool' = True, expression: 'str | None' = None) -> None
```

Declared fields: `name`, `columns`, `unique`, `using`, `where`, `include`, `with_params`, `tablespace`, `nulls_not_distinct`, `column_sorting`, `postgresql_ops`, `concurrently`, `expression`.

Methods declared on this class:

```text
to_dict(self) -> 'dict[str, Any]'
from_dict(d: 'dict') -> 'PgIndexSpec'
```

### `PgTableSpec`

```text
PgTableSpec(tablespace: 'str | None' = None, fillfactor: 'int | None' = None, unlogged: 'bool' = False, inherits: 'list[str] | None' = None, schema: 'str | None' = None, partition: 'dict | None' = None) -> None
```

Declared fields: `tablespace`, `fillfactor`, `unlogged`, `inherits`, `schema`, `partition`.

### `PgViewSpec`

```text
PgViewSpec(query: 'str | None' = None, materialized: 'bool' = False, schema: 'str | None' = None, auto_refresh: 'bool' = False) -> None
```

Declared fields: `query`, `materialized`, `schema`, `auto_refresh`.

### `exclude`

```text
exclude(name: 'str', *, using: 'str' = 'gist', where: 'str | None' = None, elements: 'list[dict[str, Any]] | None' = None) -> 'dict[str, Any]'
```

### `field`

```text
field(*, collation: 'str | None' = None, storage: 'str | None' = None, compression: 'str | None' = None, generated: 'str | None' = None, identity: 'str | None' = None, identity_start: 'int | None' = None, identity_increment: 'int | None' = None, identity_min: 'int | None' = None, identity_max: 'int | None' = None) -> 'PgFieldSpec'
```

### `index`

```text
index(name: 'str', columns: 'list[str]', *, unique: 'bool' = False, using: 'str | None' = None, where: 'str | None' = None, include: 'list[str] | None' = None, with_params: 'dict[str, Any] | None' = None, tablespace: 'str | None' = None, nulls_not_distinct: 'bool' = False, column_sorting: 'dict[str, str] | None' = None, postgresql_ops: 'dict[str, str] | None' = None, concurrently: 'bool' = True, expression: 'str | None' = None) -> 'dict[str, Any]'
```

### `partition_by_hash`

```text
partition_by_hash(column: 'str', partitions: 'int') -> 'dict[str, Any]'
```

### `partition_by_list`

```text
partition_by_list(column: 'str') -> 'dict[str, Any]'
```

### `partition_by_range`

```text
partition_by_range(column: 'str', *, interval: 'str | None' = None) -> 'dict[str, Any]'
```

### `pg`

```text
pg
```

## `dbwarden.databases.sqlite`

### `SqColumnMeta`

Re-export of `dbwarden.schema.table_meta.SqColumnMeta`.

### `SqFieldSpec`

```text
SqFieldSpec(generated: 'str | None' = None, generated_mode: 'str' = 'STORED', collate: 'str | None' = None) -> None
```

Declared fields: `generated`, `generated_mode`, `collate`.

Methods declared on this class:

```text
to_col_info(self) -> 'dict[str, Any]'
```

### `SqTableMeta`

Re-export of `dbwarden.schema.table_meta.SqTableMeta`.

### `SqTableSpec`

```text
SqTableSpec(without_rowid: 'bool' = False, strict: 'bool' = False) -> None
```

Declared fields: `without_rowid`, `strict`.

### `field`

```text
field(*, generated: 'str | None' = None, generated_mode: 'str' = 'STORED', collate: 'str | None' = None) -> 'SqFieldSpec'
```

### `sq`

```text
sq
```

## `dbwarden.db_handle`

### `DatabaseHandle`

```text
DatabaseHandle(name: 'str', db_type: 'str') -> 'None'
```

Methods declared on this class:

```text
session (property)
```

## `dbwarden.engine.backends.clickhouse`

### `Safety`

```text
Safety(*values)
```

### `analyze_clickhouse_options`

```text
analyze_clickhouse_options(table_snapshot: 'dict[str, Any]', model_table: 'ModelTable') -> 'list[SafetyIssue]'
```

### `classify_ch_column_change`

```text
classify_ch_column_change(key: 'str', *, change: 'dict | None' = None) -> 'Safety'
```

### `classify_ch_options_change`

```text
classify_ch_options_change(key: 'str') -> 'Safety'
```

### `classify_ch_safety`

```text
classify_ch_safety(op: 'dict', model_column: 'Any' = None, snapshot_column: 'Any' = None) -> 'list[SafetyIssue]'
```

### `extract_balanced_parens`

```text
extract_balanced_parens(match: 're.Match') -> 'str | None'
```

### `parse_dict_layout`

```text
parse_dict_layout(create_query: 'str') -> 'str | None'
```

### `parse_dict_lifetime`

```text
parse_dict_lifetime(create_query: 'str') -> 'int | str | None'
```

### `parse_dict_primary_key`

```text
parse_dict_primary_key(create_query: 'str') -> 'str | None'
```

### `parse_dict_source`

```text
parse_dict_source(create_query: 'str') -> 'str | None'
```

### `parse_mv_query`

```text
parse_mv_query(create_query: 'str') -> 'str | None'
```

### `parse_mv_to_table`

```text
parse_mv_to_table(create_query: 'str') -> 'str | None'
```

### `parse_projection_names`

```text
parse_projection_names(create_query: 'str') -> 'list[str]'
```

### `parse_projection_queries`

```text
parse_projection_queries(create_query: 'str') -> 'list[dict[str, str]]'
```

### `parse_replica_name`

```text
parse_replica_name(create_query: 'str', engine: 'str') -> 'str | None'
```

### `parse_settings`

```text
parse_settings(create_query: 'str') -> 'dict[str, str] | None'
```

### `parse_ttl_expressions`

```text
parse_ttl_expressions(create_query: 'str') -> 'list[str]'
```

### `parse_tuple_or_list`

```text
parse_tuple_or_list(value: 'Any') -> 'str | list[str] | None'
```

### `parse_zookeeper_path`

```text
parse_zookeeper_path(create_query: 'str', engine: 'str') -> 'str | None'
```

## `dbwarden.engine.backends.clickhouse.handlers`

### `ChAggTargetHandler`

```text
ChAggTargetHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `ChColumnHandler`

```text
ChColumnHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `ChCommentHandler`

```text
ChCommentHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `ChDataOpHandler`

```text
ChDataOpHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'tuple[list[Op], list[Op]]'
emit(self, op: 'Op', db_name: 'str | None' = None, **kwargs: 'Any') -> 'list[MigrationStatement]'
```

### `ChDictionaryHandler`

```text
ChDictionaryHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `ChMaterializedViewHandler`

```text
ChMaterializedViewHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `ChProjectionHandler`

```text
ChProjectionHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `ChSkipIndexHandler`

```text
ChSkipIndexHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `ChTableHandler`

```text
ChTableHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`, `clickhouse_engine_recreate`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

## `dbwarden.engine.backends.mariadb`

### `append_mariadb_column_attrs`

```text
append_mariadb_column_attrs(sql: 'str', meta: 'dict[str, Any]') -> 'str'
```

### `assert_complete_mariadb_type`

```text
assert_complete_mariadb_type(col_type: 'str') -> 'None'
```

### `extract_mariadb_meta`

```text
extract_mariadb_meta(connection, table_name: 'str', indexes: 'list[dict] | None' = None, checks: 'list[dict] | None' = None, uniques: 'list[dict] | None' = None) -> 'tuple[dict[str, Any], dict[str, dict[str, Any]]]'
```

### `mariadb_column_definition_for_meta`

```text
mariadb_column_definition_for_meta(col_type: 'str', meta: 'dict[str, Any]', nullable: 'bool | None' = None, default: 'str | None' = None, comment: 'str | None' = None, autoincrement: 'bool | None' = None) -> 'str'
```

### `normalize_mariadb_default`

```text
normalize_mariadb_default(d: 'Any') -> 'str | None'
```

### `normalize_mariadb_table_value`

```text
normalize_mariadb_table_value(key: 'str', value: 'Any') -> 'Any'
```

### `render_mariadb_column_type`

```text
render_mariadb_column_type(type_str: 'str', meta: 'dict[str, Any]') -> 'str'
```

### `resolve_mariadb_imports`

```text
resolve_mariadb_imports(columns: 'list[dict]') -> 'set[str]'
```

## `dbwarden.engine.backends.mysql`

### `append_mysql_column_attrs`

```text
append_mysql_column_attrs(sql: 'str', meta: 'dict[str, Any]') -> 'str'
```

### `assert_complete_mysql_type`

```text
assert_complete_mysql_type(col_type: 'str') -> 'None'
```

### `build_mysql_alter_default_sql`

```text
build_mysql_alter_default_sql(table: 'str', column: 'str', default: 'Any', col_type: 'str | None' = None, nullable: 'bool | None' = None, my_meta: 'dict[str, Any] | None' = None) -> 'tuple[str, str]'
```

### `extract_mysql_meta`

```text
extract_mysql_meta(connection, table_name: 'str', indexes: 'list[dict] | None' = None, checks: 'list[dict] | None' = None, uniques: 'list[dict] | None' = None) -> 'tuple[dict[str, Any], dict[str, dict[str, Any]]]'
```

### `mysql_column_definition_for_meta`

```text
mysql_column_definition_for_meta(col_type: 'str', meta: 'dict[str, Any]', nullable: 'bool | None' = None, default: 'str | None' = None, comment: 'str | None' = None, autoincrement: 'bool | None' = None) -> 'str'
```

### `normalize_mysql_default`

```text
normalize_mysql_default(d: 'Any') -> 'str | None'
```

### `normalize_mysql_table_value`

```text
normalize_mysql_table_value(key: 'str', value: 'Any') -> 'Any'
```

### `render_mysql_column_type`

```text
render_mysql_column_type(type_str: 'str', meta: 'dict[str, Any]') -> 'str'
```

### `resolve_mysql_imports`

```text
resolve_mysql_imports(columns: 'list[dict]') -> 'set[str]'
```

## `dbwarden.engine.backends.mysql.handlers`

### `MyTableHandler`

```text
MyTableHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

## `dbwarden.engine.backends.postgresql`

### `classify_enum_change`

```text
classify_enum_change(from_values: 'list[str]', to_values: 'list[str]') -> 'str'
```

### `classify_pg_type_change`

```text
classify_pg_type_change(from_type: 'dict[str, Any]', to_type: 'dict[str, Any]') -> 'str'
```

## `dbwarden.engine.backends.postgresql.handlers`

### `ColumnHandler`

```text
ColumnHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `ConstraintHandler`

```text
ConstraintHandler() -> 'None'
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `IndexHandler`

```text
IndexHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `PartitionHandler`

```text
PartitionHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `PgTableHandler`

```text
PgTableHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `RenameTableHandler`

```text
RenameTableHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `SchemaHandler`

```text
SchemaHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `StatisticsHandler`

```text
StatisticsHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `TableHandler`

```text
TableHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `TypeHandler`

```text
TypeHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `ViewHandler`

```text
ViewHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

## `dbwarden.engine.backends.sqlite`

### `extract_sqlite_column_meta`

```text
extract_sqlite_column_meta(inspector, connection, table_name: 'str', raw_columns: 'list[dict] | None' = None) -> 'dict[str, dict[str, Any]]'
```

### `extract_sqlite_meta`

```text
extract_sqlite_meta(connection, table_name: 'str', indexes: 'list[dict] | None' = None, checks: 'list[dict] | None' = None, uniques: 'list[dict] | None' = None, raw_columns: 'list[dict] | None' = None) -> 'tuple[dict[str, Any], dict[str, dict[str, Any]]]'
```

### `extract_sqlite_table_meta`

```text
extract_sqlite_table_meta(connection, table_name: 'str') -> 'dict[str, Any]'
```

### `resolve_sqlite_imports`

```text
resolve_sqlite_imports(columns: 'list[dict]') -> 'set[str]'
```

## `dbwarden.engine.backends.sqlite.handlers`

### `SqTableHandler`

```text
SqTableHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`, `op_types`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

## `dbwarden.engine.core`

### `RunPhase`

```text
RunPhase(*values)
```

### `Op`

```text
Op(object_type: 'str', upgrade_attrs: 'dict[str, Any]' = <factory>, rollback_attrs: 'dict[str, Any]' = <factory>, irreversible: 'bool' = False, category: 'str | None' = None, safety: 'str | None' = None) -> None
```

Declared fields: `object_type`, `upgrade_attrs`, `rollback_attrs`, `irreversible`, `category`, `safety`.

### `ObjectHandler`

```text
ObjectHandler(*args, **kwargs)
```

Declared fields: `object_type`, `run_phase`, `statement_order`, `ordering`.

Methods declared on this class:

```text
extract(self, snapshot: 'dict[str, Any]') -> 'dict[str, Any]'
model_spec_from_config(self, config: 'Any') -> 'dict[str, Any]'
model_spec_from_tables(self, model_tables: 'list[Any]') -> 'dict[str, Any]'
canonicalize(self, spec: 'dict[str, Any]') -> 'dict[str, Any]'
diff(self, snap_spec: 'dict[str, Any]', model_spec: 'dict[str, Any]') -> 'Tuple[List[Op], List[Op]]'
emit(self, op: 'Op', db_name: 'Optional[str]' = None, **kwargs: 'Any') -> 'List[MigrationStatement]'
```

### `Anchor`

```text
Anchor(*values)
```

### `OrderingConstraint`

```text
OrderingConstraint(after: 'tuple[Anchor, ...]' = (), before: 'tuple[Anchor, ...]' = (), after_object: 'tuple[str, ...]' = (), before_object: 'tuple[str, ...]' = ()) -> None
```

Declared fields: `after`, `before`, `after_object`, `before_object`.

### `OrderingError`

```text
OrderingError
```

### `StatementOrder`

```text
StatementOrder(*values)
```

### `MigrationStatement`

```text
MigrationStatement(order: 'StatementOrder', upgrade_sql: 'str', rollback_sql: 'str', rollback_kind: 'str' = 'real', rollback_reason: 'str | None' = None, category: 'str | None' = None, safety: 'str | None' = None) -> None
```

Declared fields: `order`, `upgrade_sql`, `rollback_sql`, `rollback_kind`, `rollback_reason`, `category`, `safety`.

### `Change`

```text
Change(operation: str, table: str, target: Optional[str] = None, resolved_from: Optional[str] = None, index_type: Optional[str] = None) -> None
```

Declared fields: `operation`, `table`, `target`, `resolved_from`, `index_type`.

### `RegistryDriver`

```text
RegistryDriver(*, include_plugins: 'bool' = True) -> 'None'
```

Methods declared on this class:

```text
register(self, handler: 'ObjectHandler') -> 'None'
run(self, snapshot: 'dict[str, Any]', model_tables: 'list[Any]', config: 'Any') -> 'Tuple[List[Op], List[Op]]'
emit_all(self, ops: 'List[Op]', db_name: 'Optional[str]' = None, cluster_ctx: 'Any' = None) -> 'List[MigrationStatement]'
emit_op_to_sql(self, upgrade_ops: 'List[Op]', rollback_ops: 'List[Op]', db_name: 'Optional[str]' = None, cluster_ctx: 'Any' = None) -> 'Tuple[str, str, List[Change]]'
```

### `ModelColumn`

```text
ModelColumn(name: 'str', type: 'str', nullable: 'bool', primary_key: 'bool', unique: 'bool', default: 'Optional[str]', foreign_key: 'Optional[str]', codec: 'Optional[str]' = None, comment: 'Optional[str]' = None, pg_meta: 'Optional[dict[str, Any]]' = None, ch_meta: 'Optional[dict[str, Any]]' = None, my_meta: 'Optional[dict[str, Any]]' = None, sq_meta: 'Optional[dict[str, Any]]' = None, autoincrement: 'Optional[bool]' = None, fk_on_delete: 'Optional[str]' = None, fk_on_update: 'Optional[str]' = None)
```

Methods declared on this class:

```text
to_dict(self) -> 'dict'
```

### `IndexInfo`

```text
IndexInfo(columns: 'list[str]', name: 'str | None' = None, unique: 'bool' = False, using: 'str | None' = None, where: 'str | None' = None, include: 'list[str] | None' = None, with_params: 'dict[str, Any] | None' = None, tablespace: 'str | None' = None, nulls_not_distinct: 'bool' = False, column_sorting: 'dict[str, str] | None' = None, postgresql_ops: 'dict[str, str] | None' = None, comment: 'str | None' = None, concurrently: 'bool' = True, clickhouse_type: 'str | None' = None, clickhouse_granularity: 'int | None' = None, expression: 'str | None' = None) -> None
```

Declared fields: `columns`, `name`, `unique`, `using`, `where`, `include`, `with_params`, `tablespace`, `nulls_not_distinct`, `column_sorting`, `postgresql_ops`, `comment`, `concurrently`, `clickhouse_type`, `clickhouse_granularity`, `expression`.

Methods declared on this class:

```text
to_dict(self) -> 'dict'
from_dict(d: 'dict') -> "'IndexInfo'"
```

### `ModelTable`

```text
ModelTable(name: 'str', columns: 'List[ModelColumn]', clickhouse_options: 'Optional[dict]' = None, object_type: 'str' = 'table', foreign_keys: 'Optional[list[dict]]' = None, indexes: 'Optional[list[dict | IndexInfo]]' = None, comment: 'str | None' = None, checks: 'Optional[list[dict[str, Any]]]' = None, uniques: 'Optional[list[dict[str, Any]]]' = None, excludes: 'Optional[list[dict[str, Any]]]' = None, pg_table: 'Optional[dict[str, Any]]' = None, my_table: 'Optional[dict[str, Any]]' = None, sq_table: 'Optional[dict[str, Any]]' = None, schema: 'str | None' = None, pg_view_definition: 'str | None' = None, pg_view_materialized: 'bool' = False, pg_view_auto_refresh: 'bool' = False, pg_policies: 'Optional[list[dict[str, Any]]]' = None, pg_grants: 'Optional[list[dict[str, Any]]]' = None)
```

Methods declared on this class:

```text
to_dict(self) -> 'dict'
```

### `STATE_FORMAT_VERSION`

```text
STATE_FORMAT_VERSION
```

### `model_state_to_dict`

```text
model_state_to_dict(tables: 'list[ModelTable]', dbwarden_version: 'str' = '') -> 'dict'
```

### `normalize_model_state`

```text
normalize_model_state(state: 'dict[str, Any]') -> 'dict[str, Any]'
```

### `reconstruct_model_table`

```text
reconstruct_model_table(table_entry: 'dict[str, Any]') -> 'ModelTable'
```

### `reconstruct_model_column`

```text
reconstruct_model_column(col_entry: 'dict[str, Any]') -> 'ModelColumn'
```

### `compute_checksum`

```text
compute_checksum(snapshot: 'dict[str, Any]') -> 'str'
```

### `get_schemas_directory`

```text
get_schemas_directory(database: 'str | None' = None) -> 'str'
```

### `write_snapshot`

```text
write_snapshot(snapshot: 'dict[str, Any]', database: 'str | None' = None, migration_id: 'str' = '') -> 'str'
```

### `read_snapshot`

```text
read_snapshot(filepath: 'str') -> 'dict[str, Any] | None'
```

### `find_latest_snapshot`

```text
find_latest_snapshot(database: 'str | None' = None) -> 'dict[str, Any] | None'
```

### `extract_snapshot_tables`

```text
extract_snapshot_tables(snapshot: 'dict[str, Any]') -> 'dict[str, set[str]]'
```

### `TableRenameIntent`

```text
TableRenameIntent(old_table: 'str', new_table: 'str') -> None
```

Declared fields: `old_table`, `new_table`.

### `RENAME_TABLE_OVERLAP_THRESHOLD`

```text
RENAME_TABLE_OVERLAP_THRESHOLD
```

### `detect_renames`

```text
detect_renames(table_name: 'str', dropped_columns: 'list[tuple[str, dict[str, Any]]]', added_columns: 'list[tuple[str, ModelColumn]]') -> 'list[tuple[str, str]]'
```

## `dbwarden.engine.core.plugin_api`

### `ClusterableStatement`

```text
ClusterableStatement(prefix: 'str', suffix: 'str', supports_cluster: 'bool' = True) -> None
```

Declared fields: `prefix`, `suffix`, `supports_cluster`.

Methods declared on this class:

```text
render(self, ctx: 'ClusterContext') -> 'str'
from_sql(sql: 'str') -> "'ClusterableStatement'"
to_migration(self, order: "'StatementOrder'", ctx: 'ClusterContext', rollback: "'ClusterableStatement | str | None'" = None) -> 'MigrationStatement'
```

### `REDACTED`

```text
REDACTED
```

### `build_alter_policy_sql`

```text
build_alter_policy_sql(policy: 'dict', qname: 'str') -> 'str'
```

### `build_create_policy_sql`

```text
build_create_policy_sql(policy: 'dict', qname: 'str') -> 'str'
```

### `build_grant_sql`

```text
build_grant_sql(grant_entry: 'dict', qname: 'str', object_type: 'str' = 'TABLE') -> 'str'
```

### `build_revoke_sql`

```text
build_revoke_sql(grant_entry: 'dict', qname: 'str', object_type: 'str' = 'TABLE') -> 'str'
```

### `emit_with_cluster`

```text
emit_with_cluster(method)
```

### `has_visible_secrets`

```text
has_visible_secrets(spec: 'dict') -> 'bool'
```

### `qualified_name`

```text
qualified_name(name: 'str', schema: 'str | None') -> 'str'
```

### `quote_pg`

```text
quote_pg(name: 'str') -> 'str'
```

### `strip_secret_values`

```text
strip_secret_values(spec: 'dict', secret_keys: 'frozenset[str]') -> 'dict'
```

## `dbwarden.engine.model_discovery`

### `IndexInfo`

Re-export of `dbwarden.engine.core.models.IndexInfo`.

### `ModelColumn`

Re-export of `dbwarden.engine.core.models.ModelColumn`.

### `ModelTable`

Re-export of `dbwarden.engine.core.models.ModelTable`.

### `DBWardenConfigError`

```text
DBWardenConfigError
```

### `auto_discover_model_paths`

Re-export of `dbwarden.engine.model_discovery.path_discovery.auto_discover_model_paths`.

### `discover_models_in_directory`

```text
discover_models_in_directory(directory: str) -> List[str]
```

### `load_model_from_path`

```text
load_model_from_path(filepath: str) -> Optional[module]
```

### `extract_column_info`

```text
extract_column_info(column, db_name: str | None = None, backend: str | None = None) -> Optional[dbwarden.engine.core.models.ModelColumn]
```

### `extract_table_from_model`

```text
extract_table_from_model(model_class: type, db_name: str | None = None) -> Optional[dbwarden.engine.core.models.ModelTable]
```

### `extract_tables_from_database`

```text
extract_tables_from_database(sqlalchemy_url: str) -> dict[str, set[str]]
```

### `extract_tables_from_migrations`

```text
extract_tables_from_migrations(migrations_dir: str) -> dict[str, set[str]]
```

### `filter_model_tables_by_name`

```text
filter_model_tables_by_name(tables: list[dbwarden.engine.core.models.ModelTable], allowed_names: list[str] | None) -> list[dbwarden.engine.core.models.ModelTable]
```

### `validate_model_tables_exist`

```text
validate_model_tables_exist(discovered_tables: list[dbwarden.engine.core.models.ModelTable], configured_names: list[str] | None, db_name: str) -> None
```

### `generate_add_column_sql`

```text
generate_add_column_sql(table_name: str, column: dbwarden.engine.core.models.ModelColumn, db_name: str | None = None, schema: str | None = None) -> str
```

### `generate_create_table_sql`

```text
generate_create_table_sql(table: dbwarden.engine.core.models.ModelTable, db_name: str | None = None) -> str
```

### `generate_drop_object_sql`

```text
generate_drop_object_sql(table: dbwarden.engine.core.models.ModelTable) -> str
```

### `generate_drop_table_sql`

```text
generate_drop_table_sql(table_name: str, schema: str | None = None) -> str
```

### `get_all_model_tables`

Re-export of `dbwarden.engine.model_discovery.get_all_model_tables`.

### `get_model_table_by_name`

```text
get_model_table_by_name(table_name: str, model_paths: list[str] | None = None, db_name: str | None = None) -> dbwarden.engine.core.models.ModelTable | None
```

## `dbwarden.exceptions`

### `ConfigurationError`

```text
ConfigurationError
```

### `DBDisconnectedError`

```text
DBDisconnectedError
```

### `DBWardenConfigError`

Re-export of `dbwarden.exceptions.core.DBWardenConfigError`.

### `DBWardenError`

```text
DBWardenError
```

### `DatabaseError`

```text
DatabaseError
```

### `DirectoryNotFoundError`

```text
DirectoryNotFoundError
```

### `HookConflictError`

```text
HookConflictError(hook_name: 'str', plugins: 'list[str]') -> 'None'
```

### `HookNotRegisteredError`

```text
HookNotRegisteredError(hook_name: 'str') -> 'None'
```

### `HookValidationError`

```text
HookValidationError
```

### `HookVetoError`

```text
HookVetoError
```

### `ImmutableChangeError`

```text
ImmutableChangeError
```

### `LockAcquireTimeout`

```text
LockAcquireTimeout
```

### `LockError`

```text
LockError
```

### `LockStuck`

```text
LockStuck
```

### `NoMigrationsError`

```text
NoMigrationsError
```

### `NoSeedsError`

```text
NoSeedsError
```

### `ObjectHandlerConflictError`

```text
ObjectHandlerConflictError(object_type: 'str', plugins: 'list[str]') -> 'None'
```

### `OrderingError`

Re-export of `dbwarden.exceptions.engine.OrderingError`.

### `PendingMigrationsError`

```text
PendingMigrationsError
```

### `PluginApiMismatchError`

```text
PluginApiMismatchError(dist_name: 'str', declared: 'object') -> 'None'
```

### `PluginInstallError`

```text
PluginInstallError
```

### `RecoveryRequired`

```text
RecoveryRequired
```

### `RollbackContractError`

```text
RollbackContractError
```

### `SeedError`

```text
SeedError
```

### `VersionNotFoundError`

```text
VersionNotFoundError
```

## `dbwarden.merge`

### `SupersededMarker`

```text
SupersededMarker(merged_into: 'str', merged_at: 'str', merge_base: 'str', branch: 'str', applied_persistent: 'str', file_checksum: 'str') -> None
```

Declared fields: `merged_into`, `merged_at`, `merge_base`, `branch`, `applied_persistent`, `file_checksum`.

### `parse_superseded_marker`

```text
parse_superseded_marker(file_path: 'str | Path') -> 'Optional[SupersededMarker]'
```

### `write_superseded_marker`

```text
write_superseded_marker(file_path: 'str | Path', marker: 'SupersededMarker') -> 'None'
```

### `is_superseded`

```text
is_superseded(file_path: 'str | Path') -> 'bool'
```

### `get_file_checksum`

```text
get_file_checksum(file_path: 'str | Path') -> 'str'
```

### `ReconciliationHeader`

```text
ReconciliationHeader(merge_base: 'str', merge_base_checksum: 'str', supersedes: 'list[str]', probe_results: 'dict[str, str]', generated_by: 'str', renames: 'list[dict] | None' = None) -> None
```

Declared fields: `merge_base`, `merge_base_checksum`, `supersedes`, `probe_results`, `generated_by`, `renames`.

### `parse_reconciliation_header`

```text
parse_reconciliation_header(file_path: 'str | Path') -> 'Optional[ReconciliationHeader]'
```

### `write_reconciliation_header`

```text
write_reconciliation_header(file_path: 'str | Path', header: 'ReconciliationHeader') -> 'None'
```

### `is_reconciliation`

```text
is_reconciliation(file_path: 'str | Path') -> 'bool'
```

### `MergeSignal`

```text
MergeSignal(*values)
```

### `detect_merge_signals`

```text
detect_merge_signals(db_name: 'str | None' = None) -> 'list[MergeSignal]'
```

### `EnvironmentConfig`

Re-export of `dbwarden.merge.environments.EnvironmentConfig`.

### `load_environments`

```text
load_environments(db_name: 'str | None' = None) -> 'list[EnvironmentConfig]'
```

### `is_persistent`

```text
is_persistent(environment: 'str', db_name: 'str | None' = None) -> 'bool'
```

### `get_persistent_environments`

```text
get_persistent_environments(db_name: 'str | None' = None) -> 'list[str]'
```

## `dbwarden.models`

### `MigrationType`

```text
MigrationType(*values)
```

### `MigrationDirection`

```text
MigrationDirection(*values)
```

### `MigrationRecord`

```text
MigrationRecord(order_executed: int, version: str | None, description: str, filename: str, migration_type: str, applied_at: datetime.datetime, checksum: str | None) -> None
```

Declared fields: `order_executed`, `version`, `description`, `filename`, `migration_type`, `applied_at`, `checksum`.

### `MigrationFile`

```text
MigrationFile(version: str | None, filename: str, filepath: str, upgrade_sql: list[str], rollback_sql: list[str], checksum: str) -> None
```

Declared fields: `version`, `filename`, `filepath`, `upgrade_sql`, `rollback_sql`, `checksum`.

### `SchemaDifference`

```text
SchemaDifference(type: str, table_name: str, column_name: str | None = None, sql: str = '') -> None
```

Declared fields: `type`, `table_name`, `column_name`, `sql`.

### `SeedRecord`

```text
SeedRecord(version: str, description: str, filename: str, seed_type: str, applied_at: datetime.datetime, checksum: str | None = None) -> None
```

Declared fields: `version`, `description`, `filename`, `seed_type`, `applied_at`, `checksum`.

### `SafetyIssue`

```text
SafetyIssue(severity: str, change_type: str, table_name: str, message: str, column_name: str | None = None, sql: str = '', required_flag: str | None = None) -> None
```

Declared fields: `severity`, `change_type`, `table_name`, `message`, `column_name`, `sql`, `required_flag`.

## `dbwarden.plugin`

### `HOOK_CALL_SPECS`

```text
HOOK_CALL_SPECS
```

### `KNOWN_VALUE_HOOKS`

```text
KNOWN_VALUE_HOOKS
```

### `MULTI_VALUE_HOOKS`

```text
MULTI_VALUE_HOOKS
```

### `PLUGIN_API_ATTR`

```text
PLUGIN_API_ATTR
```

### `PLUGIN_API_VERSION`

```text
PLUGIN_API_VERSION
```

### `PLUGIN_CONFIG_KEY_OWNERS`

```text
PLUGIN_CONFIG_KEY_OWNERS
```

### `PLUGIN_GROUP`

```text
PLUGIN_GROUP
```

### `ConfigKeyRegistry`

```text
ConfigKeyRegistry()
```

Declared fields: .

Methods declared on this class:

```text
register(key: 'str', *, plugin: 'str') -> 'None'
is_registered(key: 'str') -> 'bool'
owner(key: 'str') -> 'str | None'
keys() -> 'tuple[str, ...]'
reset() -> 'None'
```

### `HookConflictError`

Re-export of `dbwarden.exceptions.plugin.HookConflictError`.

### `HookNotRegisteredError`

Re-export of `dbwarden.exceptions.plugin.HookNotRegisteredError`.

### `HookRegistry`

```text
HookRegistry()
```

Declared fields: .

Methods declared on this class:

```text
register(hook_name: 'str', fn: 'Callable[..., Any]', *, plugin: 'str') -> 'None'
execute_single(hook_name: 'str', *args: 'Any', **kwargs: 'Any') -> 'Any'
execute_all(hook_name: 'str', *args: 'Any', **kwargs: 'Any') -> 'list[Any]'
is_registered(hook_name: 'str') -> 'bool'
providers(hook_name: 'str') -> 'list[str]'
hooks() -> 'dict[str, list[tuple[str, Callable[..., Any]]]]'
clear() -> 'None'
```

### `ObjectHandlerConflictError`

Re-export of `dbwarden.exceptions.plugin.ObjectHandlerConflictError`.

### `ObjectHandlerRegistration`

```text
ObjectHandlerRegistration(plugin: 'str', handler: 'Any') -> None
```

Declared fields: `plugin`, `handler`.

### `ObjectPluginRegistry`

```text
ObjectPluginRegistry()
```

Declared fields: .

Methods declared on this class:

```text
register_category(name: 'str', *, order: 'int', plugin: 'str') -> 'None'
categories() -> 'dict[str, dict[str, Any]]'
register(handler: 'Any', *, plugin: 'str') -> 'None'
handlers() -> 'dict[str, ObjectHandlerRegistration]'
has_handler(object_type: 'str') -> 'bool'
clear() -> 'None'
```

### `PluginApiMismatchError`

Re-export of `dbwarden.exceptions.plugin.PluginApiMismatchError`.

### `PluginRegistrar`

```text
PluginRegistrar(plugin_name: 'str') -> 'None'
```

Methods declared on this class:

```text
register(self, hook_name: 'str', fn: 'Callable[..., Any]') -> 'None'
register_object_handler(self, handler: 'Any') -> 'None'
register_migration_category(self, name: 'str', *, order: 'int') -> 'None'
register_config_key(self, *keys: 'str') -> 'None'
```

## `dbwarden.repositories`

### `create_migrations_table_if_not_exists`

```text
create_migrations_table_if_not_exists(db_name: str | None = None) -> None
```

### `fetch_latest_versioned_migration`

```text
fetch_latest_versioned_migration(db_name: str | None = None) -> Optional[dbwarden.models.MigrationRecord]
```

### `get_existing_runs_always_filenames`

```text
get_existing_runs_always_filenames(db_name: str | None = None) -> set[str]
```

### `get_existing_runs_on_change_filenames_to_checksums`

```text
get_existing_runs_on_change_filenames_to_checksums(db_name: str | None = None) -> dict[str, str]
```

### `get_latest_versions`

```text
get_latest_versions(db_name: str | None = None, limit: int | None = None, starting_version: str | None = None) -> list[str]
```

### `get_migration_records`

```text
get_migration_records(db_name: str | None = None) -> list[dbwarden.models.MigrationRecord]
```

### `get_migrated_versions`

```text
get_migrated_versions(db_name: str | None = None) -> list[str]
```

### `migrations_table_exists`

```text
migrations_table_exists(db_name: str | None = None) -> bool
```

### `run_migration`

```text
run_migration(sql_statements: list[str], version: Optional[str], migration_operation: str, filename: str, migration_type: str = 'versioned', db_name: str | None = None, perf: bool = False, connection: typing.Any | None = None, namespace: str = 'default', fencing_token: int = 0, progress_callback: typing.Any | None = None, migration_path: str | None = None, reapply_data: bool = False) -> None
```

### `run_repeatable_migration`

```text
run_repeatable_migration(sql_statements: list[str], filename: str, migration_type: str, db_name: str | None = None, perf: bool = False, connection: typing.Any | None = None) -> None
```

### `acquire_lock`

```text
acquire_lock(db_name: 'str | None' = None, *, namespace: 'str | None' = None, migration_version: 'str | None' = None, migration_checksum: 'str | None' = None) -> 'LockAcquisition'
```

### `check_lock`

```text
check_lock(db_name: 'str | None' = None, *, namespace: 'str' = 'default') -> 'bool'
```

### `create_lock_table_if_not_exists`

```text
create_lock_table_if_not_exists(db_name: 'str | None' = None) -> 'None'
```

### `force_release_lock`

```text
force_release_lock(db_name: 'str | None' = None, *, namespace: 'str' = 'default') -> 'bool'
```

### `release_lock`

```text
release_lock(db_name: 'str | None' = None, *, namespace: 'str' = 'default', strategy: 'Any' = None) -> 'bool'
```

## `dbwarden.schema`

### `DBWardenSeed`

```text
DBWardenSeed(database: 'str', description: 'str', on_conflict: 'str' = 'ignore', conflict_columns: 'list[str] | None' = None, source_hash: 'str' = '', version: 'str' = '', **kwargs: 'Any') -> 'None'
```

Declared fields: `database`, `description`, `on_conflict`, `conflict_columns`, `source_hash`.

## `dbwarden.schema.table_meta`

### `TableMeta`

Re-export of `dbwarden.schema.table_meta.TableMeta`.

### `PGTableMeta`

Re-export of `dbwarden.schema.table_meta.PGTableMeta`.

### `PGColumnMeta`

Re-export of `dbwarden.schema.table_meta.PGColumnMeta`.

### `PGViewMeta`

Re-export of `dbwarden.schema.table_meta.PGViewMeta`.

### `CHViewMeta`

Re-export of `dbwarden.schema.table_meta.CHViewMeta`.

### `CHTableMeta`

Re-export of `dbwarden.schema.table_meta.CHTableMeta`.

### `CHColumnMeta`

Re-export of `dbwarden.schema.table_meta.CHColumnMeta`.

### `MyTableMeta`

Re-export of `dbwarden.schema.table_meta.MyTableMeta`.

### `MyColumnMeta`

Re-export of `dbwarden.schema.table_meta.MyColumnMeta`.

### `MdbTableMeta`

Re-export of `dbwarden.schema.table_meta.MdbTableMeta`.

### `MdbColumnMeta`

Re-export of `dbwarden.schema.table_meta.MdbColumnMeta`.

### `SqTableMeta`

Re-export of `dbwarden.schema.table_meta.SqTableMeta`.

### `SqColumnMeta`

Re-export of `dbwarden.schema.table_meta.SqColumnMeta`.
