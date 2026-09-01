# ON CLUSTER

ClickHouse supports distributed DDL via the `ON CLUSTER` clause. When set, every DDL statement is automatically propagated to all nodes in the cluster. dbwarden injects this clause for you; no manual SQL editing required.

## Configuration

### Class-based (DbwardenDatabase)

Set `ch_cluster` on your database class:

```python
from dbwarden import DbwardenDatabase

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    default = True
    database_type = "clickhouse"
    database_url_sync = "clickhouse://default:@clickhouse1:8123/analytics"
    model_paths = ["app/analytics_models"]
    ch_cluster = "production_cluster"
```

### Function-based (database_config)

```python
from dbwarden import database_config

database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="clickhouse://default:@clickhouse1:8123/analytics",
    model_paths=["app/analytics_models"],
    ch_cluster="production_cluster",
)
```

### Config keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `ch_cluster` | `str \| None` | `None` | Cluster name. When set, `ON CLUSTER '<name>'` is appended to every DDL statement. |
| `ch_replicated_database` | `bool` | `False` | Use `Replicated` database engine. DDL propagates automatically via ZooKeeper; `ON CLUSTER` must be omitted. |

These are mutually exclusive. Setting both raises `ConfigurationError`.

## Three cluster modes

dbwarden supports three mutually exclusive cluster modes, resolved from the config via `ClusterContext.from_config()`:

| Mode | Config | DDL behavior | Use case |
|------|--------|--------------|----------|
| **NONE** | (default) | Bare DDL, no cluster clause | Single-node development |
| **ON_CLUSTER** | `ch_cluster = "name"` | Appends `ON CLUSTER '<name>'` to every DDL statement | Multi-node with explicit DDL distribution |
| **REPLICATED** | `ch_replicated_database = True` | DDL propagates automatically via ZooKeeper; `ON CLUSTER` must be omitted | Full replication with automatic DDL sync |

The modes are defined by the `ClusterMode` enum:

```python
class ClusterMode(Enum):
    NONE = "none"
    ON_CLUSTER = "on_cluster"
    REPLICATED = "replicated"
```

### Mutual exclusivity

`ch_cluster` and `ch_replicated_database` are mutually exclusive. Setting both raises a `ConfigurationError`:

```python
# WRONG (raises ConfigurationError)
ch_cluster = "my_cluster"
ch_replicated_database = True

# CORRECT: ON CLUSTER mode
ch_cluster = "my_cluster"
ch_replicated_database = False

# CORRECT: Replicated database mode
ch_cluster = None
ch_replicated_database = True
```

### ClusterContext

dbwarden resolves the cluster mode once per migration run into an immutable `ClusterContext`:

```python
@dataclass(frozen=True)
class ClusterContext:
    mode: ClusterMode
    cluster_name: str | None = None
```

Validation rules:

- `ON_CLUSTER` mode requires a non-empty `cluster_name`. Raises `ValueError` otherwise.
- Non-`ON_CLUSTER` modes reject any `cluster_name`. Raises `ValueError` if one is provided.

Handlers never construct or mutate this context. It is created once, passed read-only into the emit layer, and used to decorate every DDL statement.

## How DDL injection works

### The ClusterableStatement mechanism

dbwarden wraps each DDL statement in a `ClusterableStatement`, a dataclass that splits the SQL at the object name boundary:

```python
@dataclass
class ClusterableStatement:
    prefix: str      # SQL up to the ON CLUSTER insertion point
    suffix: str      # SQL after the insertion point
    supports_cluster: bool = True
```

When `render()` is called with a `ClusterContext`, it inserts `ON CLUSTER '<name>'` between prefix and suffix:

```sql
-- prefix:  CREATE TABLE db.events
-- suffix:  (id Int32, ...) ENGINE = MergeTree() ORDER BY id
-- result:  CREATE TABLE db.events ON CLUSTER 'production_cluster' (id Int32, ...) ENGINE = MergeTree() ORDER BY id
```

The `supports_cluster` flag allows specific statements to opt out of ON CLUSTER injection. When `False`, the statement passes through untouched regardless of cluster mode.

### The parsing regex

`ClusterableStatement.from_sql()` parses raw DDL using a regex that identifies the DDL verb and object name, then splits at the boundary:

```
\b(
  CREATE\s+(TABLE|MATERIALIZED\s+VIEW|DICTIONARY)
  (?:\s+IF\s+NOT\s+EXISTS)?
  |
  ALTER\s+TABLE
  |
  RENAME\s+TABLE
  |
  (?:DETACH|ATTACH)\s+TABLE
  |
  DROP\s+(TABLE|DICTIONARY)(?:\s+IF\s+EXISTS)?
)\s+(<qualified_identifier>)
```

The `<qualified_identifier>` pattern matches bare identifiers, backtick-quoted identifiers (with escape handling), and dot-qualified names like `database.table`:

```
identifier = `(?:`(?:``|[^`])+`|[a-zA-Z_][a-zA-Z0-9_]*)`
qualified_identifier = `identifier(?:\.identifier)*`
```

### Supported DDL types

| DDL type | Insertion point | Example |
|----------|----------------|---------|
| CREATE TABLE | After object name | `CREATE TABLE events ON CLUSTER 'c' (...)` |
| CREATE VIEW | After object name | `CREATE VIEW v ON CLUSTER 'c' AS ...` |
| CREATE MATERIALIZED VIEW | After object name | `CREATE MATERIALIZED VIEW mv ON CLUSTER 'c' TO ...` |
| CREATE DICTIONARY | After object name | `CREATE DICTIONARY d ON CLUSTER 'c' (...)` |
| ALTER TABLE | After object name | `ALTER TABLE events ON CLUSTER 'c' ADD COLUMN ...` |
| RENAME TABLE | After first object name | `RENAME TABLE events ON CLUSTER 'c' TO events_v2` |
| DETACH TABLE | After object name | `DETACH TABLE events ON CLUSTER 'c'` |
| ATTACH TABLE | After object name | `ATTACH TABLE events ON CLUSTER 'c'` |
| DROP TABLE | After object name | `DROP TABLE IF EXISTS events ON CLUSTER 'c'` |
| DROP DICTIONARY | After object name | `DROP DICTIONARY IF EXISTS d ON CLUSTER 'c'` |

### Fallback behavior

If the regex does not match (e.g., non-DDL statements like `INSERT INTO`), the entire string becomes the prefix with an empty suffix, and `ON CLUSTER` is appended at the end. In practice, the recreate pipeline filters non-DDL through a separate check before calling `from_sql`, so this fallback is not exercised for `INSERT` statements.

### Cluster name quoting

dbwarden quotes cluster names as ClickHouse string literals. Single quotes in the name are escaped:

```python
ch_cluster = "my-cluster-v2"  # → ON CLUSTER 'my-cluster-v2'
```

### No double spaces

The `render()` method checks whether the suffix already starts with whitespace before inserting a space separator. The output is `.strip()`'d to prevent trailing whitespace.

## Which handlers use ON CLUSTER

All ClickHouse-specific handler classes are decorated with `@emit_with_cluster`, which extracts the cluster context from the migration pipeline and stores it on the handler instance:

| Handler | Object type | Op types |
|---------|-------------|----------|
| `ChTableHandler` | Tables | `alter_ch_options`, `recreate_ch_table` |
| `ChMaterializedViewHandler` | Materialized views | `modify_mv_query`, `modify_mv_refresh` |
| `ChDictionaryHandler` | Dictionaries | `alter_ch_dict` |
| `ChProjectionHandler` | Projections | `alter_ch_projection` |
| `ChSkipIndexHandler` | Skip indexes | `alter_ch_skip_index` |
| `ChColumnHandler` | Column changes | `alter_ch_column` |
| `ChCommentHandler` | Comment changes | `alter_ch_comment` |
| `ChAggTargetHandler` | Aggregating view targets | `create_ch_agg_target`, `drop_ch_agg_target` |

Data operations (`ChDataOpHandler`) are **not** cluster-decorated. They are user-authored raw SQL and pass through unchanged.

## How cluster context flows through the pipeline

The complete flow from config to rendered SQL:

1. **Config**: `ch_cluster` / `ch_replicated_database` are set on `DatabaseEntry` or `DbwardenDatabase`
2. **Context creation**: `ClusterContext.from_config(entry)` is called once per migration run
3. **RegistryDriver.emit_all**: Passes `cluster_ctx` to every handler's `emit()` call
4. **emit_with_cluster decorator**: Pops `cluster_ctx` from kwargs, stores as `self._cluster_ctx` on the handler. Falls back to `NONE` mode if not provided.
5. **Handler emit methods**: Build `ClusterableStatement` objects and call `.render(self._cluster_ctx)` to produce the final SQL
6. **Migration output**: Both upgrade and rollback SQL contain the ON CLUSTER clause

## Rollback behavior

Every handler constructs rollback SQL the same way as upgrade SQL. The `ClusterableStatement.to_migration()` method renders both:

```python
def to_migration(self, order, ctx, rollback=None):
    forward_sql = self.render(ctx)
    if isinstance(rollback, ClusterableStatement):
        rollback_sql = rollback.render(ctx)   # cluster applied to rollback too
    elif isinstance(rollback, str):
        rollback_sql = rollback               # raw string, no cluster injection
    else:
        rollback_sql = f"-- no-op ..."
```

When rollback is a `ClusterableStatement`, it receives the same ON CLUSTER treatment. When it is a raw string, it is used verbatim.

### Example migration

With `ch_cluster = "production_cluster"`:

```sql
-- upgrade

CREATE TABLE IF NOT EXISTS events ON CLUSTER 'production_cluster' (
    id Int32,
    event_date Date,
    amount Float64
) ENGINE = MergeTree()
ORDER BY (event_date, id)
PARTITION BY toYYYYMM(event_date);

-- rollback

DROP TABLE IF EXISTS events ON CLUSTER 'production_cluster';
```

Without `ch_cluster`:

```sql
-- upgrade

CREATE TABLE IF NOT EXISTS events (
    id Int32,
    event_date Date,
    amount Float64
) ENGINE = MergeTree()
ORDER BY (event_date, id)
PARTITION BY toYYYYMM(event_date);

-- rollback

DROP TABLE IF EXISTS events;
```

## ON CLUSTER and the recreate pipeline

When a table change requires a full recreate (engine change, ORDER BY change, etc.), the recreate pipeline uses ON CLUSTER on every step. The sequence:

### Upgrade path

```sql
-- 1. Detach dependent MVs
DETACH TABLE event_daily_mv ON CLUSTER 'production_cluster';

-- 2. Create new table
CREATE TABLE events_new ON CLUSTER 'production_cluster' (...) ENGINE = MergeTree() ORDER BY ...;

-- 3. Insert data (NOT cluster-decorated; this is DML, not DDL)
INSERT INTO events_new SELECT * FROM events;

-- 4. Swap
RENAME TABLE events ON CLUSTER 'production_cluster' TO events__dbw_old, events_new ON CLUSTER 'production_cluster' TO events;

-- 5. Drop old
DROP TABLE IF EXISTS events__dbw_old ON CLUSTER 'production_cluster';

-- 6. Reattach MVs
ATTACH TABLE event_daily_mv ON CLUSTER 'production_cluster';
```

### Key detail: INSERT is not decorated

The recreate pipeline uses `_cluster_sql()` which checks if the SQL starts with a DDL verb (`CREATE|ALTER|DROP|RENAME|DETACH|ATTACH`). `INSERT INTO` does not match, so it passes through without ON CLUSTER. This is correct; `INSERT` is DML, not DDL, and ClickHouse does not support `ON CLUSTER` for DML.

### RENAME TABLE handling

For multi-pair renames like `RENAME TABLE a TO b, c TO d`, `from_sql` splits after the first qualified identifier:

```
Input:  RENAME TABLE events TO events__dbw_new, events__dbw_old TO events
Prefix: RENAME TABLE events
Suffix:  TO events__dbw_new, events__dbw_old TO events
Result: RENAME TABLE events ON CLUSTER 'c' TO events__dbw_new, events__dbw_old TO events
```

## Replicated databases

When `ch_replicated_database = True`, ClickHouse uses the `Replicated` database engine. DDL propagates automatically through ZooKeeper, so `ON CLUSTER` is omitted.

```python
class Analytics(DbwardenDatabase):
    database_type = "clickhouse"
    database_url_sync = "clickhouse://default:@clickhouse1:8123/analytics"
    ch_replicated_database = True
```

With replicated databases, you should use `Replicated*` engine variants in your models:

```python
from dbwarden.databases.clickhouse import replicated_merge_tree

class Event(Base):
    __tablename__ = "events"

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=replicated_merge_tree("/zk/pv", "{replica}"),
            order_by=["event_date", "id"],
        )
```

### Why ON CLUSTER is omitted in replicated mode

In a replicated database, ClickHouse handles DDL distribution through ZooKeeper. Adding `ON CLUSTER` would be redundant and can cause conflicts. The `ClusterContext` in `REPLICATED` mode never injects the clause; `render()` only adds `ON CLUSTER` when `mode is ClusterMode.ON_CLUSTER`.

## ON CLUSTER vs Replicated: when to use which

| Consideration | ON CLUSTER | Replicated database |
|---------------|------------|---------------------|
| DDL propagation | Explicit `ON CLUSTER` clause | Automatic via ZooKeeper |
| Engine choice | Any engine | Must use `Replicated*` variants |
| ZooKeeper dependency | Not required for DDL | Required for DDL propagation |
| Setup complexity | Lower | Higher |
| Rollback support | Full (every step is cluster-decorated) | Full (ZooKeeper propagates rollback DDL too) |
| Best for | Mixed deployments, selective clustering, existing non-replicated tables | New deployments with full replication from day one |

## Cluster name quoting

dbwarden quotes cluster names as ClickHouse string literals. Single quotes are escaped:

```python
ch_cluster = "my-cluster-v2"     # → ON CLUSTER 'my-cluster-v2'
ch_cluster = "cluster's_name"    # → ON CLUSTER 'cluster\'s_name'
```

## Diagnostics

### Checking your cluster config

```python
# Class-based
print(Analytics.ch_cluster)           # None or cluster name
print(Analytics.ch_replicated_database)  # True or False

# From a DatabaseEntry
from dbwarden.config_registry import registered_entries
for entry in registered_entries():
    print(f"{entry.database_name}: ch_cluster={entry.ch_cluster}, replicated={entry.ch_replicated_database}")
```

### Verifying ON CLUSTER appears in migrations

```bash
# Generate a migration and check for ON CLUSTER
dbwarden make-migrations --plan -d analytics | grep "ON CLUSTER"
```

### Inspecting the ClusterContext

The `ClusterContext` is resolved once per migration run. To see what mode was resolved:

```python
from dbwarden.databases.clickhouse.cluster import ClusterContext, ClusterMode

ctx = ClusterContext.from_config(entry)
print(ctx.mode)         # ClusterMode.ON_CLUSTER
print(ctx.cluster_name) # "production_cluster"
```

## Troubleshooting

### ON CLUSTER not appearing in migrations

1. Verify `ch_cluster` is set on your database class or config:
   ```python
   print(Analytics.ch_cluster)  # Should print the cluster name
   ```

2. Check that `ch_replicated_database` is not `True` (they are mutually exclusive)

3. Ensure the handler for your object type supports ON CLUSTER (see Which handlers use ON CLUSTER above)

### ConfigurationError: mutually exclusive

You've set both `ch_cluster` and `ch_replicated_database`. Pick one:

```python
# ON CLUSTER mode
ch_cluster = "production_cluster"
ch_replicated_database = False

# Replicated database mode
ch_cluster = None
ch_replicated_database = True
```

### Replication lag on DDL

ON CLUSTER DDL is fire-and-forget from ClickHouse's perspective; it sends the statement to all nodes but does not wait for completion. If you see DDL applied on some nodes but not others, check the ClickHouse server logs for errors on the lagging nodes.

### Cluster name not found

ClickHouse will error with `Cluster ... not found` if the cluster name in `ch_cluster` does not match a cluster configured in the ClickHouse server's `config.xml` or `remote_servers.xml`. Verify the cluster name:

```sql
SELECT * FROM system.clusters WHERE cluster = 'production_cluster';
```

### RENAME TABLE fails with ON CLUSTER

Multi-pair `RENAME TABLE` with ON CLUSTER requires all tables to exist on the same cluster. If one of the tables in the rename pair does not exist on all nodes, the operation will fail on some nodes. Ensure the table exists on all cluster nodes before renaming.

### Non-idempotent statements with ON CLUSTER

Under coordination profile CH-1, non-idempotent statements like `RENAME` are refused. This is independent of ON CLUSTER; the idempotency check runs before DDL injection. Use `--allow-non-idempotent` to override, or restructure the migration to use idempotent forms.

## See also

- [Migration Locking](../../advanced/clickhouse-locking.md): Coordination profiles (CH-0 through CH-4), idempotency enforcement, and production lock strategies
- [Immutability](immutability.md): What can never change, and what forces a table recreate
- [Safety Classification](safety.md): Change classification levels and `--force`
- [Config Reference](config-reference.md): All `DbwardenDatabase` / `database_config()` parameters
