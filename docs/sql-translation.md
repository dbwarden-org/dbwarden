# SQL Translation

dbwarden includes a SQL translation layer to support development workflows where your primary database differs from your development database.

The most common case is:

- Primary database: PostgreSQL/MySQL/MariaDB/ClickHouse
- Development database: SQLite (`--dev` mode)

This keeps local development fast while still allowing production-targeted schemas.

## Why SQL Translation Exists

SQLite does not support all backend-specific SQL types and default expressions used by other databases.

Without translation, generated migrations can fail in local development when they contain backend-specific types like `UUID`, `JSONB`, or default expressions like `now()`.

dbwarden translation solves this by adapting generated SQL for SQLite compatibility.

## When translation is active

Translation runs whenever the **resolved backend is SQLite** and SQL is being
generated from models (`make-migrations`, `export-models`). That covers both:

- a `--dev` run whose `dev_database_url` is SQLite, and
- a database whose `database_type` is `sqlite` in its own right.

It is not a runtime SQL proxy for arbitrary manual SQL, and it is not applied to
migration files you write by hand.

!!! note "Translation and first-class SQLite are different layers"

    Translation adapts a *type or default written for another backend* so it is
    legal in SQLite. [First-class SQLite support](databases/sqlite.md) is about
    the shape of the migration itself - table rebuilds, `WITHOUT ROWID`,
    `STRICT`, generated columns. They compose: a translated type is what the
    rebuild then renders.

## How It Works

When you run commands in development mode and target a SQLite dev database:

```bash
$ dbwarden --dev make-migrations "sync models" -d primary
```

dbwarden uses this flow:

1. Loads the selected database config and resolves `dev_database_url`.
2. Detects that the active target backend is SQLite.
3. Extracts model metadata from SQLAlchemy models.
4. Translates backend-specific types/defaults to SQLite-compatible SQL.
5. Generates migration SQL with translated definitions.

Translation is applied during migration generation, not as a post-processing regex pass.

## Type conversion behavior

Common conversions:

| Source type | SQLite output |
|-------------|---------------|
| `UUID` | `TEXT` |
| `JSON` / `JSONB` | `TEXT` |
| `BYTEA` | `BLOB` |
| `TIMESTAMPTZ` / `TIMESTAMP WITH TIME ZONE` | `DATETIME` |
| `SERIAL` / `BIGSERIAL` | `INTEGER` |
| `BOOL` | `BOOLEAN` |
| `INT8` / `INT16` / `INT32` / `INT64` | `INTEGER` |
| `UINT8` / `UINT16` / `UINT32` / `UINT64` | `INTEGER` |
| `FLOAT32` / `FLOAT64` | `REAL` |
| `DATETIME64` / `DATETIME64(n)` | `DATETIME` |
| `DECIMAL(p,s)` / `NUMERIC(p,s)` | `NUMERIC` |
| `FIXEDSTRING(n)` | `TEXT` |
| `ARRAY(...)` | `TEXT` (or an error in strict mode) |

ClickHouse `Nullable(...)` and `LowCardinality(...)` wrappers are unwrapped
before the inner type is translated.

Types SQLite already understands - `INTEGER`, `VARCHAR(n)`, `TEXT`, `BOOLEAN`,
`REAL`, `DATE`, `DATETIME`, `BLOB`, `NUMERIC` and friends - pass through
unchanged, keeping their declared length.

If a type cannot be translated safely:

- non-strict mode: fallback to `TEXT` + warning
- strict mode: fail migration generation

## Default expression handling

Backend expressions such as `now()` or `gen_random_uuid()` may not have direct SQLite equivalents.

`CURRENT_TIMESTAMP`, `CURRENT_DATE` and `CURRENT_TIME` pass through unchanged.
`NOW()`, `UUID_GENERATE_V4()`, `GEN_RANDOM_UUID()` and `NEXTVAL(...)` have no
SQLite equivalent.

In non-strict mode, unsupported defaults are dropped with warning.

In strict mode, unsupported defaults fail generation.

## Strict Translation Mode

If you want hard failures instead of fallback behavior:

```bash
$ dbwarden --dev --strict-translation make-migrations "sync models" -d primary
```

In strict mode:

- Unknown/unsupported type conversions raise errors
- Unsupported default expression conversions raise errors

Use this when you want to catch every lossy conversion early.

!!! warning "`--strict-translation` is not a `STRICT` table"

    `--strict-translation` controls what dbwarden does with a type it cannot
    translate. SQLite's `STRICT` keyword is a table option, set with
    `sq_strict` on the model, that makes SQLite itself enforce column types.
    See [SQLite](databases/sqlite.md#types-and-strict-tables).

## Recommended team workflow

1. iterate quickly with `--dev` (SQLite)
2. keep strict checks in CI (`--strict-translation`)
3. validate release candidate migrations against production-like database

This balances speed and correctness.

## Troubleshooting

`--dev mode is enabled, but database '<name>' has no dev_database_url configured`:

- add `dev_database_url` for that database entry

Unexpected type fallback to `TEXT`:

- inspect model type for backend-specific declaration
- re-run with `--strict-translation` to fail fast and fix explicitly

Generated SQL differs from production expectations:

- expected in SQLite compatibility mode; validate final release migrations on production-like backend

## Notes and Limitations

- Translation focuses on compatibility for local development.
- Some backend features cannot be represented exactly in SQLite.
- For production accuracy, always test migrations against your production-like database too.
- Translation converts a type name; it does not convert stored data. Narrowing a
  column against existing rows can still be rejected by SQLite when the target
  table is `STRICT`.
