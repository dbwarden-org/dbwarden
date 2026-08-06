# Migration File Format

Migration files are the execution contract in DBWarden.

Everything that changes your database should be represented in explicit SQL files that can be reviewed, tested, and rolled back.

## File naming and location

Versioned migrations are stored under each database migrations directory (default: `migrations/<database_name>`).

Canonical filename pattern:

```text
{database_name}__{version}_{description}.sql
```

A legacy format without the `{database_name}__` prefix (e.g. `0001_create_users_table.sql`) is also accepted for backward compatibility.

Examples:

```text
primary__0001_initial_schema.sql
primary__0002_add_users_table.sql
analytics__0001_create_events.sql
```

When a migration is auto-generated with `make-migrations`, DBWarden also writes a companion plan file:

```text
primary__0001_initial_schema.plan.json
```

That file captures machine-readable metadata for CI and debugging and is not executed by `migrate`.

## Required sections

Each migration file must define both:

```sql
-- upgrade

-- rollback
```

- `-- upgrade`: statements applied during `migrate`
- `-- rollback`: statements applied during `rollback`

If rollback is weak or incomplete, production recovery is weak or incomplete.

## Rollback contract

Generated rollback is part of the migration contract. DBWarden classifies rollback before a migration is accepted:

| Kind | Meaning | Generation behavior |
|------|---------|---------------------|
| `real` | Executable rollback SQL restores the prior schema state for the operation. | Allowed. |
| `conditional` | Executable rollback SQL is allowed only when prior state is captured and DBWarden can prove the inverse is structurally safe. | Allowed with verbose warning. |
| `irreversible` | Rollback is genuinely unsafe or impossible, such as lossy ClickHouse engine changes or PostgreSQL enum value additions. | Allowed only for known irreversible operations or explicit acknowledgement. |
| `placeholder` | Rollback is only a comment or manual instruction. | Refused by default. |

Placeholder rollback is not a working rollback. If DBWarden cannot emit executable rollback SQL, generation fails unless the migration is intentionally declared irreversible.

Explicit irreversible declaration:

```sql
-- dbwarden: irreversible
```

Use this only when the team accepts that the migration cannot be rolled back automatically. See [Rollback Coverage](correctness/rollback-coverage-matrix.md) for the backend operation matrix.

## Migration classes

DBWarden supports three execution classes:

| Prefix | Class | Behavior |
|--------|-------|----------|
| `NNNN_` | Versioned | Runs once in ordered version sequence |
| `RA__` | Runs always | Runs on every `migrate` execution |
| `ROC__` | Runs on change | Runs when checksum changed |

### When to use each

- `NNNN_`: schema evolution (tables, columns, indexes, constraints)
- `RA__`: objects that should always be refreshed (views, grants)
- `ROC__`: routines/policies that should apply only when content changes

## Execution model

At runtime, DBWarden builds an execution plan from file discovery + migration metadata:

1. read versioned files and filter already-applied versions
2. include `RA__` files
3. include changed `ROC__` files
4. execute with lock protection
5. record metadata and checksums

Conceptual plan:

```python
def build_plan(directory, applied_versions):
    versioned = parse_versioned_files(directory)
    repeatable = parse_repeatable_files(directory)
    pending_versioned = [m for m in versioned if m.version not in applied_versions]
    pending_ra = repeatable.runs_always
    pending_roc = changed_only(repeatable.runs_on_change)
    return pending_versioned + pending_ra + pending_roc
```

## Examples

### Versioned migration

```sql
-- upgrade

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    created_at DATETIME
);

-- rollback

DROP TABLE users;
```

### Runs-always migration (`RA__`)

Filename example: `primary__RA__refresh_active_users_view.sql`

```sql
-- upgrade

CREATE OR REPLACE VIEW active_users AS
SELECT id, email FROM users WHERE is_active = TRUE;

-- rollback

DROP VIEW IF EXISTS active_users;
```

### Runs-on-change migration (`ROC__`)

Filename example: `primary__ROC__update_timestamp_trigger.sql`

```sql
-- upgrade

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- rollback

DROP FUNCTION IF EXISTS update_updated_at();
```

## Metadata headers

Headers are parsed from migration file comments. The `-- seed` marker is recognised
by tools; `-- depends_on` parsing is implemented but **not yet enforced** during
migration execution (migrations run in filesystem-sort order by version).

Dependency header (parsed but not enforced):

```sql
-- depends_on: ["0004", "0005"]
```

Seed marker:

```sql
-- seed
```

## Authoring guidelines

- One logical change per migration file
- Keep DDL explicit; avoid hidden application-side schema effects
- Keep rollback idempotent when possible (`IF EXISTS`, safe predicates)
- Do not commit placeholder rollback. Use executable rollback SQL or an explicit irreversible declaration.
- For data migrations, use bounded, reversible operations
- Prefer small migrations over large monolithic SQL scripts

## Review checklist

Before merge:

- upgrade section matches intended schema change
- rollback section restores prior valid state
- indexes/constraints/defaults are explicit
- no environment-specific literals accidentally committed

Before release:

```bash
$ dbwarden status --database primary
$ dbwarden migrate --database primary
$ dbwarden rollback --database primary --count 1
$ dbwarden migrate --database primary
```
