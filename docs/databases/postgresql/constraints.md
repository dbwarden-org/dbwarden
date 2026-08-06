# Constraints

Constraints are handled by `ConstraintHandler` during the DIFF phase.

## Foreign Keys

FK comparison uses a signature tuple: `(columns, ref_table, ref_columns, on_delete, on_update, deferrable, match)`.

### MATCH Behaviour

PostgreSQL supports three FK match modes: `MATCH FULL`, `MATCH PARTIAL`, and `MATCH SIMPLE` (default).

- `MATCH FULL` is explicitly emitted in DDL
- `MATCH PARTIAL` is explicitly emitted
- `MATCH SIMPLE` and absent match are canonicalized to the same representation (no match clause) to avoid churn on existing FKs

```sql
ALTER TABLE t ADD CONSTRAINT fk FOREIGN KEY (ref_id) REFERENCES ref (id) MATCH FULL;
ALTER TABLE t ADD CONSTRAINT fk FOREIGN KEY (ref_id) REFERENCES ref (id) ON DELETE CASCADE;
```

`MATCH PARTIAL` is defined by the SQL standard but PostgreSQL implements it identically to `MATCH SIMPLE`. Both allow some columns in a multi-column FK to be NULL; `MATCH FULL` requires all columns to be NULL or all to be non-NULL.

### ON DELETE / ON UPDATE

Supported actions: `CASCADE`, `SET NULL`, `SET DEFAULT`, `RESTRICT`, `NO ACTION`.

`NO ACTION` is the default. Unlike `RESTRICT`, `NO ACTION` allows deferrable constraints to defer checking to transaction end.

### DEFERRABLE

Support for `DEFERRABLE` / `NOT DEFERRABLE` and `INITIALLY DEFERRED` / `INITIALLY IMMEDIATE`.

A deferrable FK does not check referential integrity at statement end; checking is deferred to transaction end. Use `SET CONSTRAINTS ALL DEFERRED` at the start of a transaction to restore deferred checking.

```sql
BEGIN;
SET CONSTRAINTS all_fks DEFERRED;
-- Can now delete referenced rows before updating referencing rows
DELETE FROM orders WHERE id = 1;
UPDATE order_items SET order_id = 2 WHERE order_id = 1;
COMMIT; -- Constraints checked here
```

### NOT VALID / VALIDATE

FKs can be created `NOT VALID` and validated later with `VALIDATE CONSTRAINT`. This allows adding an FK to a live table without locking it for a full table scan during creation.

```sql
ALTER TABLE t ADD CONSTRAINT fk FOREIGN KEY (ref_id) REFERENCES ref (id) NOT VALID;
-- later, during low-traffic window:
ALTER TABLE t VALIDATE CONSTRAINT fk;
```

### ALTER ALTER CONSTRAINT

PostgreSQL 9.4+ supports modifying constraint deferrability without drop/create:

```sql
ALTER TABLE t ALTER CONSTRAINT fk DEFERRABLE INITIALLY DEFERRED;
```

DBWarden detects when only the deferrability changed and emits `ALTER CONSTRAINT` instead of a drop+add cycle.

## Unique Constraints

Support for:

- `DEFERRABLE` / `INITIALLY DEFERRED`
- `INCLUDE` columns
- `NULLS NOT DISTINCT` (PG 15+)

A unique constraint with `NULLS NOT DISTINCT` treats NULLs as equal, so only one row can contain NULL.
If only the constraint name changes, DBWarden emits `ALTER TABLE ... RENAME CONSTRAINT ...` instead of dropping and recreating the unique constraint.

## Primary Key Constraints

Declared via SQLAlchemy's `primary_key=True` on `mapped_column`. The PK constraint is managed automatically:

| Operation | DDL |
|-----------|-----|
| Create | `PRIMARY KEY (col1, col2)` inline in `CREATE TABLE` |
| Add | `ALTER TABLE t ADD PRIMARY KEY (col1)` |
| Drop | `ALTER TABLE t DROP CONSTRAINT t_pkey` |

PK constraint options (set via the column/s in SQLAlchemy):

| Option | SQL | Notes |
|--------|-----|-------|
| Tablespace | `USING INDEX TABLESPACE name` | Separates PK index from table storage |
| Deferrable | `DEFERRABLE` / `INITIALLY DEFERRED` | PK can be deferred (rare) |

## Check Constraints

Support for:

- Arbitrary CHECK expressions
- `NO INHERIT` (constraint not applied to child tables)
- `NOT VALID` + `VALIDATE`

### NO INHERIT Example

```python
class Meta(PGTableMeta):
    pg_checks = [
        {"name": "ck_users_type", "sql": "user_type IN ('admin', 'user')", "no_inherit": True},
    ]
```

```sql
ALTER TABLE users ADD CONSTRAINT ck_users_type CHECK (user_type IN ('admin', 'user')) NO INHERIT;
```

`NO INHERIT` is useful when a parent table has a constraint that should not apply to child tables.

## Exclude Constraints

Declared via `pg_excludes` on `PGTableMeta`. Uses `PgTableHandler`.

```python
class Meta(PGTableMeta):
    pg_excludes = [
        {"name": "excl_room_booking", "expression": "USING gist (room_id WITH =, during WITH &&)"},
    ]
```

Exclusion constraints require a GiST or SP-GiST index. They prevent any two rows from having overlapping values in the specified columns. Common use cases: time-range booking, geo-spatial exclusion.

Diffing compares the full expression; any change in operator, access method, or columns produces a drop+add cycle.

## UniqueSpec vs unique=True on PgIndexSpec

PostgreSQL implements unique constraints as unique indexes under the hood. DBWarden supports both approaches, and the choice is a semantic one.

| Aspect | `UniqueSpec` in `uniques` / `pg_uniques` | `unique=True` on `PgIndexSpec` in `pg_indexes` |
|--------|------------------------------------------|------------------------------------------------|
| SQL generated | `ALTER TABLE ... ADD CONSTRAINT ... UNIQUE (...)` | `CREATE UNIQUE INDEX ... ON ... (...)` |
| Shows in `information_schema.table_constraints` | Yes | No |
| Targetable by FK references | Yes | No (needs the unique index to also be a constraint) |
| Business meaning | A constraint: a rule the data must satisfy | An index: a data structure for performance |

### When to use each

**Use `UniqueSpec`** when the uniqueness is a business rule:

```python
from dbwarden.databases import UniqueSpec

class Meta(PGTableMeta):
    uniques = [
        UniqueSpec(name="uq_users_email", columns=["email"]),
    ]
```

This generates an `ALTER TABLE` constraint. It shows in `information_schema.table_constraints` and can be referenced by foreign keys.

**Use `unique=True` on `PgIndexSpec`** when the uniqueness is a performance concern:

```python
from dbwarden.databases.pgsql import PgIndexSpec

class Meta(PGTableMeta):
    pg_indexes = [
        PgIndexSpec("ix_users_email", ["email"], unique=True),
    ]
```

This generates a `CREATE UNIQUE INDEX`. It enforces uniqueness identically but does not appear in the SQL standard constraint views and cannot be targeted by FK `REFERENCES`.

### Practical guidance

For a business rule like "no two users can share the same username within a branch":

```python
class Meta(PGTableMeta):
    uniques = [
        UniqueSpec(name="uq_user_branch_username",
                   columns=["branch_id", "username"]),
    ]
```

A constraint is semantically correct here: it declares a rule the data must satisfy. It also enables future FK references and works with `ON CONFLICT` in the same way as a unique index.

See [Indexes](indexes.md) for `PgIndexSpec` options.

## Constraint Diffing

Constraints are compared by full attribute content. Any difference in signature (columns, expression, options) produces `DROP` + `ADD`. Constraint name changes are detected as a new constraint.
