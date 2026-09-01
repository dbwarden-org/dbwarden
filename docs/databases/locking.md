# Locking Architecture

dbwarden uses per-engine native locking to prevent concurrent schema mutation. This page explains the general architecture, why each engine needs a different strategy, and links to per-backend details.

## Why locking matters

A schema migration is a read-modify-write cycle on the database catalog. If two processes run migrations concurrently, they can:

- Apply the same migration twice (double execution)
- Interleave DDL statements in unpredictable order
- Corrupt schema state (e.g., one process creates a table, another drops it mid-transaction)

dbwarden prevents this by acquiring an exclusive lock before any DDL executes and holding it until the migration completes (or fails).

## Lock strategy by engine

Each database engine provides different native locking primitives. dbwarden uses whichever primitive the engine offers:

| Engine | Native Lock | Grade | Auto-release on crash | How it works |
|--------|-------------|-------|----------------------|--------------|
| **PostgreSQL** | Advisory lock | A | Yes (connection teardown) | `pg_advisory_lock()` on a fixed key; session-scoped, released when the connection closes |
| **MySQL/MariaDB** | Named user lock | A | Yes (connection teardown) | `GET_LOCK('dbwarden')`; released when the connection closes or `RELEASE_LOCK()` is called |
| **SQLite** | BEGIN IMMEDIATE | B | Yes (journal cleanup) | `BEGIN IMMEDIATE` acquires a write lock on the database file; held for the entire migration run |
| **ClickHouse** | Lease row + fencing token | C | After TTL expiry | Atomic conditional upsert to `dbwarden_lock`; no native lock primitive, so dbwarden emulates one |

### Why the grades differ

- **Grade A** (PostgreSQL, MySQL): The database engine provides a session-scoped lock that auto-releases on crash. The lock is authoritative; no stale-lock recovery needed.
- **Grade B** (SQLite): The database is a single file. `BEGIN IMMEDIATE` prevents concurrent writes, but the lock is tied to the process, not the session. A crash releases it via journal cleanup, but the window is less predictable.
- **Grade C** (ClickHouse): ClickHouse has no session-scoped user locks, no synchronous compare-and-swap on table rows, and non-transactional DDL. dbwarden must emulate locking using a lease table with TTL-based expiry, which introduces a stale-lock window.

## General lock lifecycle

```
migrate
  -> acquire lock (engine-specific)
  -> write status row to dbwarden_lock
  -> start heartbeat (background thread, every 15s)
  -> execute pending migrations
  -> stop heartbeat
  -> release lock
```

If the process crashes mid-migration, the lock is released according to the engine's native behavior (connection teardown for PostgreSQL/MySQL, journal cleanup for SQLite, TTL expiry for ClickHouse).

## Observability

All engines write a status row to the `dbwarden_lock` table. This row records:

- Execution ID (unique per migration run)
- Owner identity (host, PID)
- Fencing token (monotonically increasing, prevents stale workers)
- Heartbeat timestamp (updated every 15s)
- Lease expiry (ClickHouse only)

Check lock state with:

```bash
dbwarden lock-status --database <name>
```

Health verdicts:

| Verdict | Meaning |
|---------|---------|
| HEALTHY | Lock held, heartbeat is fresh |
| STUCK | Lock held, heartbeat is stale (process may be paused or dead) |
| DEAD | Lock free, status row shows dead worker |
| AVAILABLE | No lock held |

## Recovery state machine

When a new worker acquires the lock and detects a dead predecessor, it enters the recovery state machine:

```
AVAILABLE -> RUNNING -> COMPLETE
                    -> FAILED
                    -> DEAD (detected by new acquirer)
                         -> INSPECTING
                              -> COMPLETE (all steps applied)
                              -> resume (safely resumable)
                              -> NEEDS_REVIEW (human decision)
```

The INSPECTING state compares the recorded migration checksum with the candidate migration. If they match, the migration can be resumed. If they do not match, the worker transitions to NEEDS_REVIEW and waits for human intervention.

## Distributed locking (Redis)

For multi-instance deployments where multiple application replicas could trigger migrations concurrently, the `dbwarden-redis` plugin provides a Redis-backed distributed lock:

```bash
dbwarden plugin add dbwarden-redis
```

```python
from redis.asyncio import Redis
from dbwarden_redis import migration_lock

redis = Redis.from_url("redis://localhost:6379")

async with migration_lock(redis):
    await run_migration()
```

The Redis lock uses `SET NX EX` with a configurable TTL (default 60 seconds). The database lock and Redis lock guard different entry points (CLI vs any wrapped code path) and can be used independently or together. See [dbwarden-redis](https://github.com/dbwarden-org/dbwarden-redis) for full documentation.

## Per-backend details

- [PostgreSQL, MySQL, SQLite](../../advanced/migration-locking.md): General locking guide covering lock acquisition, heartbeat, stuck lock recovery, and CI/CD patterns
- [ClickHouse](../../advanced/clickhouse-locking.md): Coordination profiles (CH-0 through CH-4), why ClickHouse needs custom locking, idempotency enforcement, and production setup

## Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `lock_namespace` | `"default"` | Lock scope (allows independent lock streams) |

## See also

- [ON Cluster](clickhouse/on-cluster.md): Cluster modes and DDL propagation
- [Safe Deployment](../advanced/safe-deployment.md): Deployment patterns that interact with locking
- [CI/CD Patterns](../advanced/ci-cd-patterns.md): Serialize migrations in pipelines
