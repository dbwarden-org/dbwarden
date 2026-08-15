# Modeling Guide

This guide walks through the process of defining SQLAlchemy models that dbwarden can read to generate migration SQL. For the complete reference of all supported Meta attributes, see [SQLAlchemy Models Reference](../models.md).

## How dbwarden Reads Models

dbwarden discovers models in the directories specified by `model_paths` in your `database_config(...)`. It reads two sources of metadata from each model:

1. **Column definitions**: typed SQLAlchemy `Mapped[...] = mapped_column(...)` fields, nullability, defaults, primary keys
2. **`class Meta` inner class**: backend-specific options like engine specs, partitioning, codecs

All backend-specific metadata uses the `class Meta` pattern. The `__table_args__` approach is not supported for PostgreSQL metadata. Using `mapped_column(info=...)` for backend-specific options raises `DBWardenConfigError`.

The `class Meta` convention is borrowed from Django's model metadata, so the pattern should read as familiar if you have used Django. dbwarden's version is typed and validated at import time: unknown attributes raise `DBWardenConfigError` when the module loads rather than producing incorrect DDL later.

## Common Meta Attributes

Every backend supports a core set of cross-database attributes. These work with any `database_type`.

### Table-Level

```python
from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from dbwarden.databases import TableMeta

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255))

    class Meta(TableMeta):
        comment = "Core user accounts"
        indexes = [
            {"name": "ix_users_email", "columns": ["email"]},
        ]
```

Available table-level attributes: `comment`, `indexes`, `checks`, `uniques`. See [Common Meta Attributes](../models.md#common-meta-attributes) for details.

### Column-Level

```python
class Meta(TableMeta):
    class internal_note:
        comment = "Internal system note"
        public = False
```

Available column-level attributes: `comment`, `public`. Fields named with a leading `_` are implicitly `public=False`.

For backend-specific column options, use `pg = pg.field(...)` for PostgreSQL. See [Column-Level Meta Base Class](../models.md#column-level-meta-base-class) for details.

## PostgreSQL Models

When `database_type="postgresql"`, use `class Meta(PGTableMeta)` for table-level metadata and `PGColumnMeta` inner classes for column-level metadata.

```python
from sqlalchemy import Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from dbwarden.databases.pgsql import PGTableMeta, PGColumnMeta, pg

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bio: Mapped[str] = mapped_column(Text)

    class Meta(PGTableMeta):
        pg_fillfactor = 80

        class id(PGColumnMeta):
            pg = pg.field(identity="always", identity_start=100)

        class bio(PGColumnMeta):
            pg = pg.field(storage="EXTENDED", compression="pglz")
```

See the [reference](../models.md#postgresql-model-metadata) for the full list of `PGTableMeta` and `PGColumnMeta` attributes, or the [PostgreSQL Deep Dive](../databases/postgresql/index.md) for DDL behavior and snapshot format.

### PostgreSQL Views and Schemas

dbwarden supports PostgreSQL views and materialized views via `PGViewMeta`. Define a view as a model with `__tablename__` matching the view name:

```python
from dbwarden.databases.pgsql import PGViewMeta

class ActiveUser(Base):
    __tablename__ = "active_users"

    id: Mapped[int] = mapped_column(Integer)
    email: Mapped[str] = mapped_column(String(255))

    class Meta(PGViewMeta):
        pg_view_query = "SELECT id, email FROM users WHERE active = true"
        pg_view_materialized = False
```

For materialized views, set `pg_view_materialized = True` and `pg_view_auto_refresh = True` to emit `REFRESH MATERIALIZED VIEW` automatically:

```python
class OrderSummary(Base):
    __tablename__ = "order_summary"

    user_id: Mapped[int] = mapped_column(Integer)
    total: Mapped[float] = mapped_column(Integer)

    class Meta(PGViewMeta):
        pg_view_query = "SELECT user_id, count(*) AS total FROM orders GROUP BY user_id"
        pg_view_materialized = True
        pg_view_auto_refresh = True
```

Scope tables or views to a PostgreSQL schema with `pg_schema`:

```python
class Meta(PGTableMeta):
    pg_schema = "app"

# DDL uses app.users instead of public.users
```

At the config level, set `pg_schema` in `database_config(...)` to set the connection `search_path`. See [Schema Support](../databases/postgresql/schemas.md#config-level-schema) for details.


## Using `generate-models` as a Starting Point

> **Note**: `generate-models` only works for databases with round trip support (PostgreSQL, SQLite, MySQL, ClickHouse). See [Round Trip Support](../databases/round-trip.md) for details.

The fastest way to get a correct model is to reverse-engineer it from your live database:

```bash
$ dbwarden generate-models -d primary --tables users,orders
```

dbwarden produces one `.py` file per table (or a single `models.py` with `--single-file`). The generated output includes `class Meta` with all detected backend-specific metadata.

Review the generated code before using it:

- Column types are mapped from database types to SQLAlchemy types. Verify the mapping is correct for your use case.
- Generated `class Meta` attributes are complete but may need adjustment (for example, you might want different index names or additional column hints).
- Partitioning, TTL, and engine settings are captured from the live database. If the database schema has drifted from what you intend, edit the model before running `make-migrations`.

## Auto-Generated Pydantic Schemas with `@auto_schema`

Use `@auto_schema` to generate four Pydantic schema classes on your model:

| Attribute | Contents |
|-----------|---------|
| `Model.Schema` | All mapped columns |
| `Model.CreateSchema` | Excludes server-defaulted columns (PKs with identity, `server_default`) |
| `Model.UpdateSchema` | All fields optional |
| `Model.PublicSchema` | Excludes fields where `public=False` or name starts with `_` |

Requires the `dbwarden-fastapi` plugin:

```bash
dbwarden plugin add dbwarden-fastapi
```

```python
from dbwarden_fastapi import auto_schema

@auto_schema
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))

    class Meta:
        class email:
            comment = "Primary contact email"
            public = True

        class password_hash:
            public = False

# PublicSchema excludes password_hash and any _prefixed fields
public = User.PublicSchema(email="alice@example.com")
```

The decorator reads `class Meta` to infer `SchemaConfig`, then calls `schemap` to build the Pydantic models. Column `comment` values are injected into Pydantic field descriptions, and backend-specific metadata (`pg_*`, `my_*`, `ch_*`, `mdb_*`, `sq_*`) is included in `json_schema_extra.dbwarden_backend_meta`.

To customize schema generation, pass a `SchemaConfig` explicitly:

```python
from dbwarden_fastapi import auto_schema, SchemaConfig

@auto_schema(config=SchemaConfig(exclude_public=["internal_note"]))
class Order(Base):
    ...
```

`SchemaConfig` supports the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `exclude_always` | `list[str]` | Excluded from all schemas |
| `exclude_create` | `list[str]` | Excluded from CreateSchema only |
| `exclude_update` | `list[str]` | Excluded from UpdateSchema only |
| `exclude_public` | `list[str]` | Excluded from PublicSchema only |
| `field_overrides` | `dict` | Override field types in generated schemas |
| `required_always` | `list[str]` | Fields that are always required |
| `optional_always` | `list[str]` | Fields that are always optional |

## When to Use Manual Migrations

Auto-generated migrations handle most cases, but some schema changes still need manual intervention via `dbwarden new`:

- PostgreSQL `USING` clause for type casts (e.g., casting `TEXT` to `INTEGER`). dbwarden emits `ALTER COLUMN ... TYPE` with a commented-out `-- USING col::newtype` line. Pass `--postgres-auto-using` to emit an active `USING` clause.
- Column renames not caught by the heuristic auto-detection. Use `--rename old_name:new_name` flags for deterministic renames, or rename in a manual migration.
- Data migrations (backfilling, transforming existing data). dbwarden emits a SQL comment placeholder.

For these cases run `dbwarden new` and write the SQL by hand, or use the relevant flag for auto-generation.

## Best Practices

- **One model class per table**: dbwarden discovers models by scanning directories. Each table should have exactly one model class.
- **Use `model_paths`**: always set `model_paths` explicitly in `database_config(...)`. Auto-discovery is available but explicit paths are more predictable.
- **Review generated migrations**: always read the `.sql` file before running `dbwarden migrate`.
- **Use `--dev` for local development**: configure a `dev_database_url` (SQLite works well) and use `dbwarden --dev` to iterate quickly without touching your real database.
- **Keep Meta classes minimal**: only set attributes that differ from the default. Default values are omitted from generated migrations, reducing noise.
- **Use `@auto_schema` for API projects** (requires `dbwarden-fastapi` plugin): generates Pydantic schemas from your model annotations. Fields with `public=False` or a leading `_` are excluded from `PublicSchema`.

See also: [Cookbook: Models & Migrations](../cookbook/02-models-and-migrations.md)

Next, continue with [Your First Migration](first-migration.md).
