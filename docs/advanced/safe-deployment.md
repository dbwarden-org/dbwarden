---
description: 'Deploy database schema changes safely with dbwarden: pre-flight checks, impact
  analysis, sandbox validation, migration locking, rollback planning, and CI/CD integration
  patterns.'
---

# Safe Deployment

How to deploy schema changes with minimal risk and a clear recovery path.

## Pre-flight checklist

Before running migrations in production:

- [ ] Confirm no other migration is running (`dbwarden lock-status`)
- [ ] Review pending migrations (`dbwarden status`)
- [ ] Confirm migrations have been tested in staging
- [ ] Take a backup if your database does not have point-in-time recovery

## Standard deploy sequence

```bash
# 1. Verify lock is free
$ dbwarden lock-status --database primary

# 2. Check what will run
$ dbwarden status --database primary

# 3. Apply with backup
$ dbwarden migrate --database primary --with-backup --backup-dir ./backups

# 4. Confirm clean state post-migration
$ dbwarden status --database primary
$ dbwarden history --database primary
```

For multi-database deployments:

```bash
$ dbwarden migrate --all --with-backup --backup-dir ./backups
```

## What happens when a migration fails mid-run

### PostgreSQL (transactional DDL)

PostgreSQL wraps DDL in transactions. If a migration file fails partway through, the entire file is rolled back. The migration remains in "pending" state. The lock is released. You can safely fix the SQL and retry.

```bash
# After a failed migration:
$ dbwarden status --database primary     # confirm migration is still pending
$ dbwarden lock-status --database primary # confirm lock was released

# Fix the migration file, then:
$ dbwarden migrate --database primary
```

### MySQL / databases without transactional DDL

DDL cannot be rolled back. A failed migration may have partially applied changes (e.g., a table was created but the index was not). Manual inspection is required before retrying.

```bash
# Check what the migration was supposed to do
cat migrations/primary/V__0012_add_payment_tables.sql

# Inspect current schema state via your database client
# Determine what was applied and what was not

# Either:
# a) Manually apply the remaining SQL
# b) Create a corrective migration
# c) Roll back manually and retry from scratch
```

## Recovery: stuck lock

If a migration process was killed and the lock was not released:

```bash
# 1. Confirm no migration process is running
$ dbwarden lock-status --database primary

# 2. Inspect history to see the last applied migration
$ dbwarden history --database primary

# 3. Inspect pending state
$ dbwarden status --database primary

# 4. Only if the process is confirmed dead:
$ dbwarden unlock --database primary

# 5. Retry
$ dbwarden migrate --database primary
```

See [Migration Locking](migration-locking.md) for full lock recovery guidance.

## Recovery: failed migration, data is wrong

If a migration applied successfully but produced incorrect data or schema:

**Option A: Rollback** (if the migration has a `-- rollback` section):

```bash
$ dbwarden rollback --database primary
```

This executes the rollback SQL defined in the migration file. Verify the rollback SQL was written when the migration was created; not all migrations include one.

**Option B: Forward fix** (preferred for data migrations):

```bash
# Create a corrective migration
$ dbwarden new "fix column type on payments" --database primary
# Edit the generated file with the corrective SQL
$ dbwarden migrate --database primary
```

Forward fixes are safer than rollbacks for data migrations, as rollback SQL is harder to write correctly after the fact.

## Baseline migrations

For databases that already have a schema (migrating from another tool or brownfield setup):

```bash
$ dbwarden migrate --database primary --baseline --to-version 0005
```

`--baseline` marks migrations as applied without executing them. Use this to tell dbwarden "this database already has schema up to version 0005."

## Smoke test after deploy

After migrations complete, run a quick connectivity and schema check:

```bash
$ dbwarden check-db --database primary
```

`check-db` inspects the live database schema and reports what tables and columns exist. Use this to confirm the schema matches what your application expects.

See also: [Migration Locking](migration-locking.md) | [CI/CD Patterns](ci-cd-patterns.md) | [`rollback` command](../commands/rollback.md) | [Cookbook: Safety & Impact](../cookbook/06-safety-impact.md)
