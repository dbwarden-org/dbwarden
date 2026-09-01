# ClickHouse Migration Locking

dbwarden provides production-grade migration locking for ClickHouse. This guide explains why ClickHouse needs custom locking, what the coordination profiles are, and how to set up your deployment.

See [Locking Architecture](../databases/locking.md) for the general locking model across all backends.

## Why ClickHouse needs custom locking

Every other database backend (PostgreSQL, MySQL, SQLite) provides a native lock primitive that dbwarden can use directly:

- PostgreSQL has `pg_advisory_lock()` (session-scoped, auto-releases on crash)
- MySQL has `GET_LOCK()` (session-scoped, auto-releases on crash)
- SQLite has `BEGIN IMMEDIATE` (write锁, released on crash via journal cleanup)

ClickHouse has none of these. Specifically, ClickHouse is missing:

1. **No session-scoped user locks.** There is no `LOCK TABLE` or advisory lock equivalent. You cannot hold a lock for the duration of a session.
2. **No synchronous compare-and-swap on table rows.** The `INSERT ... WHERE NOT EXISTS` pattern is not atomic in the way PostgreSQL's `INSERT ... ON CONFLICT` is.
3. **Non-transactional DDL.** DDL statements (CREATE, ALTER, DROP) cannot be rolled back. If a migration fails midway, there is no automatic undo.

Without native locks, two concurrent `dbwarden migrate` processes could both acquire the "lock" simultaneously, execute conflicting DDL, and corrupt the schema. dbwarden must therefore emulate locking using what ClickHouse does provide: table rows, ZooKeeper, and external infrastructure.

The coordination profiles (CH-0 through CH-4) represent progressively stronger guarantees, each addressing a specific weakness of the previous profile. They are not arbitrary levels; each one exists because the previous level has a concrete failure mode that cannot be solved within ClickHouse alone.

## Coordination profiles

| Profile | Description | Grade | Use case | What it fixes |
|---------|-------------|-------|----------|---------------|
| **CH-0** | Lease row with fencing token | C | Non-production, human-gated | Baseline: prevents concurrent migration via lease table |
| **CH-1** | CH-0 + strict idempotency | C | Production with idempotent migrations | Prevents data corruption from non-idempotent DDL |
| **CH-2** | CH-1 + Keeper-backed lock | C | Replicated with clickhouse-keeper | Better liveness: crash releases lock immediately via znode expiry |
| **CH-3** | Migration proxy choke point | A- | Production with migration proxy | Eliminates stale-lock window: single migration process |
| **CH-4** | Singleton executor | A* | Kubernetes with dedicated executor | Strongest guarantee: no DDL credentials on workers |

### CH-0: Lease with fencing token

The baseline profile. dbwarden writes a lease row to `dbwarden_lock` with a fencing token (monotonically increasing integer). A second process cannot acquire the lease while the first is active.

**Why it is grade C:** The fence check and DDL statement are not atomic. A paused worker (GC pause, SIGSTOP, CPU starvation) can lose its lease, another worker can acquire it, and the paused worker resumes and executes DDL against a database it no longer owns. The fencing token detects this, but only after the fact.

```sql
INSERT INTO dbwarden_lock
    (namespace, execution_id, owner_id, fencing_token, expires_at, ...)
SELECT ...
WHERE NOT EXISTS (
    SELECT 1 FROM dbwarden_lock FINAL
    WHERE namespace = :ns AND expires_at > now()
)
```

### CH-1: Idempotency enforcement

CH-1 adds strict idempotency checking on top of CH-0. Non-idempotent statements (`RENAME`, `MODIFY COLUMN`, `UPDATE`, `DELETE`) are refused unless `--allow-non-idempotent` is passed.

**Why it exists:** Even with CH-0's lease, a stale worker can execute non-idempotent DDL after losing its lease. Idempotent DDL (`CREATE TABLE IF NOT EXISTS`, `DROP TABLE IF EXISTS`) is safe to re-execute; non-idempotent DDL is not. By refusing non-idempotent statements, CH-1 limits the blast radius of the stale-worker window to idempotent operations only.

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

### CH-2: Keeper-backed lock

For replicated deployments with clickhouse-keeper (ZooKeeper-compatible), CH-2 replaces the lease table with ephemeral-sequential znodes.

**Why it exists:** CH-0/CH-1 lease expiry depends on TTL, which means there is always a window between "worker died" and "lease expired" where the database is locked but no work is happening. With ZooKeeper ephemeral znodes, the lock is released the instant the ZooKeeper session ends (typically within seconds of a crash), eliminating the stale-lock window for liveness purposes.

- Lock via ephemeral-sequential znodes
- Crash triggers session expiry, which removes the znode immediately
- Fencing token equals the znode sequence number (monotonic by construction)
- Status row maintained for observability

### CH-3: Migration proxy

All migration DDL flows through a single migration proxy process. Workers connect only to the proxy, not directly to ClickHouse.

**Why it exists:** Even with CH-2's fast lock release, two workers could theoretically acquire the lock in rapid succession during a network partition. CH-3 eliminates this by design: only one process ever has DDL credentials, so concurrent migration is structurally impossible.

- Proxy holds the lock (CH-2 or CH-0)
- Workers have no direct DDL access to ClickHouse
- Network-restricted migration user (proxy-only DDL access)
- Takeover: `KILL QUERY WHERE initial_user = 'dbwarden_migration'` during grace window

### CH-4: Singleton executor

Workers submit migration jobs; a single executor process executes them. The executor is the only process with DDL credentials, enforced by the deployment platform.

**Why it exists:** CH-3 requires running a proxy process, which is additional infrastructure. CH-4 achieves the same guarantee using the deployment platform's native primitives (Kubernetes Job with `parallelism: 1`). Workers submit jobs; the platform serializes them; the executor runs them one at a time.

- Workers have no DDL credentials
- Executor is the only mutation channel
- Enforced by deployment platform (Kubernetes Job with `parallelism: 1`)
- Executor holds CH-2 Keeper lock as defense in depth

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
    ch_cluster = "production_cluster"
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
    ch_replicated_database = True
```

## Configuration

```python
database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="clickhouse://...",
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
    ch_cluster = None
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

- [Locking Architecture](../databases/locking.md): General locking model across all backends
- [ON Cluster](../databases/clickhouse/on-cluster.md): Cluster modes and DDL propagation
- [Immutability](../databases/clickhouse/immutability.md): What can never change in ClickHouse
- [Safety Classification](../databases/clickhouse/safety.md): Change classification levels
