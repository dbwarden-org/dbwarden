# 3. Applying and Inspecting Migrations

## What You'll Learn

- How `dbwarden migrate` applies pending SQL
- How to roll back and downgrade to specific versions
- How to inspect migration history and status
- How to validate schema integrity and database connectivity

## Prerequisites

- Completed [Section 2](02-models-and-migrations.md) (migration file exists)
- `examples/core/` project

## Step 1: Apply Migrations

```bash
cd examples/core
bash scripts/03-apply-inspect.sh
```

### The Migrate Command

```bash
$ dbwarden migrate --database primary
```

When you run `migrate`, DBWarden:

1. Creates the metadata table (`_dbwarden_migrations`) if it doesn't exist
2. Creates the lock table (`_dbwarden_lock`) if it doesn't exist
3. Acquires a migration lock (prevents concurrent runs)
4. Reads all migration files and filters to pending (unapplied) ones
5. Executes the `-- upgrade` SQL of each pending migration
6. Records each migration's version, checksum, and timestamp
7. Writes a schema snapshot file for future diffs
8. Releases the lock

```
[DBWarden] Applying primary__0001_create_core_tables...
[DBWarden] Migration applied successfully (42ms)
[DBWarden] All migrations applied. Pending: 0
```

### Verify Status

```bash
$ dbwarden status --database primary
```

Output:

```
Database: primary
  Applied:  1
  Pending:  0
  Status:   up-to-date
```

### View History

```bash
$ dbwarden history --database primary
```

Output:

```
 Migration History (primary)
  V0001  create_core_tables  2025-01-15 10:30:00  a1b2c3d4...
```

The checksum (`a1b2c3d4...`) is a SHA-256 hash of the migration file content. This detects tampering or accidental edits after apply.

## Step 2: Rollback

```bash
$ dbwarden rollback --database primary --count 1
```

Rollback executes the `-- rollback` section of the most recently applied migration. After rollback:

```
[DBWarden] Rolling back primary__0001_create_core_tables...
[DBWarden] Rollback complete
```

```bash
$ dbwarden status --database primary
```

```
Database: primary
  Applied:  0
  Pending:  1
  Status:   pending
```

### Rollback Mechanics

- Rollback always executes the `-- rollback` section of the file; never auto-generates reverse SQL
- `--count` controls how many migrations to roll back (default: 1)
- Rollbacks are also lock-protected
- After rollback, the migration is considered "pending" again and can be re-applied

## Step 3: Re-apply

```bash
$ dbwarden migrate --database primary
```

Re-applies the migration. Since rollback removed the tracking record, the migration runs again.

## Step 4: Downgrade to a Version

```bash
$ dbwarden downgrade --to 0000 --database primary
```

`downgrade` is a bulk rollback: it rolls back all migrations down to (but not including) the target version. `--to 0000` rolls back everything.

```
[DBWarden] Rolling back primary__0001_create_core_tables...
[DBWarden] Downgrade complete. At version: 0000
```

### migrate vs rollback vs downgrade

| Command | What it does | Safe to run twice? |
|---------|-------------|-------------------|
| `migrate` | Applies pending migrations | Yes (idempotent) |
| `rollback` | Reverses the last N applied migrations | Yes (tracks what's applied) |
| `downgrade` | Rolls back to a specific target version | Yes |

## Step 5: Re-apply All

```bash
$ dbwarden migrate --database primary
$ dbwarden status --database primary
```

After the final apply, status should show:

```
Database: primary
  Applied:  1
  Pending:  0
  Status:   up-to-date
```

## Step 6: Schema Validation

```bash
$ dbwarden check --database primary
```

`check` scans each migration file and classifies operations by safety level:

- **SAFE**: Adding a nullable column, creating an index
- **INFO**: Table comment changes
- **WARN**: Dropping a default, changing column type
- **CRITICAL**: Dropping a table or column, removing a NOT NULL

```
Checking migrations for 'primary'...
  primary__0001_create_core_tables:
    CREATE TABLE users           SAFE
    CREATE TABLE posts           SAFE
    CREATE TABLE products        SAFE
    CREATE TABLE tags            SAFE
    CREATE INDEX                 SAFE
    COMMENT ON TABLE             INFO
  Result: 5 SAFE, 1 INFO, 0 WARN, 0 CRITICAL
```

## Step 7: Database Connectivity Check

```bash
$ dbwarden check-db --database primary
```

`check-db` connects to the live database and reports its schema:

```
Database: primary
  Connection: OK
  Tables:
    users (6 columns)
    posts (5 columns)
    products (6 columns)
    tags (2 columns)
  Migration table: _dbwarden_migrations (present)
  Lock table: _dbwarden_lock (present)
```

This confirms the database is reachable and has the expected schema.

## Key Takeaways

- `migrate` applies pending SQL with lock protection and checksum recording
- `rollback` and `downgrade` give you precise control over reversal
- `status` and `history` are your windows into migration state
- `check` classifies each operation by safety before it runs
- `check-db` validates database connectivity and schema existence

## Related Documentation

- [`migrate` command](../commands/migrate.md)
- [`rollback` command](../commands/rollback.md)
- [`downgrade` command](../commands/downgrade.md)
- [`status` command](../commands/status.md)
- [`history` command](../commands/history.md)
- [`check` command](../commands/check.md)
- [`check-db` command](../commands/check-db.md)

## Next

[Section 4: Offline & CI Workflows](04-offline-ci.md)
