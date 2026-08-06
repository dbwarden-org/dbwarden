# Checksum Integrity

DBWarden stores a SHA-256 checksum of each migration file at apply time. On subsequent runs, it recalculates the checksum and compares. A mismatch means the file changed after it was applied.

## What checksums protect against

- Accidentally editing an applied migration file
- Copy-paste errors that silently modify historical SQL
- Merge conflicts that land inside already-applied migration files
- Tooling (formatters, editors) modifying migration files in place

## What triggers a mismatch error

```
ChecksumMismatchError: Migration '0004_add_indexes' checksum has changed.
  stored:   a3f8c2e1...
  current:  7b91d40c...
  database: primary

The migration file was modified after it was applied.
Use 'dbwarden history --database primary' to inspect applied migrations.
```

This error blocks `migrate` and `status` from running. DBWarden will not proceed while a checksum is inconsistent.

## Repeatable migrations

Migrations prefixed `RA__` (repeatable, always) or `ROC__` (repeatable on change) behave differently: they are designed to be re-applied. Checksum changes on `ROC__` files trigger re-application on the next `migrate` run. This is expected behavior, not an error.

Checksum mismatch errors only apply to versioned migrations (`V__` prefix).

## Diagnosing a mismatch

```bash
# See which migrations are applied and their checksums
$ dbwarden history --database primary

# Check the current status
$ dbwarden status --database primary
```

Common causes:

1. **Editor auto-format**: your editor reformatted whitespace in the file
2. **Merge conflict**: conflict markers were added/removed inside a migration file
3. **Intentional edit**: someone changed the migration to fix a typo or add a comment

## Resolution: dev environment

In development where the migration has not been applied to shared data:

1. Reset the database state and re-run migrations from scratch, **or**
2. If the change is trivial (whitespace, comment), revert the file to its original state:

```bash
git diff migrations/primary/V__0004_add_indexes.sql
git checkout migrations/primary/V__0004_add_indexes.sql
```

After reverting, the stored checksum and file checksum will match again.

## Resolution: production environment

In production, never modify applied migration files. The resolution is:

1. **Revert the file** to its exact applied state (use git history)
2. Create a **new migration** for any schema changes you need to make

If the file change was accidental and the schema is correct, reverting the file is safe; no data or schema change occurs.

If the file was intentionally changed to fix an error in a migration that was already applied in production, the database schema may already reflect the original (wrong) SQL. Coordinate carefully:

```bash
# 1. Revert the migration file to what was actually applied
git checkout <commit-before-edit> -- migrations/primary/V__0004_add_indexes.sql

# 2. Verify status is clean
$ dbwarden status --database primary

# 3. Create a corrective migration for the actual schema fix
$ dbwarden new "fix index on users" --database primary
```

## When is it safe to ignore?

Never. A checksum mismatch means recorded history diverges from what is on disk. Even if the change appears harmless, proceeding without resolving the mismatch means your migration history is untrustworthy.

## Schema snapshot checksums

DBWarden also writes a **schema snapshot** after each migration:
a JSON file at `.dbwarden/schemas/<migration_id>.schema.json`. Each
snapshot contains a `checksum` field computed from the full snapshot
content via SHA-256, plus a `previous_checksum` field linking it to
the prior snapshot:

```json
{
  "tables": { ... },
  "checksum": "abc123...",
  "previous_checksum": "def456..."
}
```

Snapshots are written atomically (write to temp file, verify, rename)
and read with integrity validation; if the file content doesn't match
the stored checksum, the snapshot is rejected.

The snapshot checksum chain serves a different purpose from the
migration checksum. A migration checksum tells you a specific SQL
file hasn't changed. A snapshot checksum tells you the full schema
state at a given point is intact. If a snapshot is corrupted or
manually edited, `find_latest_snapshot()` falls back to the previous
intact snapshot.

## Preventing mismatches

- Treat versioned migration files as immutable once applied to any shared environment
- Configure your editor to exclude `migrations/` from auto-format
- Use a pre-commit hook to detect changes to applied migration files:

```bash
# .git/hooks/pre-commit (example, adapt to your setup)
$ dbwarden check --database primary
```

`dbwarden check` compares models to the live schema. While not a direct checksum check, it surfaces drift that often accompanies unintended migration file edits.

See also: [Migration Locking](migration-locking.md) | [Migration Files](../migration-files.md) | [Cookbook: Schema Inspection](../cookbook/05-schema-inspection.md)
