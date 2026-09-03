# Migration Locking

dbwarden uses per-engine native locking to prevent concurrent schema mutation. This page explains how it works, what happens when it fails, and how to recover from a stuck lock.

## How locking works

When `dbwarden migrate` runs, it:

1. Acquires a native lock on the database engine (advisory lock for PostgreSQL, named lock for MySQL, BEGIN IMMEDIATE for SQLite, lease for ClickHouse)
2. Writes a status row in the `dbwarden_lock` table for observability
3. Starts a background heartbeat to detect stale workers
4. Executes all pending migrations
5. Stops the heartbeat and releases the lock on success or failure

The lock mechanism varies by engine:

| Engine | Lock Type | Grade | Auto-release on crash |
|--------|-----------|-------|----------------------|
| PostgreSQL | Session-level advisory lock | A | Yes (connection teardown) |
| MySQL/MariaDB | Named user lock (GET_LOCK) | A | Yes (connection teardown) |
| SQLite | BEGIN IMMEDIATE transaction | B | Yes (journal cleanup) |
| ClickHouse | Lease row with fencing token | C | After TTL expiry |

If a second `migrate` invocation starts while the first holds the lock, it fails immediately with:

```
Could not acquire migration lock. <holder diagnostics>
```

dbwarden does not retry on lock failure by default. The calling process (CI job, deploy script) must decide whether to retry or abort.

## Inspecting lock state

```bash
$ dbwarden lock-status --database primary
```

Output when unlocked:

```
Migration lock: INACTIVE
```

Output when locked:

```
Migration lock status
  State:       RUNNING
  Health:      HEALTHY
  Execution:   abc123def456ghi7
  Host:        deploy-runner-7
  PID:         1234
  Migration:   V042
  Acquired:    2026-08-22 12:03:14Z
  Heartbeat:   2026-08-22 12:04:01Z
```

Health verdicts:

- **HEALTHY**: Lock held, heartbeat is fresh
- **STUCK**: Lock held, heartbeat is stale (process may be paused or dead)
- **DEAD**: Lock free, status row shows dead worker
- **AVAILABLE**: No lock held
- **COMPLETE**: Migration completed successfully
- **FAILED**: Migration failed
- **INSPECTING**: Recovery inspection in progress
- **NEEDS_REVIEW**: Human intervention required

## Heartbeat

dbwarden runs a background heartbeat that updates the `last_heartbeat_at` timestamp every 15 seconds. This allows other processes to detect stale or dead workers.

On native-lock engines (PostgreSQL, MySQL), heartbeat failure is an observability event only; the migration continues. On SQLite, there is no heartbeat because the migration transaction holds the write lock. On fallback engines (ClickHouse), heartbeat failure is fatal after a configurable grace period.

The heartbeat runs on a separate connection to avoid interfering with the migration connection.

## Stuck lock recovery

A lock becomes stuck when:

- The migration process was killed (SIGKILL, OOM, machine restart)
- A CI job was cancelled mid-run
- A deploy container was stopped before migrate completed
- The process is paused (GC, SIGSTOP, CPU starvation)

**Before unlocking, confirm no migration is running:**

```bash
# Check if the PID from lock-status is still alive
ps aux | grep <pid>

# Or check your deployment logs / CI job status
```

If the process is genuinely dead:

```bash
# 1. Confirm lock state and holder
$ dbwarden lock-status --database primary

# 2. Inspect migration history to see what ran last
$ dbwarden history --database primary

# 3. Check pending migrations
$ dbwarden status --database primary

# 4. Release the stale lock
$ dbwarden unlock --database primary --force

# 5. Retry migration
$ dbwarden migrate --database primary
```

The `--force` flag skips the confirmation prompt. Without it, `unlock` shows holder diagnostics and requires confirmation.

## Recovery state machine

When a new worker acquires the lock and detects a dead predecessor, it enters the recovery state machine:

```
AVAILABLE → RUNNING → COMPLETE → AVAILABLE (loop back)
                     → FAILED → AVAILABLE (loop back)
                     → DEAD (detected by new acquirer)
                          → INSPECTING
                               → COMPLETE (all steps applied)
                               → RUNNING (resume safely)
                               → NEEDS_REVIEW (human decision)
                                    → AVAILABLE (loop back)
                                    → RUNNING (retry)
```

The INSPECTING state compares the recorded migration checksum with the candidate migration. If they match, the migration can be resumed. If they don't match, the worker transitions to NEEDS_REVIEW and waits for human intervention.

## INSPECTING procedure

When a new worker acquires the lock and detects a dead predecessor (DEAD state), it executes the INSPECTING procedure:

1. **Compare checksums**: The recorded `migration_checksum` is compared with the candidate migration's checksum
2. **Walk history**: The migration history table is checked to determine the last durably recorded step
3. **Verify catalog state**: On transactional engines (PostgreSQL, SQLite), verify catalog state matches history
4. **Reconcile catalog**: On non-transactional engines (MySQL, ClickHouse), reconcile catalog against step list
5. **Apply recovery policy**: Based on the configured `recovery_policy`, transition to RUNNING (resume) or NEEDS_REVIEW (halt)

## Recovery policy

Configure the recovery policy in `dbwarden.py`:

```python
class Primary(DbwardenDatabase):
    database_name = "primary"
    recovery_policy = "halt"  # default
```

Policies:

| Policy | Behavior |
|--------|----------|
| `halt` (default) | Transition to NEEDS_REVIEW, print inspection report, exit with code 78 |
| `resume_idempotent` | Resume only if all remaining statements are idempotent; else NEEDS_REVIEW |
| `force` | Re-run remaining steps unconditionally (audit-logged) |

## Re-entrancy protection

dbwarden prevents nested acquisition of the same advisory lock on PostgreSQL. If a worker attempts to acquire a lock it already holds, the acquisition is refused with an error. This prevents lock corruption from nested migration runs.

## Heartbeat fatal on fallback engines

On ClickHouse (fallback engine), if the heartbeat fails repeatedly (exceeding `fatal_grace`), the migration run is aborted immediately. This prevents a worker from executing DDL after losing its lease.

## ClickHouse per-statement fence check

For ClickHouse, dbwarden performs a per-statement fence check before each DDL statement. This validates that the worker still owns the lease by checking the fencing token matches. If the token has advanced (another worker acquired the lease), the migration is aborted with a `LockError`.

## POSSIBLE_CONCURRENT_EXECUTION detection

After a ClickHouse migration completes, dbwarden re-reads the lease and checks if the fencing token has advanced during the run. If it has, a `POSSIBLE_CONCURRENT_EXECUTION` warning is printed, indicating another worker may have executed DDL concurrently.

## TCP keepalive

dbwarden enables TCP keepalive on migration connections to prevent idle-timeout kills:

- **MySQL/MariaDB**: 60s idle, 3 probes, 30s interval (configurable via `tcp_keepalive` config)
- **PostgreSQL**: Uses default OS settings
- **SQLite**: N/A (file-based)

## SQLite busy_timeout

For SQLite, dbwarden sets a configurable `busy_timeout` to control how long `BEGIN IMMEDIATE` waits for the write lock:

```python
class Primary(DbwardenDatabase):
    database_name = "primary"
    sqlite_busy_timeout = 5000  # Wait up to 5 seconds
```

Default is 0 (fail fast). Set to a positive value to wait for the write lock.

## Prepared transactions prohibition

dbwarden prohibits 2PC (prepared transactions) on the migration connection. Advisory locks interact badly with prepared transactions, which can lead to lock leaks or corruption.

## AUDIT-level logging

dbwarden logs all unlock operations at AUDIT level for compliance and forensics. The audit log includes:

- Operator identity (user, host, PID)
- Target diagnostics (host, PID, execution ID)
- Outcome (success/failure)

Example audit log entry:
```
AUDIT: unlock executed by user=deploy host=ci-runner-1 pid=5678
target_host=deploy-runner-7 target_pid=1234 target_execution=abc123def456 outcome=success
```

## v1 lock phase-out

dbwarden v2 uses native locking with the `dbwarden_lock` table schema. The v1 lock schema (with `locked`, `owner_token` columns) is no longer supported. If a v1 table exists, it will be automatically dropped and recreated with the v2 schema on first use.

## Per-statement history recording

For non-transactional engines (MySQL, ClickHouse), dbwarden can record history per-statement instead of per-migration file. This enables finer-grained recovery but may impact performance.

```python
class Primary(DbwardenDatabase):
    database_name = "primary"
    per_statement_history = True  # Default: False
```

When enabled, each DDL statement is recorded to the history table before the next statement begins. This is the only resumption signal on non-transactional engines.

Default is 0 (fail fast). Set to a positive value to wait for the write lock.

## When NOT to use `unlock`

Do not run `unlock` if:

- You are unsure whether a migration process is still running
- The lock status shows HEALTHY with a recent heartbeat
- Multiple processes share a database and you cannot confirm all are idle

Releasing a lock held by a live migration process will allow a second migration to start concurrently, which can corrupt schema state.

## ClickHouse coordination profiles

ClickHouse has no session-scoped user locks, so dbwarden uses a lease-based approach with coordination profiles. For the full explanation of why each profile exists and how to set up your deployment, see [ClickHouse Locking](clickhouse-locking.md).

| Profile | Description | Grade |
|---------|-------------|-------|
| CH-0 | Lease row with fencing token | C |
| CH-1 | CH-0 + strict idempotency enforcement | C |
| CH-2 | CH-1 + Keeper-backed lock | C |
| CH-3 | Migration proxy choke point | A- |
| CH-4 | Singleton executor | A* |

CH-0 and CH-1 are implemented in core. CH-2, CH-3, and CH-4 require external infrastructure and are documented as deployment options.

### CH-1 idempotency

Under CH-1, non-idempotent statements are refused:

- Idempotent: `CREATE TABLE IF NOT EXISTS`, `DROP TABLE IF EXISTS`, `ALTER TABLE ADD COLUMN IF NOT EXISTS`
- Non-idempotent: `RENAME`, `MODIFY COLUMN`, `UPDATE`, `DELETE`
- Unknown: treated as non-idempotent for safety

## Preventing concurrent migration in CI

In CI/CD, run migrations from a single job with no parallelism:

```yaml
# GitHub Actions: serialize via job dependency
jobs:
  migrate:
    runs-on: ubuntu-latest
    steps:
      - run: dbwarden migrate --database primary
  deploy:
    needs: migrate
    ...
```

If your pipeline can trigger multiple concurrent deploys, add a concurrency group:

```yaml
concurrency:
  group: migrate-${{ github.ref }}
  cancel-in-progress: false
```

`cancel-in-progress: false` queues the second run instead of cancelling it, which avoids orphaned locks from killed jobs.

## Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `lock_namespace` | `"default"` | Lock scope (allows independent lock streams) |

## Distributed locking with Redis

For multi-instance deployments where multiple application replicas could
trigger migrations concurrently, the `dbwarden-redis` plugin provides
a Redis-backed distributed lock:

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

The Redis lock uses `SET NX EX` with a configurable TTL (default 60 seconds).
If the application crashes while holding the lock, Redis releases it
automatically after the TTL expires.

### Database-level vs Redis lock

| Aspect | Database lock | Redis lock |
|--------|---------------|------------|
| Scope | CLI commands (`migrate`, `rollback`, `downgrade`) | Any code path you wrap |
| Storage | `dbwarden_lock` table in the target database | Redis key |
| TTL | Heartbeat-based stale detection (default 45s); native locks auto-release on connection close | Configurable TTL (default 60s) |
| Failure mode | Detects stale locks via heartbeat; recovery state machine guides recovery | Auto-released after TTL |
| External dependency | None (uses the database itself) | Redis required |

Both locks can be used independently or together; they guard different
entry points. The database lock protects the CLI; the Redis lock
protects any entry point you wrap.

See the [dbwarden-redis](https://github.com/dbwarden-org/dbwarden-redis) plugin for full documentation.

## Lifespan integration

The `dbwarden_lifespan` context manager wraps migration logic and
engine disposal into a single FastAPI-compatible lifespan. When using
the Redis lock in a lifespan, acquire the lock before entering the
migration context:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from dbwarden_fastapi import dbwarden_lifespan
from dbwarden_redis import migration_lock

@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = Redis.from_url("redis://localhost:6379")
    async with migration_lock(redis):
        async with dbwarden_lifespan(mode="migrate", allow_in_production=True):
            yield
```

See also: [Safe Deployment](safe-deployment.md) | [CI/CD Patterns](ci-cd-patterns.md) | [`lock` commands](../commands/lock.md)

The FastAPI lifespan helper lives in the `dbwarden-fastapi` plugin: [dbwarden-fastapi](https://github.com/dbwarden-org/dbwarden-fastapi).
The Redis lock lives in the `dbwarden-redis` plugin: [dbwarden-redis](https://github.com/dbwarden-org/dbwarden-redis).
