# ALTER semantics

ClickHouse ALTERs behave differently from every other database dbwarden supports. Understanding these differences prevents production failures that are hard to diagnose.

## Why ClickHouse ALTERs are special

Unlike PostgreSQL or MySQL, ClickHouse ALTERs have three properties that affect how migrations must be structured:

1. **Non-transactional DDL.** DDL statements (CREATE, ALTER, DROP) cannot be rolled back. If a migration fails midway, there is no automatic undo. dbwarden's recreate pipeline handles this for critical changes, but metadata-only ALTERs are still individual commits.

2. **ZooKeeper-based replication.** On replicated tables, ALTER statements add instructions to ZooKeeper. Replicas pick them up asynchronously. The ALTER statement returns before replicas have applied the change.

3. **Async mutations.** Data-mutating ALTERs (UPDATE, DELETE, MATERIALIZE INDEX) are background processes that rewrite entire data parts. They are not instant and do not block inserts.

These properties mean that the *shape* of your ALTER statements matters. Two separate `ALTER TABLE` statements are not equivalent to one combined statement, even if the SQL looks equivalent.

## CANNOT_ASSIGN_ALTER (Code 517)

### What it is

On ReplicatedMergeTree tables, ClickHouse rejects concurrent ALTER mutations on the same table with:

```
Code: 517. DB::Exception: CANNOT_ASSIGN_ALTER
```

The error message says: "Metadata on replica is not up to date with common metadata in Zookeeper. Cannot alter."

### When it fires

The race window is between two separate `ALTER TABLE` statements against the same table:

```sql
-- Statement 1: reaches ClickHouse, begins processing
ALTER TABLE events ADD INDEX IF NOT EXISTS ix_a col_a TYPE minmax GRANULARITY 100;

-- Statement 2: arrives before Statement 1's metadata has been applied to all replicas
ALTER TABLE events ADD INDEX IF NOT EXISTS ix_b col_b TYPE minmax GRANULARITY 100;
-- ^ Code: 517 CANNOT_ASSIGN_ALTER
```

Even with `mutations_sync=2` or `alter_sync=2`, the next HTTP request can arrive before the previous ALTER has cleared the mutation slot. The ClickHouse docs state:

> On replicated tables, submitting several separate `ALTER` statements against the same table in quick succession can fail with `CANNOT_ASSIGN_ALTER` (code 517). The replicated path raises this when the replica has not yet applied some previous `ALTER`s.

### How to avoid it

**Combine compatible subcommands into a single ALTER TABLE statement:**

```sql
-- Wrong: two separate statements, race-prone
ALTER TABLE events ADD INDEX IF NOT EXISTS ix_a col_a TYPE minmax GRANULARITY 100;
ALTER TABLE events ADD INDEX IF NOT EXISTS ix_b col_b TYPE minmax GRANULARITY 100;

-- Right: one combined statement, no race
ALTER TABLE events
    ADD INDEX IF NOT EXISTS ix_a col_a TYPE minmax GRANULARITY 100,
    ADD INDEX IF NOT EXISTS ix_b col_b TYPE minmax GRANULARITY 100;
```

For operations that cannot be combined (see [ALTER batching](#alter-batching) below), serialize them and retry on Code 517 until the previous ALTER has been applied.

### How dbwarden handles it

dbwarden's handlers batch most ALTER subcommands into a single `MigrationStatement`. However, there is a known limitation: **`MODIFY SETTING` is emitted as one statement per setting change** (see [MODIFY SETTING](#modify-setting)). On replicated tables with multiple setting changes, this can trigger Code 517.

See the [operation catalog](#operation-catalog) for per-handler batching behavior.

## mutations_sync vs alter_sync

ClickHouse has **two distinct settings** that control ALTER synchronicity. They apply to different categories of operations.

### Which setting applies to which operations

| Category | Setting | Operations |
|----------|---------|------------|
| **Metadata-only** | `alter_sync` | ADD/DROP/MODIFY COLUMN, ADD/DROP INDEX, ADD/DROP PROJECTION, MODIFY SETTING, MODIFY TTL, REMOVE TTL, MODIFY ORDER BY, COMMENT, RENAME TABLE |
| **Data mutations** | `mutations_sync` | UPDATE, DELETE, MATERIALIZE INDEX, MATERIALIZE PROJECTION, MATERIALIZE COLUMN, APPLY DELETED MASK, APPLY PATCHES, CLEAR STATISTIC, MATERIALIZE STATISTIC |

The distinction matters because:
- Metadata ALTERs propagate via ZooKeeper metadata entries (fast, small)
- Mutations rewrite data parts (slow, heavy I/O)

### Setting values

| Value | alter_sync | mutations_sync |
|-------|-----------|----------------|
| 0 | Do not wait | Do not wait |
| 1 | Wait for own execution | Wait for own execution |
| 2 | Wait for all replicas | Wait for all replicas |
| 3 | Wait for active replicas | **Bug: see below** |

Both settings apply only to Replicated and SharedMergeTree tables. Non-replicated tables always execute synchronously.

### Default behavior

- `alter_sync` defaults to `0` (do not wait) in ClickHouse Cloud, `2` in self-hosted
- `mutations_sync` defaults to `0` (do not wait)

For migrations, the safe default is `alter_sync=2` and `mutations_sync=2` when you need confirmation that changes have propagated.

### Setting them in dbwarden

dbwarden does not set these automatically. You control them through `data_op()`:

```python
from dbwarden.databases.clickhouse import data_op

# Metadata ALTER with wait
data_op("ALTER TABLE events ADD INDEX ix_a col_a TYPE minmax GRANULARITY 100 SETTINGS alter_sync = 2")

# Mutation with wait
data_op("ALTER TABLE events DELETE WHERE event_date < '2020-01-01' SETTINGS mutations_sync = 2")
```

Or at the session level before running `dbwarden migrate`:

```sql
SET mutations_sync = 2;
SET alter_sync = 2;
```

## The mutations_sync=3 bug

!!! warning "Do not use mutations_sync=3 on ReplicatedMergeTree"

    On ReplicatedMergeTree, `mutations_sync=3` and `alter_sync=3` **silently do nothing**; the statement returns immediately without waiting, behaving like value `0`.

### What happens

The documented contract for value `3` is:

> The query waits only for active replicas. Supported only for SharedMergeTree. For ReplicatedMergeTree it behaves the same as mutations_sync = 2.

But in the C++ implementation, `StorageReplicatedMergeTree::waitMutation` only branches on `== 1` and `== 2`. Value `3` falls through both branches, leaving the replica list empty, and `waitMutationToFinishOnReplicas` returns immediately on an empty list.

### Impact

A user who sets `mutations_sync=3` gets no error and no wait; the statement returns as if it succeeded synchronously, and scripts proceed on the assumption that the mutation has been applied. This is a silent loss of a synchronization guarantee.

### Safe values

On ReplicatedMergeTree, use only `0` or `2`:

```sql
-- Safe
ALTER TABLE t DELETE WHERE ... SETTINGS mutations_sync = 2;

-- Unsafe (silently does nothing on ReplicatedMergeTree)
ALTER TABLE t DELETE WHERE ... SETTINGS mutations_sync = 3;
```

Value `3` is only safe on SharedMergeTree.

See [ClickHouse issue #117395](https://github.com/ClickHouse/clickHouse/issues/117395) for tracking.

## MODIFY SETTING

### Batching requirement

Multiple settings should be combined into a **single** `ALTER TABLE ... MODIFY SETTING` statement:

```sql
-- Wrong: two separate statements, race-prone on ReplicatedMergeTree
ALTER TABLE events MODIFY SETTING index_granularity = 4096;
ALTER TABLE events MODIFY SETTING max_bytes_to_merge_at_max_space_in_pool = 10000000000;

-- Right: one combined statement
ALTER TABLE events MODIFY SETTING
    index_granularity = 4096,
    max_bytes_to_merge_at_max_space_in_pool = 10000000000;
```

### dbwarden limitation

dbwarden currently emits **one `MODIFY SETTING` per changed setting** in `ch_table_handler.py`:

```python
for setting_key, setting_value in to_val.items():
    suffix = f"MODIFY SETTING {setting_key} = {setting_value}"
    target.append(ClusterableStatement(prefix, suffix))
```

On non-replicated tables this works fine. On ReplicatedMergeTree, multiple setting changes in the same migration can trigger Code 517.

### Workaround

Until dbwarden batches MODIFY SETTING into a single statement, you can manually combine them in a `data_op()`:

```python
data_op("ALTER TABLE events MODIFY SETTING index_granularity = 4096, max_bytes_to_merge_at_max_space_in_pool = 10000000000")
```

## ALTER batching

### What can be combined

Compatible metadata-only ALTER subcommands can appear in a single `ALTER TABLE` statement. ClickHouse processes them atomically (single ZooKeeper coordination round, no Code 517 race):

| Subcommands | Example |
|-------------|---------|
| ADD INDEX + ADD INDEX | `ALTER TABLE t ADD INDEX ix_a ..., ADD INDEX ix_b ...` |
| ADD/DROP INDEX mix | `ALTER TABLE t ADD INDEX ix_a ..., DROP INDEX ix_old` |
| ADD PROJECTION + ADD PROJECTION | `ALTER TABLE t ADD PROJECTION p1 ..., ADD PROJECTION p2 ...` |
| ADD COLUMN + ADD INDEX | `ALTER TABLE t ADD COLUMN col Int64, ADD INDEX ix col TYPE minmax GRANULARITY 1` |
| MODIFY SETTING (multiple) | `ALTER TABLE t MODIFY SETTING k1 = v1, k2 = v2` |
| COMMENT + MODIFY COLUMN COMMENT | `ALTER TABLE t COMMENT 'table comment', MODIFY COLUMN c COMMENT 'col comment'` |

### What cannot be combined

Some operations must be in separate statements:

| Restriction | Reason |
|-------------|--------|
| MATERIALIZE INDEX + ADD INDEX | DatabaseReplicated rejects mixed AlterCommand/MutationCommand segments with `QUERY_IS_PROHIBITED` |
| UPDATE/DELETE + any metadata ALTER | Mutations are async background processes; metadata ALTERs are synchronous ZooKeeper operations |
| MODIFY SETTING + MATERIALIZE INDEX | Mixes metadata and mutation in one statement |
| DROP TABLE + CREATE TABLE (same name) | Requires atomic swap, not combinable in one ALTER |

When operations cannot be combined, serialize them and ensure each completes before the next begins (via `mutations_sync=2`, `alter_sync=2`, or polling `system.mutations`).

### dbwarden's batching behavior

| Handler | Batches? | Separator | Notes |
|---------|----------|-----------|-------|
| ChColumnHandler | Yes | `";\n"` | Multiple MODIFY COLUMN for same column joined |
| ChTableHandler | Partially | `"\n"` | MODIFY SETTING emitted per-setting (see limitation above) |
| ChSkipIndexHandler | Yes | `"\n"` | ADD/DROP INDEX batched |
| ChProjectionHandler | Yes | `"\n"` | ADD/DROP PROJECTION batched |
| ChCommentHandler | Yes | `"\n"` | Table + column comments batched |
| ChDictionaryHandler | Yes | `"\n"` | MODIFY LAYOUT/SOURCE/LIFETIME/PRIMARY KEY batched |
| ChMVHandler | No | N/A | One op per statement (MODIFY QUERY, MODIFY REFRESH) |
| ChDataOpHandler | No | N/A | User provides raw SQL |

## Operation catalog

Complete reference of every ALTER subcommand dbwarden generates, its sync category, batching behavior, and safety classification.

### Table-level operations

| Operation | Handler | Sync setting | Batched? | Safety | Example |
|-----------|---------|-------------|----------|--------|---------|
| MODIFY SETTING | ChTableHandler | alter_sync | **No** (per-setting) | INFO | `ALTER TABLE t MODIFY SETTING k = v` |
| MODIFY TTL | ChTableHandler | alter_sync | Yes | INFO | `ALTER TABLE t MODIFY TTL expr` |
| REMOVE TTL | ChTableHandler | alter_sync | Yes | INFO | `ALTER TABLE t REMOVE TTL` |
| MODIFY ORDER BY | ChTableHandler | alter_sync | Yes | CRITICAL | `ALTER TABLE t MODIFY ORDER BY (col)` |
| COMMENT | ChCommentHandler | alter_sync | Yes | INFO | `ALTER TABLE t COMMENT 'text'` |
| Recreate (swap) | ChTableHandler | N/A (DDL) | N/A | CRITICAL | CREATE new → INSERT → RENAME → DROP old |

### Column operations

| Operation | Handler | Sync setting | Batched? | Safety | Example |
|-----------|---------|-------------|----------|--------|---------|
| MODIFY COLUMN (type) | ChColumnHandler | alter_sync | Yes | WARN | `ALTER TABLE t MODIFY COLUMN c Int64` |
| MODIFY COLUMN (codec) | ChColumnHandler | alter_sync | Yes | INFO | `ALTER TABLE t MODIFY COLUMN c Int64 CODEC(ZSTD)` |
| MODIFY COLUMN (TTL) | ChColumnHandler | alter_sync | Yes | INFO | `ALTER TABLE t MODIFY COLUMN c Int64 TTL expr` |
| MODIFY COLUMN (default) | ChColumnHandler | alter_sync | Yes | INFO | `ALTER TABLE t MODIFY COLUMN c Int64 DEFAULT 0` |
| MODIFY COLUMN (materialized) | ChColumnHandler | alter_sync | Yes | INFO | `ALTER TABLE t MODIFY COLUMN c Int64 MATERIALIZED expr` |
| MODIFY COLUMN (alias) | ChColumnHandler | alter_sync | Yes | INFO | `ALTER TABLE t MODIFY COLUMN c Int64 ALIAS expr` |
| MODIFY COLUMN (ephemeral) | ChColumnHandler | alter_sync | Yes | INFO | `ALTER TABLE t MODIFY COLUMN c Int64 EPHEMERAL` |
| REMOVE CODEC | ChColumnHandler | alter_sync | Yes | INFO | `ALTER TABLE t MODIFY COLUMN c Int64 REMOVE CODEC` |
| REMOVE TTL | ChColumnHandler | alter_sync | Yes | INFO | `ALTER TABLE t MODIFY COLUMN c Int64 REMOVE TTL` |
| REMOVE DEFAULT | ChColumnHandler | alter_sync | Yes | INFO | `ALTER TABLE t MODIFY COLUMN c Int64 REMOVE DEFAULT` |
| REMOVE MATERIALIZED | ChColumnHandler | alter_sync | Yes | INFO | `ALTER TABLE t MODIFY COLUMN c Int64 REMOVE MATERIALIZED` |
| REMOVE ALIAS | ChColumnHandler | alter_sync | Yes | INFO | `ALTER TABLE t MODIFY COLUMN c Int64 REMOVE ALIAS` |
| REMOVE EPHEMERAL | ChColumnHandler | alter_sync | Yes | INFO | `ALTER TABLE t MODIFY COLUMN c Int64 REMOVE EPHEMERAL` |
| Column comment | ChCommentHandler | alter_sync | Yes | INFO | `ALTER TABLE t MODIFY COLUMN c COMMENT 'text'` |
| Column REMOVE COMMENT | ChCommentHandler | alter_sync | Yes | INFO | `ALTER TABLE t MODIFY COLUMN c REMOVE COMMENT` |

### Index operations

| Operation | Handler | Sync setting | Batched? | Safety | Example |
|-----------|---------|-------------|----------|--------|---------|
| ADD INDEX | ChSkipIndexHandler | alter_sync | Yes | INFO | `ALTER TABLE t ADD INDEX ix col TYPE minmax GRANULARITY 1` |
| DROP INDEX | ChSkipIndexHandler | alter_sync | Yes | WARN | `ALTER TABLE t DROP INDEX ix` |

### Projection operations

| Operation | Handler | Sync setting | Batched? | Safety | Example |
|-----------|---------|-------------|----------|--------|---------|
| ADD PROJECTION | ChProjectionHandler | alter_sync | Yes | INFO | `ALTER TABLE t ADD PROJECTION p (SELECT ...)` |
| DROP PROJECTION | ChProjectionHandler | alter_sync | Yes | WARN | `ALTER TABLE t DROP INDEX p` |

### MV operations

| Operation | Handler | Sync setting | Batched? | Safety | Example |
|-----------|---------|-------------|----------|--------|---------|
| MODIFY QUERY | ChMVHandler | alter_sync | No | CRITICAL | `ALTER TABLE mv MODIFY QUERY SELECT ...` |
| MODIFY REFRESH | ChMVHandler | alter_sync | No | CRITICAL | `ALTER TABLE mv MODIFY REFRESH EVERY 1 HOUR` |

### Dictionary operations

| Operation | Handler | Sync setting | Batched? | Safety | Example |
|-----------|---------|-------------|----------|--------|---------|
| MODIFY LAYOUT | ChDictionaryHandler | alter_sync | Yes | INFO | `ALTER DICTIONARY d MODIFY LAYOUT(...)` |
| MODIFY SOURCE | ChDictionaryHandler | alter_sync | Yes | INFO | `ALTER DICTIONARY d MODIFY SOURCE(...)` |
| MODIFY LIFETIME | ChDictionaryHandler | alter_sync | Yes | INFO | `ALTER DICTIONARY d MODIFY LIFETIME(...)` |
| MODIFY PRIMARY KEY | ChDictionaryHandler | alter_sync | Yes | INFO | `ALTER DICTIONARY d MODIFY PRIMARY KEY(...)` |

### Data operations (user-authored)

| Operation | Handler | Sync setting | Batched? | Safety | Example |
|-----------|---------|-------------|----------|--------|---------|
| DELETE | ChDataOpHandler | mutations_sync | N/A (user SQL) | WARN | `ALTER TABLE t DELETE WHERE ...` |
| UPDATE | ChDataOpHandler | mutations_sync | N/A (user SQL) | WARN | `ALTER TABLE t UPDATE col = val WHERE ...` |
| MATERIALIZE INDEX | ChDataOpHandler | mutations_sync | N/A (user SQL) | INFO | `ALTER TABLE t MATERIALIZE INDEX ix` |
| MATERIALIZE PROJECTION | ChDataOpHandler | mutations_sync | N/A (user SQL) | INFO | `ALTER TABLE t MATERIALIZE PROJECTION p` |
| OPTIMIZE | ChDataOpHandler | N/A | N/A (user SQL) | INFO | `OPTIMIZE TABLE t FINAL` |
| Partition operations | ChDataOpHandler | N/A | N/A (user SQL) | varies | `ALTER TABLE t DROP PARTITION '2024-01'` |

## Related

- [Safety classification](safety.md): INFO/WARN/CRITICAL levels and the recreate pipeline
- [Migration locking](../../advanced/clickhouse-locking.md): CH-0 through CH-4 coordination profiles
- [Data operations](data-operations.md): non-DDL mutations and ad-hoc operations
- [ON Cluster](on-cluster.md): DDL propagation in clustered deployments
