---
description: SQLite as a first-class dbwarden backend - table rebuilds for changes SQLite's
  ALTER TABLE cannot express, WITHOUT ROWID and STRICT tables, generated columns, and
  verified round-trip support.
---

# SQLite

dbwarden treats SQLite as a **first-class backend**: every natively supported feature is reverse-engineered, diffed, and emitted as correct DDL, and every change SQLite's `ALTER TABLE` cannot express is emitted as a table rebuild rather than as a comment telling you to write the migration yourself.

Database declarations use `DbwardenDatabase` subclasses by default. The equivalent `database_config(...)` function API remains supported for existing projects.

## First-Class Features

"First-class" means the round-trip is verified: reverse-engineer a live database with `generate-models`, feed the output back into `make-migrations`, and get **zero diff**.

```bash
# Step 1: reverse-engineer your live SQLite database
$ dbwarden generate-models -d primary

# Step 2: feed the generated models back in, zero diff
$ dbwarden make-migrations -d primary
# -> "No new migrations to generate"  (your models match the DB exactly)
```

| Category | Features |
|----------|----------|
| Table options | `WITHOUT ROWID`, `STRICT` via `sq_without_rowid`, `sq_strict` |
| Generated columns | `GENERATED ALWAYS AS (expr) STORED / VIRTUAL` via `sq.field(generated=..., generated_mode=...)` |
| Collation | Per-column `COLLATE` via `sq.field(collate="NOCASE")` |
| Keys | `INTEGER PRIMARY KEY AUTOINCREMENT`, composite primary keys, `UNIQUE` and `CHECK` table constraints |
| Foreign keys | Column-level and multi-column `FOREIGN KEY` clauses with `ON DELETE` / `ON UPDATE` |
| Indexes | `CREATE [UNIQUE] INDEX`, partial indexes (`WHERE`), expression indexes |
| Native ALTER | `RENAME TO`, `RENAME COLUMN`, `ADD COLUMN`, `DROP COLUMN` |
| Everything else | Emitted as a table rebuild (see below) |

## Installation

SQLite ships with Python, so no extra dependency group is needed:

```bash
uv add dbwarden
```

## Configuration

```python
from dbwarden import DbwardenDatabase

class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "sqlite"
    database_url_sync = "sqlite:///./app.db"
```

## Table Rebuilds

SQLite's `ALTER TABLE` supports only `RENAME TO`, `RENAME COLUMN`, `ADD COLUMN` and `DROP COLUMN`. Anything else - a column's type, its nullability, its default, a table constraint, `WITHOUT ROWID`, `STRICT` - is expressed by rebuilding the table.

dbwarden generates that rebuild for you, following the procedure from the SQLite documentation:

```sql
CREATE TABLE users__dbw_new (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    email VARCHAR(255) NOT NULL,
    age TEXT,
    CONSTRAINT uq_users_email UNIQUE (email)
);
INSERT INTO users__dbw_new (id, email, age) SELECT id, email, age FROM users;
DROP TABLE users;
ALTER TABLE users__dbw_new RENAME TO users;
CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);
```

Notes on the generated sequence:

- **All changes to one table collapse into one rebuild.** Changing three columns and adding a constraint produces a single copy of the table, not four.
- **The rollback is a rebuild in the other direction**, so a SQLite migration satisfies the [rollback contract](../correctness/rollback-generation.md) instead of emitting a placeholder.
- **The staging table is created without `IF NOT EXISTS`.** A leftover `*__dbw_new` table from a failed run fails the migration rather than silently receiving the copied rows.
- **Indexes are recreated** after the rename, because `DROP TABLE` takes the original table's indexes with it.
- **Generated columns are not copied**; SQLite recomputes them.
- **Dropped columns lose their data.** The rollback restores the column but not its contents, and the migration is reported as a conditional rollback for that reason.
- **Foreign key enforcement.** dbwarden never issues `PRAGMA foreign_keys=ON`, and SQLite defaults it to off, so the `DROP TABLE` step is not blocked by other tables referencing this one. If your deployment enables foreign keys on the migration connection, turn them off for the duration of the migration.
- **Index statements for a rebuilt table are folded into the rebuild.** A rebuild recreates the table's indexes itself, so a migration that both rebuilds a table and changes one of its indexes emits the rebuild alone rather than an index statement that the rebuild would immediately discard.

### What the rebuild preserves

A rebuild reproduces the table from what dbwarden reads back from the database. Reflection alone is lossy, so several things are read from the `CREATE TABLE` and `CREATE INDEX` text SQLite stores in `sqlite_master`:

| Preserved | Read from |
|-----------|-----------|
| Declared type, exactly as written (`VARCHAR(255)`, not `varchar`) | stored table DDL |
| `AUTOINCREMENT` | stored table DDL |
| Generated columns and their `STORED` / `VIRTUAL` mode | stored table DDL |
| Column `COLLATE` | stored table DDL |
| `ON DELETE` / `ON UPDATE` on foreign keys | `PRAGMA foreign_key_list` |
| Unnamed `UNIQUE (...)` table constraints | reflection, named `uq_<table>_<columns>` |
| Partial indexes (`WHERE`), `DESC` and `COLLATE` inside an index, expression indexes | stored index DDL |

An index the migration does not change is recreated from its stored DDL rather than from the reflected shape, so index details SQLAlchemy cannot report survive the rebuild. An index the model changes is rendered from the model.

### What Triggers a Rebuild

| Change | Emitted as |
|--------|-----------|
| Column type change | Rebuild |
| Column nullability change | Rebuild |
| Column default change | Rebuild |
| Add / drop `UNIQUE`, `CHECK` or `FOREIGN KEY` constraint | Rebuild |
| `WITHOUT ROWID` or `STRICT` change | Rebuild |
| Generated expression or collation change | Rebuild |
| Add column that is a primary key, `UNIQUE`, `NOT NULL` without a default, a STORED generated column, or has a non-constant default | Rebuild |
| Drop column that is part of the primary key, is indexed, is named by a table constraint, or is referenced by a generated column | Rebuild |
| Add column (otherwise) | `ALTER TABLE ... ADD COLUMN` |
| Drop column (otherwise) | `ALTER TABLE ... DROP COLUMN` |
| Rename table / rename column | `ALTER TABLE ... RENAME` |
| Add / drop index | `CREATE INDEX` / `DROP INDEX` |

A rebuild copies every row and holds a write lock for the duration. `dbwarden check-impact` reports SQLite table-option, generated-column and collation changes as warnings for that reason.

## Table Metadata

```python
from dbwarden import SqTableMeta
from dbwarden.databases.sqlite import sq


class Session(Base):
    __tablename__ = "sessions"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)

    class Meta(SqTableMeta):
        sq_without_rowid = True
        sq_strict = True
```

| Attribute | Effect |
|-----------|--------|
| `sq_without_rowid` | Appends `WITHOUT ROWID`. The table must have a primary key, and `AUTOINCREMENT` is not available on it. |
| `sq_strict` | Appends `STRICT`. Column types are collapsed to the set SQLite accepts (see below). |
| `sq_indexes` | SQLite-specific index specs, merged with `indexes` |

## Column Metadata

```python
from dbwarden import SqColumnMeta, SqTableMeta
from dbwarden.databases.sqlite import sq


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    slug: Mapped[str] = mapped_column(String)
    code: Mapped[str] = mapped_column(String)

    class Meta(SqTableMeta):
        class slug(SqColumnMeta):
            sq = sq.field(generated="lower(title)")

        class code(SqColumnMeta):
            sq = sq.field(collate="NOCASE")
```

| `sq.field(...)` argument | Effect |
|--------------------------|--------|
| `generated` | `GENERATED ALWAYS AS (<expr>)` |
| `generated_mode` | `STORED` (default) or `VIRTUAL` |
| `collate` | `COLLATE <name>` |

A `VIRTUAL` generated column can be added to an existing table with `ALTER TABLE ADD COLUMN`; a `STORED` one cannot, and forces a rebuild.

## Types and STRICT Tables

Outside a `STRICT` table, SQLite accepts any type name and reads it only to pick a column affinity, so dbwarden emits the declared type unchanged: `VARCHAR(255)` stays `VARCHAR(255)`. This keeps the schema readable and keeps `generate-models` round-trips stable.

Inside a `STRICT` table only `INT`, `INTEGER`, `REAL`, `TEXT`, `BLOB` and `ANY` are legal, so the declared type is collapsed to the closest one:

| Declared | STRICT |
|----------|--------|
| `VARCHAR(n)`, `CHAR(n)`, `CLOB` | `TEXT` |
| `BOOLEAN` | `INTEGER` |
| `DATE`, `DATETIME`, `TIMESTAMP`, `TIME` | `TEXT` |
| `NUMERIC(p,s)`, `DECIMAL(p,s)` | `REAL` |
| `BIGINT`, `SMALLINT`, `INT` | `INTEGER` |
| `FLOAT`, `DOUBLE PRECISION` | `REAL` |

## AUTOINCREMENT

`AUTOINCREMENT` is emitted only where SQLite permits it: a single-column `INTEGER PRIMARY KEY` on a rowid table. On a composite key, a non-integer key, or a `WITHOUT ROWID` table it is omitted, because the declaration would be rejected.

A SQLite `INTEGER PRIMARY KEY` is the rowid alias whether or not `AUTOINCREMENT` is written, so dbwarden does not generate autoincrement changes for primary key columns on SQLite.

## Primary Key Nullability

SQLite reports a primary key column as nullable unless it was declared `NOT NULL`, while a mapped model always declares its primary key `NOT NULL`. dbwarden records primary key columns as `NOT NULL` when reading a SQLite schema, so a table does not report a nullability change on its key column on every run.

## SQLite as a Development Database

SQLite is also the usual choice for `dev_database_url`, where the production schema is generated for another backend and translated for local use. That path is unchanged and documented separately in [SQL Translation](../sql-translation.md).

## Data Conversion in a Rebuild

The rebuild copies rows with `INSERT INTO ... SELECT`, so SQLite's own rules apply to the data:

- In an ordinary table, SQLite converts values to the new column's affinity where it can and stores them as-is where it cannot; a type change never fails for the data's sake.
- In a `STRICT` table, SQLite rejects a value that does not fit the new type, and the migration fails:

    ```
    cannot store REAL value in INTEGER column sales__dbw_new.total
    ```

    That is the table doing its job. Convert the data first - in a `data_op()` or a hand-written migration - and then narrow the column.

- A column that becomes `NOT NULL` while existing rows hold NULL fails the same way. Give the column a default, or backfill first.

## Limitations

- DDL runs inside the migration transaction, but a rebuild rewrites the whole table: expect the migration to take time proportional to table size.
- SQLite allows a single writer; a rebuild blocks other writers for its duration.
- Column comments have no SQLite equivalent and are emitted as SQL comments.
- Schemas (`ATTACH`ed databases) are not modelled; `pg_schema`-style qualification does not apply.
- Dropping a column in a rebuild discards its data; the rollback restores the column, not the values.
- Views are not rebuilt with their table. A view over a rebuilt table survives because SQLite resolves it by name, but a view that names a dropped column breaks at query time.
- `--safe-type-change` has no effect: the rebuild is already a single copy of the table.
