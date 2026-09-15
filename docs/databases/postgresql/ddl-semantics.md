# DDL semantics

PostgreSQL DDL statements have runtime behaviors that affect migration safety: lock modes, table rewrites, transaction boundaries, and version-gated optimizations. Understanding these prevents production outages caused by migrations that are syntactically correct but operationally dangerous.

## DDL locking fundamentals

### The three lock modes that matter

PostgreSQL has eight table-level lock modes. For schema migrations, three dominate:

| Lock mode | Blocks reads? | Blocks writes? | Used by |
|-----------|--------------|----------------|---------|
| **ACCESS EXCLUSIVE** | Yes | Yes | Most `ALTER TABLE`, `DROP TABLE`, `TRUNCATE`, `REINDEX`, `CLUSTER`, `VACUUM FULL` |
| **SHARE UPDATE EXCLUSIVE** | No | Yes (and other DDL) | `VACUUM`, `ANALYZE`, `CREATE INDEX CONCURRENTLY`, `VALIDATE CONSTRAINT` |
| **ACCESS SHARE** | No | No | `SELECT` (held by every read query) |

`ACCESS EXCLUSIVE` conflicts with *every other lock mode*, including `ACCESS SHARE`. This means a single `ALTER TABLE` blocks all `SELECT` statements on that table.

### The lock queue pile-up

A blocked `ALTER TABLE` does not wait politely to the side. It takes the head of the lock queue, and everything else lines up behind it:

```
1. Reporting query starts. Holds ACCESS SHARE on users. Runs for 40 seconds.
2. Migration: ALTER TABLE users ADD COLUMN age int. Wants ACCESS EXCLUSIVE.
   Conflicts with the reporting query. Waits.
3. Normal SELECT from the app arrives. Wants ACCESS SHARE.
   Does NOT conflict with the reporting query, but DOES conflict with
   the queued ACCESS EXCLUSIVE ahead of it. Waits.
4. Every subsequent SELECT piles up behind step 3.
```

The table is dark for the duration of the slowest query that was running when the migration started. The `ALTER TABLE` itself was never slow.

### lock_timeout

`lock_timeout` bounds the *acquisition* phase. If the lock cannot be acquired within the timeout, the statement errors with `55P03 lock_not_available`, releases its queue position, and the backlog drains:

```sql
SET lock_timeout = '2s';
ALTER TABLE users ADD COLUMN age int;
-- If lock not acquired in 2s: ERROR, backlog drains, SELECTs resume
```

**Without lock_timeout**, a migration waiting behind a 40-minute reporting query holds the queue for 40 minutes. **With lock_timeout = 2s**, it fails in 2 seconds and can be retried.

Recommended values:

| Environment | lock_timeout | Rationale |
|-------------|-------------|-----------|
| Production | 500ms - 2s | Fail fast, retry on deploy |
| Staging | 5s | More tolerance for background queries |
| CI/development | 0 (default) | No concurrent work to conflict with |

!!! warning "lock_timeout vs statement_timeout"
    `lock_timeout` bounds *waiting for the lock*. `statement_timeout` bounds *executing the statement after the lock is acquired*. They serve different purposes:
    - `lock_timeout` prevents the queue pile-up
    - `statement_timeout` prevents a long-running DDL (e.g., `ALTER COLUMN TYPE` on a large table) from running indefinitely

    Set `lock_timeout` smaller than any non-zero `statement_timeout`. A unitless value is milliseconds.

### Hold-time: locks held until transaction end

PostgreSQL holds table-level locks until the **end of the transaction**, not until the statement finishes. A 6ms `ALTER TABLE` inside a 5-minute transaction holds `ACCESS EXCLUSIVE` for the full 5 minutes:

```sql
BEGIN;
ALTER TABLE orders ADD COLUMN note3 text;  -- 6ms work, but...
UPDATE orders SET note3 = compute(note3);  -- 5 minutes of backfill
COMMIT;  -- ACCESS EXCLUSIVE released here, 5 minutes later
```

The fix: separate DDL from backfill. Run DDL in its own short transaction, then backfill in batches:

```sql
-- Migration: DDL only. Brief lock.
BEGIN;
SET LOCAL lock_timeout = '2s';
ALTER TABLE orders ADD COLUMN note3 text;
COMMIT;

-- Separate: backfill in batches, each its own short transaction
UPDATE orders SET note3 = compute(note3) WHERE id BETWEEN 1 AND 5000;
UPDATE orders SET note3 = compute(note3) WHERE id BETWEEN 5001 AND 10000;
-- ...
```

## Operation lock reference

Complete reference of every `ALTER TABLE` subform dbwarden generates, its lock mode, rewrite behavior, and zero-downtime safety.

### Column operations

| Operation | Lock mode | Rewrite? | Duration (large table) | Safe? |
|-----------|-----------|----------|----------------------|-------|
| ADD COLUMN (no default) | ACCESS EXCLUSIVE | No | Instant | Yes |
| ADD COLUMN (constant default, PG11+) | ACCESS EXCLUSIVE | No | Instant | Yes |
| ADD COLUMN (volatile default) | ACCESS EXCLUSIVE | **Yes** | Full rewrite | **No** |
| ADD COLUMN (stored generated) | ACCESS EXCLUSIVE | **Yes** | Full rewrite | **No** |
| ADD COLUMN (identity) | ACCESS EXCLUSIVE | **Yes** | Full rewrite | **No** |
| ADD COLUMN (virtual generated) | ACCESS EXCLUSIVE | No | Instant | Yes |
| DROP COLUMN | ACCESS EXCLUSIVE | No | Instant | Yes |
| ALTER COLUMN TYPE | ACCESS EXCLUSIVE | **Usually** | Full rewrite | **No** |
| ALTER COLUMN TYPE (binary coercible) | ACCESS EXCLUSIVE | No | Index rebuild only | Partial |
| SET DEFAULT | ACCESS EXCLUSIVE | No | Instant | Yes |
| DROP DEFAULT | ACCESS EXCLUSIVE | No | Instant | Yes |
| SET NOT NULL | ACCESS EXCLUSIVE | No (scan) | Full table scan | **No** |
| SET NOT NULL (after validated CHECK, PG12+) | ACCESS EXCLUSIVE | No | Instant | Yes |
| SET NOT NULL NOT VALID (PG18+) | ACCESS EXCLUSIVE | No | Instant | Yes |
| SET DATA TYPE | ACCESS EXCLUSIVE | Usually | Full rewrite | **No** |
| SET STORAGE | ACCESS EXCLUSIVE | No | Instant | Yes |
| SET COMPRESSION | ACCESS EXCLUSIVE | No | Instant | Yes |
| SET STATISTICS | ACCESS EXCLUSIVE | No | Instant | Yes |
| ALTER COLUMN SET (attribute_option) | ACCESS EXCLUSIVE | No | Instant | Yes |
| SET COLUMN ENCRYPTED/DECRYPTED | ACCESS EXCLUSIVE | No | Instant | Yes |

### Table operations

| Operation | Lock mode | Rewrite? | Duration | Safe? |
|-----------|-----------|----------|----------|-------|
| SET TABLESPACE | ACCESS EXCLUSIVE | **Yes** | Full rewrite + copy | **No** |
| SET LOGGED | ACCESS EXCLUSIVE | **Yes** | Full rewrite | **No** |
| SET UNLOGGED | ACCESS EXCLUSIVE | **Yes** | Full rewrite | **No** |
| SET SCHEMA | ACCESS EXCLUSIVE | No | Metadata only | Yes |
| CLUSTER | ACCESS EXCLUSIVE | **Yes** | Full rewrite | **No** |
| ENABLE/DISABLE TRIGGER | ACCESS EXCLUSIVE | No | Instant | Yes |
| ENABLE/DISABLE RULE | ACCESS EXCLUSIVE | No | Instant | Yes |
| DISABLE ROW LEVEL SECURITY | ACCESS EXCLUSIVE | No | Instant | Yes |
| FORCE ROW LEVEL SECURITY | ACCESS EXCLUSIVE | No | Instant | Yes |
| SET ACCESS METHOD | ACCESS EXCLUSIVE | No | Metadata only | Yes |
| INHERIT/NO INHERIT | ACCESS EXCLUSIVE | No | Metadata only | Yes |
| OF type_name/NOT OF | ACCESS EXCLUSIVE | No | Metadata only | Yes |
| REPLICA IDENTITY | ACCESS EXCLUSIVE | No | Metadata only | Yes |
| ENABLE/DISABLE ROW LEVEL SECURITY | ACCESS EXCLUSIVE | No | Instant | Yes |
| ATTACH PARTITION | ACCESS EXCLUSIVE | No | Metadata only | Yes |
| DETACH PARTITION | ACCESS EXCLUSIVE | No | Metadata only | Yes |
| DETACH PARTITION CONCURRENTLY (PG16+) | SHARE UPDATE EXCLUSIVE | No | Metadata only | Yes |

### Index operations

| Operation | Lock mode | Duration | Safe? | Notes |
|-----------|-----------|----------|-------|-------|
| CREATE INDEX | ACCESS EXCLUSIVE | Full scan | **No** | Blocks all reads and writes |
| CREATE INDEX CONCURRENTLY | SHARE UPDATE EXCLUSIVE | Full scan (slower) | Yes | Cannot run in transaction block |
| DROP INDEX | ACCESS EXCLUSIVE | Instant | Yes | |
| DROP INDEX CONCURRENTLY | SHARE UPDATE EXCLUSIVE | Instant | Yes | Cannot run in transaction block |
| REINDEX | ACCESS EXCLUSIVE | Full rebuild | **No** | Cannot run in transaction block |
| REINDEX CONCURRENTLY (PG12+) | SHARE UPDATE EXCLUSIVE | Full rebuild | Yes | Cannot run in transaction block |
| ALTER INDEX | ACCESS EXCLUSIVE | No | Yes | Metadata only |

### Constraint operations

| Operation | Lock mode | Duration | Safe? |
|-----------|-----------|----------|-------|
| ADD CHECK ... NOT VALID | ACCESS EXCLUSIVE | Instant (catalog only) | Yes |
| VALIDATE CONSTRAINT (CHECK) | SHARE UPDATE EXCLUSIVE | Full scan, reads+writes OK | Yes |
| ADD FOREIGN KEY | SHARE ROW EXCLUSIVE | Validates rows | Partial |
| ADD FOREIGN KEY ... NOT VALID | SHARE ROW EXCLUSIVE | Instant (catalog only) | Yes |
| VALIDATE CONSTRAINT (FK) | SHARE UPDATE EXCLUSIVE | Full scan, reads+writes OK | Yes |
| ADD UNIQUE CONSTRAINT | ACCESS EXCLUSIVE | Index build | Partial |
| ADD PRIMARY KEY | ACCESS EXCLUSIVE | Index build | Partial |
| ADD EXCLUDE | ACCESS EXCLUSIVE | GiST build | Partial |
| DROP CONSTRAINT | ACCESS EXCLUSIVE | Instant | Yes |
| ALTER CONSTRAINT ... DEFERRABLE | ACCESS EXCLUSIVE | No | Yes |

### Comment operations

| Operation | Lock mode | Duration | Safe? |
|-----------|-----------|----------|-------|
| COMMENT ON TABLE | ACCESS EXCLUSIVE | Instant | Yes |
| COMMENT ON COLUMN | ACCESS EXCLUSIVE | Instant | Yes |
| COMMENT ON INDEX | ACCESS EXCLUSIVE | Instant | Yes |
| COMMENT ON CONSTRAINT | ACCESS EXCLUSIVE | Instant | Yes |
| COMMENT ON TYPE | ACCESS EXCLUSIVE | Instant | Yes |

## CREATE INDEX CONCURRENTLY

### Why it cannot run inside a transaction

`CREATE INDEX CONCURRENTLY` splits its work across multiple internal transactions: build against a snapshot, wait for older transactions to drain, scan again to catch changes, mark valid. Waiting for other transactions to finish is impossible from inside a transaction that is itself still open. PostgreSQL enforces this:

```
ERROR: CREATE INDEX CONCURRENTLY cannot run inside a transaction block
```

This is a permanent server-side constraint. No tool can work around it.

### Three-transaction lifecycle

1. **Transaction 1**: Index entered as `INVALID` in system catalogs
2. **Transaction 2**: First table scan (builds index against snapshot)
3. **Transaction 3**: Second table scan (catches changes since first scan)
4. **Mark valid**: Index becomes usable for queries

### On failure: invalid index left behind

If the build fails (deadlock, uniqueness violation, OOM), the index remains as `INVALID`:

```
Indexes:
    "idx" btree (col) INVALID
```

Invalid indexes are ignored for queries but still consume update overhead. The recovery path:

```sql
DROP INDEX CONCURRENTLY IF EXISTS idx;
CREATE INDEX CONCURRENTLY idx ON t (col);
```

!!! warning "IF NOT EXISTS trap"
    `CREATE INDEX CONCURRENTLY IF NOT EXISTS` will silently "succeed" over a broken `INVALID` index. Always `DROP INDEX CONCURRENTLY IF EXISTS` before retrying.

### dbwarden handles this automatically

dbwarden prefixes concurrent index creation with `-- @dbwarden:autocommit`, which executes the statement outside the migration transaction on a separate autocommit connection. The `--no-concurrent` flag disables this for development environments.

See [DDL Behavior](ddl-behavior.md) for the autocommit mechanism details.

## ADD COLUMN fast path (PG11+)

### Constant defaults: instant on any table size

PostgreSQL 11 changed `ADD COLUMN` with a constant default from a full table rewrite to a catalog-only operation. The default value is evaluated at `ALTER TABLE` time and stored in `pg_attribute.atthasmissing`. Existing rows receive the value at read time:

```sql
-- Instant, even on 100M rows (PG11+)
ALTER TABLE orders ADD COLUMN status TEXT NOT NULL DEFAULT 'pending';
```

**Constant defaults** (fast path): string literals, integers, booleans, `CURRENT_TIMESTAMP`, `CURRENT_DATE`, `CURRENT_TIME`, `NULL`, `ARRAY[]`, etc.

**Volatile defaults** (full rewrite): `random()`, `gen_random_uuid()`, `clock_timestamp()`, `nextval()`, any function marked `VOLATILE`.

### When the old advice is still correct

Before PG11, the standard pattern was:

```sql
-- Old pattern (PG10 and below): add nullable, backfill, set NOT NULL
ALTER TABLE orders ADD COLUMN status TEXT;
UPDATE orders SET status = 'pending';
ALTER TABLE orders ALTER COLUMN status SET NOT NULL;
```

This is **still necessary** when:

- The default is volatile (e.g., `DEFAULT now()` or `DEFAULT gen_random_uuid()`)
- The column uses a stored generated expression
- The column uses an identity column
- You are on PG 10 or below

For PG11+ with constant defaults, the direct approach is safe and fast:

```sql
-- PG11+: direct is fine for constant defaults
ALTER TABLE orders ADD COLUMN status TEXT NOT NULL DEFAULT 'pending';
```

## NOT NULL via CHECK + VALIDATE

### The problem

`SET NOT NULL` scans the entire table under `ACCESS EXCLUSIVE` to verify no NULLs exist. On a large table, this holds the lock for the duration of the scan:

```sql
-- Dangerous on large tables: full scan under ACCESS EXCLUSIVE
ALTER TABLE orders ALTER COLUMN customer_ref SET NOT NULL;
```

### The four-step recipe

PostgreSQL 12+ can skip the scan if a validated `CHECK` constraint already proves the column contains no NULLs:

```sql
-- Step 1: Add CHECK NOT VALID (ACCESS EXCLUSIVE, instant, catalog only)
ALTER TABLE orders
    ADD CONSTRAINT orders_ref_notnull CHECK (customer_ref IS NOT NULL) NOT VALID;

-- Step 2: Validate (SHARE UPDATE EXCLUSIVE, full scan, reads+writes continue)
ALTER TABLE orders VALIDATE CONSTRAINT orders_ref_notnull;

-- Step 3: SET NOT NULL (ACCESS EXCLUSIVE, instant, scan skipped)
ALTER TABLE orders ALTER COLUMN customer_ref SET NOT NULL;

-- Step 4: Cleanup (optional, removes redundant CHECK)
ALTER TABLE orders DROP CONSTRAINT orders_ref_notnull;
```

Step 2 takes `SHARE UPDATE EXCLUSIVE` which allows concurrent reads and writes. Step 3 is instant because PG12+ recognizes the validated CHECK and skips the scan.

### dbwarden support

dbwarden supports `NOT VALID` / `VALIDATE CONSTRAINT` via the constraint handler. The `VALIDATE` step is marked with `-- @dbwarden:autocommit` to execute outside the migration transaction.

See [Constraints](constraints.md) for the full NOT VALID / VALIDATE documentation.

## Advisory lock operational details

dbwarden uses PostgreSQL advisory locks (`pg_try_advisory_lock`) for migration coordination. These have several operational subtleties.

### Session-scoped

Advisory locks are session-scoped: they release when the connection closes. This means:

- If the migration process crashes, the lock is released automatically
- If the connection is closed by a pooler, the lock is released
- There is no "dead lock" scenario where a stale lock persists

### PgBouncer detection

PgBouncer in transaction-pooling mode assigns different backend connections per transaction, making session-level advisory locks meaningless. dbwarden detects this by calling `pg_backend_pid()` twice and comparing results:

```python
# If the PID changes between calls, we're behind a pooler
pid1 = conn.execute(text("SELECT pg_backend_pid()")).scalar()
pid2 = conn.execute(text("SELECT pg_backend_pid()")).scalar()
if pid1 != pid2:
    raise LockError("Transaction-pooling proxy detected")
```

### Replica detection

dbwarden refuses to run migrations on read replicas (detected via `pg_is_in_recovery()`). Migrations must target the primary.

### Prepared transactions prohibition

Prepared transactions (2PC) interact badly with advisory locks. If a prepared transaction is open, dbwarden refuses to acquire the lock.

### Re-entrancy protection

If the same namespace is already locked on the same connection, dbwarden refuses to acquire a nested lock. This prevents deadlocks from nested migration calls.

## Canonicalization behaviors

dbwarden normalizes several PostgreSQL constructs during diffing to avoid spurious changes. Understanding these prevents confusion when the generated DDL differs from what you wrote.

### CHECK expression cast stripping

PostgreSQL stores CHECK constraints with type casts added by the parser:

```sql
-- You wrote:
CHECK (email <> '')
-- PostgreSQL stores:
CHECK (email <> ''::text)
```

Without normalization, every unnamed CHECK constraint would appear changed on every migration run. dbwarden strips `::type` suffixes and normalizes whitespace before comparison.

### FK action normalization

PostgreSQL reports `NO ACTION` for an unspecified FK action. dbwarden normalizes absent values to `NO ACTION` and uppercases the result:

```python
# Model omits on_delete → stored as None → normalized to "NO ACTION"
# Snapshot has "No Action" → normalized to "NO ACTION"
# Both match, no spurious change
```

### Index null ordering

PostgreSQL's default null ordering follows sort direction: `NULLS LAST` is implied by `ASC`, `NULLS FIRST` by `DESC`. Recording the implied value would cause every index to differ from a model that says nothing about sorting, producing spurious drop+recreate cycles.

dbwarden only emits null ordering when it **contradicts** the PG default.

### nextval() suppression for autoincrement

When a column is marked as autoincrement, the `nextval(...)` default in the snapshot is set to `None` during diffing. Without this, every autoincrementing column would appear to have a default change.

### Physical storage tuning preservation

If the model does not mention `pg_storage` or `pg_compression` for a column, dbwarden will **not** emit `SET STORAGE` or `SET COMPRESSION` to reset the column to its type default. This preserves deliberate DBA tuning (e.g., a column set to `MAIN` storage for compression).

To reset storage to the type default, explicitly set `pg_storage` or `pg_compression` in the model.

## Version-gated features

| Feature | PG version | What changes |
|---------|-----------|-------------|
| Fast ADD COLUMN with constant default | 11+ | No table rewrite for non-volatile defaults |
| SET NOT NULL skip via validated CHECK | 12+ | Instant when CHECK proves no NULLs |
| REINDEX CONCURRENTLY | 12+ | Online index rebuild |
| Column compression metadata (`attcompression`) | 14+ | Snapshot extraction includes compression info |
| Extended statistics expressions | 14+ | `pg_get_statisticsobjdef_expressions()` available |
| NULLS NOT DISTINCT on unique constraints | 15+ | Index and constraint null handling |
| DETACH PARTITION CONCURRENTLY | 16+ | Non-blocking partition detach |
| NOT NULL NOT VALID | 18+ | Attach NOT NULL without table scan |
| NOT ENFORCED constraints | 18+ | CHECK/FK defined but not enforced |

## Known limitations

dbwarden does not currently support:

| Limitation | Impact | Workaround |
|-----------|--------|------------|
| **No statement_timeout** | Long-running DDL (e.g., ALTER TYPE) can run indefinitely | Set `statement_timeout` at session level before migration |
| **ALTER TYPE USING is commented** | Generated as `-- USING col::newtype`, requires manual uncomment | Use `--postgres-auto-using` flag or uncomment manually |
| **No automatic NOT VALID/VALIDATE two-step** | User must opt in to the safe pattern | Declare constraints with `not_valid=True` in model, add separate VALIDATE |
| **Generated columns: ALTER TABLE not supported** | Cannot add/modify generated columns after creation | Recreate table |
| **No automatic lock_timeout** | Must be configured via `pg_migration_lock_timeout` | Set it in config |

## Related

- [DDL Behavior](ddl-behavior.md) : Transactional DDL, index creation, column type changes, lock levels
- [Indexes](indexes.md) : Index types, PgIndexSpec, CONCURRENTLY field
- [Constraints](constraints.md) : NOT VALID / VALIDATE, deferrable, NO INHERIT
- [Tables and Columns](tables-and-columns.md) : Column lifecycle, table properties
- [Migration Locking](../../advanced/migration-locking.md) : Advisory lock implementation, PgBouncer detection, heartbeats
- [Migration Safety](migration-safety.md) : SAFE/WARN/CRITICAL classification per object type
