# `make-rollback`

Generate a rollback SQL file for a given migration file.

## Usage

```bash
$ dbwarden make-rollback migrations/primary__0005_add_table.sql
```

## Arguments

- `MIGRATION_FILE` (required) - Path to the migration SQL file

## Output

Creates a `.rollback.sql` file next to the given migration file with auto-generated rollback statements.

## Supported reverse transformations

| Upgrade Pattern | Generated Rollback |
|----------------|-------------------|
| `CREATE TABLE t (...)` | `DROP TABLE IF EXISTS t;` |
| `CREATE MATERIALIZED VIEW v AS ...` | `DROP VIEW IF EXISTS v;` |
| `CREATE DICTIONARY d (...)` | `DROP DICTIONARY IF EXISTS d;` |
| `ALTER TABLE t ADD COLUMN c ...` | `ALTER TABLE t DROP COLUMN c;` |
| `CREATE INDEX i ON t (...)` | `DROP INDEX IF EXISTS i;` |
| `CREATE UNIQUE INDEX i ON t (...)` | `DROP INDEX IF EXISTS i;` |
| Other patterns | Refused unless the migration is explicitly irreversible |

## Irreversible annotation

If the command cannot derive executable rollback SQL, it refuses to create a placeholder rollback file. To acknowledge that a migration cannot be rolled back automatically, add this comment to the migration file:

```sql
-- dbwarden: irreversible
```

With that annotation, `make-rollback` may create a rollback file that contains a clear comment instead of executable SQL. This is an intentional declaration, not a successful rollback.

## Notes

- Generated rollback is conservative and may not handle all edge cases.
- Always review the generated rollback before using it.
- For best results, write executable rollback SQL in the `-- rollback` section of the original migration.
- Do not commit placeholder rollback unless the migration is explicitly declared irreversible.
