# ClickHouse Migration Locking and ON CLUSTER

dbwarden provides production-grade migration locking for ClickHouse with support for single-node, ON CLUSTER, and replicated deployments. This guide covers setup, configuration, and best practices.

## Overview

ClickHouse has no session-scoped user locks, no synchronous compare-and-swap on table rows, and non-transactional DDL. dbwarden implements a coordination profile system to handle these constraints safely.

### Coordination Profiles

| Profile | Description | Grade | Use Case |
|---------|-------------|-------|----------|
| **CH-0** | Lease row with fencing token | C | Non-production, human-gated |
| **CH-1** | CH-0 + strict idempotency | C | Production with idempotent migrations |
| **CH-2** | CH-1 + Keeper-backed lock | C | Replicated with clickhouse-keeper |
| **CH-3** | Migration proxy choke point | A- | Production with migration proxy |
| **CH-4** | Singleton executor | A* | Kubernetes with dedicated executor |

## Quick Start

### Single-node setup

```python
from dbwarden import DbwardenDatabase

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    default = True
    database_type = "clickhouse"
    database_url_sync = "clickhouse://default:@localhost:8123/analytics"
    model_paths = ["app/analytics_models"]
```

### ON CLUSTER setup

```python
from dbwarden import DbwardenDatabase

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    default = True
    database_type = "clickhouse"
    database_url_sync = "clickhouse://default:@localhost:8123/analytics"
    model_paths = ["app/analytics_models"]
    ch_cluster = "production_cluster"  # Enable ON CLUSTER
```

### Replicated database setup

```python
from dbwarden import DbwardenDatabase

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    default = True
    database_type = "clickhouse"
    database_url_sync = "clickhouse://default:@localhost:8123/analytics"
    model_paths = ["app/analytics_models"]
    ch_replicated_database = True  # DDL propagates automatically
```

## ON CLUSTER Support

When `ch_cluster` is set, dbwarden appends `ON CLUSTER '<cluster_name>'` to every DDL statement:

```sql
-- Without ON CLUSTER
CREATE TABLE IF NOT EXISTS events (id Int32) ENGINE = MergeTree() ORDER BY id

-- With ON CLUSTER
CREATE TABLE IF NOT EXISTS events ON CLUSTER 'production_cluster' (id Int32) ENGINE = MergeTree() ORDER BY id
```

### How it works

1. **Configuration**: Set `ch_cluster = "cluster_name"` in your database config
2. **DDL injection**: dbwarden wraps every DDL statement with `ON CLUSTER`
3. **Replication**: ClickHouse propagates DDL to all cluster nodes automatically
4. **Rollback**: Rollback statements also include `ON CLUSTER`

### Supported DDL types

| DDL Type | ON CLUSTER Support |
|----------|-------------------|
| CREATE TABLE | ✓ |
| CREATE VIEW | ✓ |
| CREATE MATERIALIZED VIEW | ✓ |
| CREATE DICTIONARY | ✓ |
| ALTER TABLE | ✓ |
| RENAME TABLE | ✓ |
| DROP TABLE/VIEW/DICTIONARY | ✓ |
| DETACH/ATTACH TABLE | ✓ |

### Example migration

```sql
-- Generated migration file
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

## Replicated Databases

When `ch_replicated_database = True`, dbwarden uses the `Replicated` database engine. DDL propagates automatically through ZooKeeper, so `ON CLUSTER` must be omitted.

### Configuration

```python
class Analytics(DbwardenDatabase):
    database_type = "clickhouse"
    database_url_sync = "clickhouse://default:@localhost:8123/analytics"
    ch_replicated_database = True
```

### Behavior differences

| Aspect | ON CLUSTER | Replicated Database |
|--------|------------|---------------------|
| DDL propagation | Explicit `ON CLUSTER` | Automatic via ZooKeeper |
| Engine specification | User-specified | `Replicated*` variants required |
| Configuration | `ch_cluster = "name"` | `ch_replicated_database = True` |
| Mutually exclusive | ✓ | ✓ |

## Lock Strategy (CH-0/CH-1)

ClickHouse uses a lease-based lock with fencing tokens:

### How it works

1. **Lease acquisition**: Atomic conditional upsert to `dbwarden_lock` table
2. **Fencing token**: Monotonically increasing token prevents stale workers from mutating
3. **Heartbeat**: Background thread updates `last_heartbeat_at` every 15 seconds
4. **Lease expiry**: Lease expires after `ttl_seconds` (default 120s)

### Lease acquisition SQL

```sql
INSERT INTO dbwarden_lock
    (namespace, execution_id, owner_id, fencing_token, expires_at, ...)
SELECT ...
WHERE NOT EXISTS (
    SELECT 1 FROM dbwarden_lock FINAL
    WHERE namespace = :ns AND expires_at > now()
)
```

### CH-1 idempotency enforcement

Under CH-1, non-idempotent statements are refused:

**Idempotent** (allowed):
- `CREATE TABLE/VIEW/DICTIONARY IF NOT EXISTS`
- `DROP TABLE/VIEW/DICTIONARY IF EXISTS`
- `ALTER TABLE ADD COLUMN IF NOT EXISTS`
- `ALTER TABLE DROP COLUMN IF EXISTS`
- Metadata-only alters (COMMENT, TTL)

**Non-idempotent** (refused):
- `RENAME` (no idempotent form)
- `MODIFY COLUMN` (rewrites data)
- `UPDATE` / `DELETE` (data mutations)

### Configuration

```python
database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="clickhouse://...",
    # Lock coordination profile
    clickhouse_coordination_profile="CH-1",  # CH-0, CH-1, CH-2, CH-3, CH-4
)
```

## Setup Guide

### Step 1: Configure database connection

```python
from dbwarden import DbwardenDatabase

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    default = True
    database_type = "clickhouse"
    
    # Single-node
    database_url_sync = "clickhouse://default:@localhost:8123/analytics"
    
    # Or ON CLUSTER
    # database_url_sync = "clickhouse://default:@clickhouse1:8123/analytics"
    # ch_cluster = "production_cluster"
    
    model_paths = ["app/analytics_models"]
```

### Step 2: Define models

```python
from sqlalchemy import Column, Integer, String, Float, Date
from sqlalchemy.orm import DeclarativeBase
from dbwarden.databases.clickhouse import CHTableMeta, ch_table, merge_tree

class Base(DeclarativeBase):
    pass

class Event(Base):
    __tablename__ = "events"
    
    id = Column(Integer, primary_key=True)
    event_date = Column(Date)
    event_type = Column(String(100))
    amount = Column(Float)
    
    class Meta(CHTableMeta):
        ch = ch_table(
            engine=merge_tree(),
            order_by=["event_date", "id"],
            partition_by="toYYYYMM(event_date)",
        )
```

### Step 3: Generate and apply migrations

```bash
# Generate migration
dbwarden make-migrations "create events table"

# Review the generated SQL
cat migrations/analytics/analytics__0001_create_events_table.sql

# Apply migration
dbwarden migrate
```

### Step 4: Verify

```bash
# Check status
dbwarden status

# Check lock status
dbwarden lock-status

# Verify schema
dbwarden diff
```

## Production Recommendations

### For single-node deployments

```python
class Analytics(DbwardenDatabase):
    database_type = "clickhouse"
    database_url_sync = "clickhouse://default:@localhost:8123/analytics"
    ch_cluster = None  # Single-node
```

### For ON CLUSTER deployments

```python
class Analytics(DbwardenDatabase):
    database_type = "clickhouse"
    database_url_sync = "clickhouse://default:@clickhouse1:8123/analytics"
    ch_cluster = "production_cluster"
```

### For replicated deployments

```python
class Analytics(DbwardenDatabase):
    database_type = "clickhouse"
    database_url_sync = "clickhouse://default:@clickhouse1:8123/analytics"
    ch_replicated_database = True
```

## Idempotency Checklist

Before enabling CH-1 in production:

1. **Audit existing migrations**:
   ```bash
   grep -E "RENAME|MODIFY COLUMN|UPDATE|DELETE" migrations/analytics/*.sql
   ```

2. **Test idempotency**:
   ```bash
   dbwarden migrate --database analytics
   dbwarden migrate --database analytics  # Should be no-op
   ```

3. **Review generated migrations**:
   - All CREATE statements should use `IF NOT EXISTS`
   - All DROP statements should use `IF EXISTS`
   - All ALTER ADD/DROP COLUMN should use `IF NOT EXISTS`/`IF EXISTS`

## Troubleshooting

### "Non-idempotent statement" error

```
Non-idempotent: ALTER TABLE events RENAME TO events_v2
```

**Solution**: Make the statement idempotent:
```sql
-- Instead of:
ALTER TABLE events RENAME TO events_v2;

-- Use:
ALTER TABLE IF EXISTS events RENAME TO events_v2;
-- Or restructure to avoid RENAME
```

### "Lease expired" error

```
ClickHouse lease held by: host=clickhouse1, pid=1234, expires=2026-09-01 12:00:00
```

**Solution**: Wait for lease expiry or force release:
```bash
dbwarden unlock --database analytics --force
```

### "ON CLUSTER" not applied

**Check**: Verify `ch_cluster` is set in your config:
```python
print(Analytics.ch_cluster)  # Should print cluster name
```

### Replicated database conflicts

```
ConfigurationError: ch_cluster and ch_replicated_database are mutually exclusive
```

**Solution**: Use one or the other, not both:
```python
# Correct: ON CLUSTER
ch_cluster = "production_cluster"
ch_replicated_database = False

# Correct: Replicated database
ch_cluster = None
ch_replicated_database = True
```

## Advanced: Custom Lock Configuration

### Extended TTL

For long-running migrations:

```python
database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="clickhouse://...",
    # Lock TTL (default: 120s)
    clickhouse_lock_ttl=600,  # 10 minutes
)
```

### Multiple namespaces

Run independent migration streams:

```python
# Stream 1: schema migrations
database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="clickhouse://...",
    lock_namespace="schema",
)

# Stream 2: data operations
database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="clickhouse://...",
    lock_namespace="data",
)
```

## See Also

- [Migration Locking](../advanced/migration-locking.md) - General locking documentation
- [ClickHouse Databases](../databases/clickhouse/index.md) - ClickHouse database configuration
- [Immutability](../databases/clickhouse/immutability.md) - What can never change in ClickHouse
- [Safety Classification](../databases/clickhouse/safety.md) - Change classification levels
