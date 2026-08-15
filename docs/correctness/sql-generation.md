# SQL Generation

SQL generation is the stage where dbwarden turns a typed diff into a plain SQL migration file. The generated file contains backend-native SQL and is meant to be reviewed by humans.

There is no hidden runtime library inside the migration. Once generated, the migration is SQL.

## Pipeline

```text
Canonical model state + canonical snapshot state
  |
  v
Diff
  |
  v
Typed operations
  |
  v
Statement ordering
  |
  v
Backend ObjectHandler.emit()
  |
  v
MigrationStatement objects
  |
  v
Plain .sql migration file
```

Each stage has a narrow job.

## 1. Diff to Typed Operations

The diff engine does not start by writing SQL strings. It creates typed operations such as:

```text
create_table
drop_table
add_column
drop_column
alter_column_type
alter_column_nullable
add_index
drop_index
alter_ch_options
recreate_ch_table
```

Typed operations make safety checks, ordering, rollback generation, and backend dispatch possible.

Example operation shape:

```json
{
  "type": "add_column",
  "table": "users",
  "column": "display_name",
  "definition": {
    "type": "VARCHAR(255)",
    "nullable": true
  }
}
```

The exact internal representation may include backend-specific metadata, but the key idea is that the operation is structured before it becomes SQL.

## 2. Operation Ordering

Not every valid operation order is safe. dbwarden uses statement ordering to make generated migrations executable.

Examples:

- Create schemas before creating tables inside them.
- Rename tables before applying column changes to the renamed table.
- Create tables before creating indexes on those tables.
- Drop dependent objects before dropping objects they depend on.
- Reverse upgrade order for rollback statements.

The ordering layer uses `StatementOrder` and backend handler constraints so SQL is emitted in a stable sequence.

## 3. Backend Dispatch

Each backend owns SQL rendering for its supported object types. The dispatch step sends operations to the relevant `ObjectHandler.emit()` implementation.

```text
Operation: add_column
  |
  +--> PostgreSQL ColumnHandler.emit()
  |
  +--> MySQL column renderer
  |
  +--> SQLite-compatible renderer
  |
  +--> ClickHouse ChColumnHandler.emit()
```

This is why the same model can produce different SQL for different database configurations.

## PostgreSQL Example: Identity Column

A PostgreSQL model can declare identity behavior through backend metadata:

```python
class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    class Meta(PGTableMeta):
        class id(PGColumnMeta):
            pg = pg.field(identity="always", identity_start=1000)
```

The PostgreSQL emitter understands that this is not a generic integer column. It must render PostgreSQL-native identity syntax:

```sql
CREATE TABLE accounts (
    id BIGINT GENERATED ALWAYS AS IDENTITY (START WITH 1000) PRIMARY KEY
);
```

Depending on the model metadata and backend version, PostgreSQL auto-increment behavior may use identity syntax or sequence-backed behavior. The important correctness property is that the PostgreSQL handler owns that decision. It is not guessed by a generic string formatter.

## PostgreSQL Example: Add a Column

Model change:

```python
display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

Typed operation:

```text
add_column users.display_name varchar(255) nullable
```

Generated SQL:

```sql
-- upgrade
ALTER TABLE users ADD COLUMN display_name VARCHAR(255);

-- rollback
ALTER TABLE users DROP COLUMN display_name;
```

The output is intentionally simple. Reviewers can see exactly what will run.

## ClickHouse Example: MergeTree Table

A ClickHouse model can declare engine metadata:

```python
class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_date: Mapped[date] = mapped_column()
    user_id: Mapped[int] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=merge_tree(),
            order_by=["event_date", "id"],
            partition_by="toYYYYMM(event_date)",
            settings={"index_granularity": "8192"},
        )
```

The ClickHouse emitter writes native engine syntax:

```sql
CREATE TABLE IF NOT EXISTS events (
    id UInt64,
    event_date Date,
    user_id UInt64
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(event_date)
ORDER BY (event_date, id)
SETTINGS index_granularity = 8192;
```

This is not a generic SQL dialect. ClickHouse engine clauses, settings, projections, skip indexes, and materialized view syntax are rendered by ClickHouse-specific handlers.

## Example Migration File

A generated migration contains both upgrade and rollback sections:

```sql
-- migration: primary__0007_add_user_display_name.sql

-- upgrade
ALTER TABLE users ADD COLUMN display_name VARCHAR(255);

CREATE INDEX idx_users_display_name ON users (display_name);

-- rollback
DROP INDEX IF EXISTS idx_users_display_name;

ALTER TABLE users DROP COLUMN display_name;
```

This file is self-contained. Applying it does not require importing dbwarden code inside the database. dbwarden is the generator and executor. The migration artifact is plain SQL.

## Why Emitters Are Trustworthy

The trust comes from separation and tests:

- Canonicalization decides whether a change is real.
- Diffing creates typed operations.
- Safety checks inspect the plan before execution.
- Backend handlers render native SQL for one backend and object family.
- Tests cover handler output, ordering, rollback metadata, and round-trip behavior.

A generic SQL generator would need to understand every backend rule at once. dbwarden instead gives each backend handler a focused responsibility.

## Link to Deterministic Diff

SQL generation depends on deterministic diffing. If canonicalization is stable, the operation list is stable. If the operation list is stable, SQL output is reviewable.

See [Deterministic Diff](deterministic-diff.md).
