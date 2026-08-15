# Rollback Generation

dbwarden generates rollback SQL at the same time it generates upgrade SQL. Rollback is not a separate best-effort file that developers maintain by hand after the fact.

The current rollback system uses a strict contract:

- Executable rollback SQL is accepted.
- Conditional rollback is accepted only when dbwarden has captured enough prior state.
- Irreversible operations must be explicitly acknowledged.
- Placeholder rollback is refused by default.

See the [Rollback Coverage Matrix](rollback-coverage-matrix.md) for backend-specific operation coverage.

## Core Principle

For every upgrade operation, the diff engine computes the reverse operation at the same time.

```text
Diff detects model change
  |
  v
Upgrade operation + rollback operation
  |
  v
SQL generation
  |
  v
Migration file with upgrade and rollback sections
```

This keeps upgrade and rollback logic synchronized. The same handler that knows how to create an object also knows what information is needed to remove or restore it.

## Symmetric Pairs

Many operations have direct structural inverses.

| Upgrade | Rollback |
|---------|----------|
| `CREATE TABLE` | `DROP TABLE` |
| `ADD COLUMN` | `DROP COLUMN` |
| `CREATE INDEX` | `DROP INDEX` |
| `ADD CONSTRAINT` | `DROP CONSTRAINT` |
| `CREATE POLICY` | `DROP POLICY` |
| `CREATE ROLE` | `DROP ROLE` |

Example:

```sql
-- upgrade
ALTER TABLE users ADD COLUMN display_name VARCHAR(255);

-- rollback
ALTER TABLE users DROP COLUMN display_name;
```

This rollback removes the column introduced by the upgrade. If the upgrade is applied and then rolled back immediately, the table shape returns to its previous form.

## Conditional Rollback

Some operations are reversible only if dbwarden captured previous state.

Example: changing a PostgreSQL column default.

```sql
-- upgrade
ALTER TABLE users ALTER COLUMN status SET DEFAULT 'active';

-- rollback
ALTER TABLE users ALTER COLUMN status SET DEFAULT 'pending';
```

The rollback is correct only if dbwarden knows the previous default was `'pending'`. If the previous default was unknown, dbwarden must not invent one.

Conditional rollback appears in operations such as:

- Restoring prior PostgreSQL role attributes.
- Restoring prior PostgreSQL policy definitions.
- Restoring prior ClickHouse RBAC object state.
- Reversing a ClickHouse table recreate only when the engine transition is classified as row-preserving and the prior definition is available.

## Irreversible Operations

Some operations cannot be automatically reversed in a way that restores data.

Example:

```sql
-- upgrade
ALTER TABLE users DROP COLUMN legacy_code;
```

dbwarden can add `legacy_code` back if it knows the column definition, but it cannot reconstruct the deleted values. That means a structural rollback is not the same as data recovery.

For generated migrations, dbwarden refuses placeholder rollback by default. If a migration is intentionally irreversible, it must be explicit.

Use the committed migration annotation:

```sql
-- dbwarden: irreversible
```

That annotation tells reviewers and automation that the migration is intentionally not automatically reversible.

## Rollback Ordering

Rollback statements run in the reverse of upgrade order.

Upgrade order:

```text
1. Add column
2. Create index on the new column
3. Add constraint that uses the new column
```

Rollback order:

```text
1. Drop constraint
2. Drop index
3. Drop column
```

This matters because dependencies point in the opposite direction during rollback. You cannot drop a column while an index or constraint still depends on it.

## Complex Example

Model change:

- Add `display_name` to `users`.
- Drop an old index.
- Add a new uniqueness constraint.

Generated migration:

```sql
-- upgrade
ALTER TABLE users ADD COLUMN display_name VARCHAR(255);

DROP INDEX IF EXISTS idx_users_name;

ALTER TABLE users ADD CONSTRAINT uq_users_display_name UNIQUE (display_name);

-- rollback
ALTER TABLE users DROP CONSTRAINT IF EXISTS uq_users_display_name;

CREATE INDEX idx_users_name ON users (name);

ALTER TABLE users DROP COLUMN display_name;
```

Why this rollback order is correct:

1. The constraint must be removed before the column can be dropped.
2. The old index is recreated before the rollback finishes because it existed before the upgrade.
3. The added column is removed last after dependencies are gone.

## Manual SQL and Data Changes

dbwarden can generate rollback for schema operations it understands. It cannot infer the inverse of arbitrary manual data changes.

Example:

```sql
-- upgrade
UPDATE users SET status = 'active' WHERE status IS NULL;
```

The inverse is not generally knowable. Which rows were originally `NULL`? Which rows were already `active`? Without captured data, rollback cannot answer that.

For manual data changes, write a manual rollback section or explicitly mark the migration irreversible if rollback cannot be made correct.

## Rollback Convergence Pattern

Teams that want stronger rollback assurance can add a rollback convergence job:

```text
Empty database
  |
  v
Apply all migrations
  |
  v
Rollback all versioned migrations
  |
  v
Verify expected empty or baseline schema
```

This does not prove that dropped data can be recovered. It proves that rollback SQL is syntactically valid and structurally consistent for the schema objects under test.

## Why Rollbacks Should Not Be Hand-Maintained

Hand-written rollback sections drift. A developer changes an upgrade statement and forgets to update the rollback. A reviewer checks the upgrade and misses the reverse path.

dbwarden avoids that by computing both directions from the same operation model. When rollback cannot be computed safely, the generator refuses placeholder rollback rather than producing a misleading comment.

The result is stricter than convenience-oriented migration tools: either dbwarden emits executable rollback SQL, or the migration explicitly declares why automatic rollback is not available.
