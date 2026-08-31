# SQLAlchemy Models Reference

This page is the **reference** for all supported Meta attributes across every backend. For a step-by-step walkthrough of defining models, see the [Modeling Guide](getting-started/modeling.md).

dbwarden reads SQLAlchemy model metadata to generate migration SQL. Use
`model_paths` on your `DbwardenDatabase` classes, or in equivalent
`database_config(...)` entries, to control discovery.

```python
from dbwarden import DbwardenDatabase

class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://user:pass@localhost:5432/main"
    model_paths = ["app.models"]
    model_tables = ["users", "posts", "comments"]
```

## Common Meta Attributes

Every backend supports a core set of cross-database attributes via `class Meta(TableMeta)`:

### Table-level

| Attribute | Type | SQL | Backends |
|-----------|------|-----|----------|
| `comment` | `str` | `COMMENT ON TABLE t IS '...'` | All |
| `indexes` | `list[IndexSpec]` | `CREATE INDEX ...` | All |
| `checks` | `list[CheckSpec]` | `ALTER TABLE t ADD CONSTRAINT ... CHECK (...)` | All |
| `uniques` | `list[UniqueSpec]` | `ALTER TABLE t ADD CONSTRAINT ... UNIQUE (...)` | All |

```python
from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from dbwarden.databases import TableMeta, IndexSpec, CheckSpec, UniqueSpec

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255))
    age: Mapped[int] = mapped_column(Integer)

    class Meta(TableMeta):
        comment = "Core user accounts"
        indexes = [
            IndexSpec(name="ix_users_email", columns=["email"]),
        ]
        checks = [
            CheckSpec(name="ck_users_age", expression="age >= 0"),
        ]
        uniques = [
            UniqueSpec(name="uq_users_email", columns=["email"]),
        ]
```

Typed spec classes (`IndexSpec`, `UniqueSpec`, `CheckSpec`) provide full IDE autocomplete and are the recommended way to declare model metadata. Plain dicts are also accepted for all three lists using the same field names.

### Column-level

| Attribute | Type | SQL | Backends |
|-----------|------|-----|----------|
| `comment` | `str` | `COMMENT ON COLUMN t.c IS '...'` | All |
| `public` | `bool` | Controls field visibility in `@auto_schema` (requires `dbwarden-fastapi` plugin) | All |

```python
class Meta(TableMeta):
    class internal_note:
        comment = "Internal system note"
        public = False
```

These attributes work with any `database_type`. Backend-specific subclasses (`PGTableMeta`, `MyTableMeta`, `CHTableMeta`) inherit all common attributes and add their own.

### Typed Spec Classes (Recommended)

Use `IndexSpec`, `UniqueSpec`, and `CheckSpec` from `dbwarden.databases` for cross-backend model metadata. These provide full IDE autocomplete and type checking:

```python
from dbwarden.databases import IndexSpec, UniqueSpec, CheckSpec

class Meta(TableMeta):
    indexes = [
        IndexSpec(name="ix_users_email", columns=["email"]),
    ]
    checks = [
        CheckSpec(name="ck_users_age", expression="age >= 0"),
    ]
    uniques = [
        UniqueSpec(name="uq_users_email", columns=["email"]),
    ]
```

Plain dicts using the same field names (`name`, `columns`, `expression`, `sql`) are also accepted for backwards compatibility, but typed specs are the idiomatic form.

### Column-Level Meta Base Class

For IDE autocomplete on column-level inner classes, use `PGColumnMeta` for PostgreSQL, `MyColumnMeta` for MySQL, `MdbColumnMeta` for MariaDB, or `CHColumnMeta` for ClickHouse. Each defines the cross-database attributes (`comment`, `public`) alongside its backend-specific spec object (`pg`, `ch`, `my`, `mdb`, `sq`). There is no shared `FieldMeta` base: the `*FieldMeta` classes were removed and their fields inlined into each backend's column Meta.

```python
from dbwarden.databases.pgsql import PGColumnMeta, pg

# Use typed spec objects for backend-specific column attributes:
#   pg = pg.field(collation=..., storage=..., ...)
#   ch = ch.field(codec=..., nullable=..., ...)
```

Backend-specific options are always set via a typed spec object attribute, never as flat attributes. For example, use `pg = pg.field(collation="en_US.UTF-8")` instead of the old `pg_collation = "en_US.UTF-8"`.

### Backend Subpackages

dbwarden organizes backend-specific types into subpackages under `dbwarden.databases`, also available there as short aliases:

| Alias | Subpackage | Key types |
|-------|------------|-----------|
| `pg` | `dbwarden.databases.pgsql` | `PgFieldSpec`, `PgIndexSpec`, `PgTableSpec` |
| `ch` | `dbwarden.databases.clickhouse` | `ChFieldSpec`, `ChIndexSpec`, `ChTableSpec` |
| `my` | `dbwarden.databases.mysql` | `MyFieldSpec`, `MyTableSpec` |
| `mdb` | `dbwarden.databases.mariadb` | `MdbFieldSpec`, `MdbTableSpec` |
| `sq` | `dbwarden.databases.sqlite` | `SqFieldSpec`, `SqTableSpec`, `SqTableMeta`, `SqColumnMeta` |

Only `IndexSpec`, `PgIndexSpec`, and `ChIndexSpec` exist as typed index spec classes. MySQL, MariaDB, and SQLite use the base `IndexSpec` with the `indexes` attribute or plain dicts in their backend-specific index list (`my_indexes`, `sq_indexes`).

```python
from dbwarden.databases.pgsql import pg
from dbwarden.databases.clickhouse import ch
from dbwarden.databases.mysql import my
from dbwarden.databases.mariadb import mdb
from dbwarden.databases.sqlite import sq

# Use pg.field(), ch.field() for column-level metadata
pg_spec = pg.field(collation="en_US.UTF-8", storage="PLAIN")
ch_spec = ch.field(codec="ZSTD(3)", nullable=True)
```

## PostgreSQL Model Metadata

When `database_type="postgresql"`, dbwarden supports first-class PostgreSQL metadata via `class Meta(PGTableMeta)` inner classes. This is the **only** supported surface: `mapped_column(info=...)` raises `DBWardenConfigError`.

### Table-Level Meta

Inherit from `PGTableMeta` on your `class Meta`:

```python
from sqlalchemy import Integer
from sqlalchemy.orm import DeclarativeBase,     Mapped, mapped_column
from dbwarden.databases.pgsql import PGTableMeta

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    class Meta(PGTableMeta):
        pg_fillfactor = 80
        pg_tablespace = "fastspace"
        pg_storage_params = {
            "fillfactor": 80,
            "autovacuum_enabled": "false",
        }
```

`PGTableMeta` inherits all common `TableMeta` attributes (`comment`, `indexes`, `checks`, `uniques`) and adds PostgreSQL-specific ones (`pg_schema`, `pg_fillfactor`, `pg_tablespace`, `pg_storage_params`, `pg_unlogged`, `pg_partition`, `pg_inherits`, `pg_excludes`, `pg_indexes`, `pg_checks`, `pg_uniques`).

For PostgreSQL-specific indexes, use `PgIndexSpec` in `pg_indexes`:

```python
from dbwarden.databases.pgsql import PgIndexSpec

class Meta(PGTableMeta):
    pg_indexes = [
        PgIndexSpec("ix_users_email", ["email"],
            unique=True, using="gin"),
    ]
```

`PgIndexSpec` supports operator classes via `postgresql_ops` for GIN indexes on JSONB columns:

```python
PgIndexSpec("ix_users_data", ["data"],
    using="gin",
    postgresql_ops={"data": "jsonb_path_ops"})
```

This generates `CREATE INDEX ... ON users USING GIN (data jsonb_path_ops)`.

Full `PgIndexSpec` constructor fields: `name`, `columns`, `unique`, `using`, `where`, `include`, `with_params`, `tablespace`, `nulls_not_distinct`, `column_sorting`, `postgresql_ops`, `concurrently`. See [Indexes](databases/postgresql/indexes.md) for indexing options and [Declaring Metadata](databases/postgresql/declaring-metadata.md) for the full `PGTableMeta` attribute list.

### Column-Level Meta

Use `PGColumnMeta` inner classes named after the column. Use `pg = pg.field(...)` to set column-level options:

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
        class id(PGColumnMeta):
            pg = pg.field(identity="always", identity_start=100)

        class bio(PGColumnMeta):
            pg = pg.field(storage="EXTENDED", collation="en_US.UTF-8")
```

`PGColumnMeta` includes the common `comment` and `public` attributes plus a `pg` attribute of type `PgFieldSpec` that bundles all PostgreSQL-specific column options.

For the full list of supported attributes, see [Tables & Columns](databases/postgresql/tables-and-columns.md) for column handler details and [Declaring Metadata](databases/postgresql/declaring-metadata.md) for PGColumnMeta options.

### PostgreSQL Views

Use `PGViewMeta` for models that represent PostgreSQL views or materialized views. Set `__tablename__` to the view name:

```python
from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from dbwarden.databases.pgsql import PGViewMeta

class Base(DeclarativeBase):
    pass

class ActiveUser(Base):
    __tablename__ = "active_users"

    id: Mapped[int] = mapped_column(Integer)
    email: Mapped[str] = mapped_column(String(255))

    class Meta(PGViewMeta):
        pg_view_query = "SELECT id, email FROM users WHERE active = true"
        pg_view_materialized = False
```

`PGViewMeta` attributes:

| Attribute | Type | Default | SQL |
|-----------|------|---------|-----|
| `pg_view_query` | `str` | `None` | `AS <query>` in CREATE VIEW |
| `pg_view_materialized` | `bool` | `False` | `CREATE MATERIALIZED VIEW` instead of `CREATE VIEW` |
| `pg_view_auto_refresh` | `bool` | `False` | Emits `REFRESH MATERIALIZED VIEW` in subsequent migrations |
| `pg_schema` | `str \| None` | `None` | Fully qualifies the view as `schema.view_name` |

Set `pg_view_auto_refresh = True` to keep a materialized view current after each schema change:

```python
class Meta(PGViewMeta):
    pg_view_query = "SELECT user_id, count(*) AS total FROM orders GROUP BY user_id"
    pg_view_materialized = True
    pg_view_auto_refresh = True
```

Each `make-migrations` run after the initial CREATE produces a `REFRESH MATERIALIZED VIEW` statement.

See [Views](databases/postgresql/views.md) for the full view lifecycle and auto-refresh configuration.

## ClickHouse Model Metadata

When `database_type="clickhouse"`, dbwarden supports first-class ClickHouse metadata via `class Meta(CHTableMeta)` inner classes. This is the **only** supported surface. Pass options via `mapped_column(info=...)` raises `DBWardenConfigError`.

### Table-Level Meta

Inherit from `CHTableMeta` on your `class Meta`:

```python
from datetime import date
from sqlalchemy.orm import DeclarativeBase,     Mapped, mapped_column
from dbwarden.databases.clickhouse import CHTableMeta, ChEngineSpec

class Base(DeclarativeBase):
    pass

class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Int64, primary_key=True)
    event_date: Mapped[date] = mapped_column(Date)
    payload: Mapped[str] = mapped_column(String)

    class Meta(CHTableMeta):
        ch_engine = ChEngineSpec("ReplacingMergeTree", args=("version_column",))
        ch_order_by = ["region", "event_time"]
        ch_primary_key = "region"
        ch_partition_by = "toYYYYMM(event_time)"
        ch_sample_by = "intHash64(user_id)"
        ch_ttl = [
            "event_time + INTERVAL 1 MONTH DELETE",
            "event_time + INTERVAL 1 YEAR TO DISK 'cold'",
        ]
        ch_settings = {"index_granularity": "8192"}
```

`CHTableMeta` inherits all common `TableMeta` attributes (`comment`, `indexes`, `checks`, `uniques`) and adds ClickHouse-specific ones (`ch_engine`, `ch_order_by`, `ch_primary_key`, `ch_partition_by`, `ch_sample_by`, `ch_ttl`, `ch_settings`, `ch_object_type`, `ch_select_statement`, `ch_to_table`, `ch_dictionary`, `ch_dict_layout`, `ch_dict_source`, `ch_dict_lifetime`, `ch_dict_primary_key`, `ch_projections`, `ch_zookeeper_path`, `ch_replica_name`).

For the full list of supported attributes, see [ClickHouse Deep Dive](databases/clickhouse/index.md).

### Column-Level Meta

Use `CHColumnMeta` inner classes named after the column. Use `ch = ch.field(...)` to set column-level options:

```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from dbwarden.databases.clickhouse import CHTableMeta, CHColumnMeta, ChEngineSpec, ch

class Base(DeclarativeBase):
    pass

class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Int64, primary_key=True)
    payload: Mapped[str] = mapped_column(String)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String))

    class Meta(CHTableMeta):
        ch_engine = ChEngineSpec("MergeTree")
        ch_order_by = "event_time"

        class payload(CHColumnMeta):
            ch = ch.field(codec="ZSTD(3)", nullable=False)

        class tags(CHColumnMeta):
            ch = ch.field(low_cardinality=True)
```

`CHColumnMeta` includes the common `comment` and `public` attributes plus a `ch` attribute of type `ChFieldSpec` that bundles all ClickHouse-specific column options.

### Engine Spec

Use `ChEngineSpec` for the table engine:

```python
from dbwarden.databases.clickhouse import ChEngineSpec

# Simple engine
ch_engine = ChEngineSpec("MergeTree")

# Engine with arguments
ch_engine = ChEngineSpec("ReplacingMergeTree", args=("version_column",))

# Replicated engine
ch_engine = ChEngineSpec("ReplicatedMergeTree",
    zookeeper_path="/clickhouse/tables/shard1/events",
    replica_name="{replica}")

# Distributed engine with settings
ch_engine = ChEngineSpec("Distributed",
    args=("cluster", "db", "events", "rand()"),
    settings={"insert_distributed_sync": "1"})
```

For replicated engines, `ch_zookeeper_path` and `ch_replica_name` are injected as the first two engine arguments. If `args` contains existing positional arguments, they come after the ZooKeeper path and replica name.

### Projections

Use `ProjectionSpec` in `ch_projections`:

```python
from dbwarden.databases.clickhouse import ProjectionSpec

class Meta(CHTableMeta):
    ch_order_by = ["author", "created_at"]
    ch_projections = [
        ProjectionSpec("by_author", "SELECT * ORDER BY author"),
        ProjectionSpec("daily_stats",
            "SELECT toDate(created_at) AS day, count() GROUP BY day"),
    ]
```

Current behavior:
- projection definitions are rendered into generated ClickHouse DDL
- safety checks classify added projections as `INFO`
- removed projections are classified as `WARNING`

### Skip Indexes

Use `ChIndexSpec` in `ch_indexes`:

```python
from dbwarden.databases.clickhouse import ChIndexSpec

class Meta(CHTableMeta):
    ch_indexes = [
        ChIndexSpec("ix_payload", ["payload"],
            type="bloom_filter", granularity=1),
    ]
```

### Materialized Views

Use `materialized_view()` builder with `CHViewMeta`:

```python
from dbwarden.databases.clickhouse import CHViewMeta, materialized_view

class EventRollup(Base):
    __tablename__ = "event_rollup_mv"

    event_date: Mapped[date] = mapped_column(Date)
    total: Mapped[int] = mapped_column(Int64)

    class Meta(CHViewMeta):
        ch = materialized_view(
            select=(
                "SELECT toDate(event_time) AS event_date, count() AS total "
                "FROM events GROUP BY event_date"
            ),
            to="mv_target",
        )
```

Two storage modes:

| Shape | `to` | `engine` / `order_by` |
|-------|-----------|----------------------|
| Explicit target | Set | Not needed (target owns storage) |
| Implicit `.inner` | `None` | Required on `materialized_view()` |

Expression fields (select, order_by, partition_by, ttl) accept
SQLAlchemy `ColumnElement`, `ChRaw`, or plain `str`.

### Dictionaries

ClickHouse dictionaries use `ch_dictionary = True` with related `ch_dict_*` fields:

```python
class CountryCode(Base):
    __tablename__ = "country_codes"

    code: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)

    class Meta(CHTableMeta):
        ch_dictionary = True
        ch_dict_layout = "FLAT()"
        ch_dict_source = "CLICKHOUSE(HOST 'localhost' TABLE 'countries')"
        ch_dict_lifetime = "MIN 0 MAX 3600"
        ch_dict_primary_key = "code"
```

Required fields when `ch_dictionary = True`:

| Field | Description | Example |
|-------|-------------|---------|
| `ch_dict_layout` | Dictionary layout | `"FLAT()"`, `"COMPLEX_KEY_HASHED()"` |
| `ch_dict_source` | Source configuration | `"CLICKHOUSE(HOST '...' TABLE '...')"` |
| `ch_dict_lifetime` | Cache lifetime | `"MIN 0 MAX 3600"` or `3600` |

Optional field:

| Field | Description | Default |
|-------|-------------|---------|
| `ch_dict_primary_key` | Primary key expression | First column |

Column types render as CH-native types (`Int64`, `String`).

### Column Hints

Use `CHColumnMeta` inner classes for per-column hints instead of `info={}`:

```python
from dbwarden.databases.clickhouse import ch

class Meta(CHTableMeta):
    class payload(CHColumnMeta):
        ch = ch.field(codec="ZSTD(3)", low_cardinality=True, nullable=False)
```

Supported `ch.field()` options:

| Keyword | Type | Description | Example |
|---------|------|-------------|---------|
| `codec` | `str` | Compression codec | `"ZSTD(3)"` |
| `default_expression` | `str` | Default value expression | `"now()"` |
| `materialized` | `str` | Materialized expression | `"lower(name)"` |
| `alias` | `str` | Alias expression | `"concat(a, b)"` |
| `ttl` | `str` | Column TTL expression | `"event_time + INTERVAL 1 YEAR"` |
| `low_cardinality` | `bool` | Wrap type in LowCardinality | `True` |
| `nullable` | `bool` | Wrap type in Nullable | `True` |

## MySQL Model Metadata

When `database_type="mysql"` (or `"mariadb"`), dbwarden supports first-class MySQL metadata via `class Meta(MyTableMeta)` inner classes. This is the **only** supported surface: `mapped_column(info=...)` raises `DBWardenConfigError`.

### Table-Level Meta

Inherit from `MyTableMeta` on your `class Meta`:

```python
from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from dbwarden.databases.mysql import MyTableMeta

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255))

    class Meta(MyTableMeta):
        my_engine = "InnoDB"
        my_charset = "utf8mb4"
        my_collate = "utf8mb4_unicode_ci"
        my_row_format = "DYNAMIC"
        my_auto_increment = 1000
        comment = "Core user accounts"
```

`MyTableMeta` inherits all common `TableMeta` attributes (`comment`, `indexes`, `checks`, `uniques`) and adds MySQL-specific ones (`my_engine`, `my_charset`, `my_collate`, `my_row_format`, `my_auto_increment`).

For MariaDB, use `MdbTableMeta` which extends `MyTableMeta` with `mdb_page_compressed` and `mdb_page_compression_level`.

### Column-Level Meta

Use `MyColumnMeta` inner classes named after the column. Use `my = my.field(...)` to set column-level MySQL options:

```python
from sqlalchemy import Integer, String, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from dbwarden.databases.mysql import MyTableMeta, MyColumnMeta, my

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[str] = mapped_column(TIMESTAMP)

    class Meta(MyTableMeta):
        class id(MyColumnMeta):
            comment = "Primary key"
            my = my.field(unsigned=True)

        class email(MyColumnMeta):
            my = my.field(charset="utf8mb4", collate="utf8mb4_unicode_ci")

        class updated_at(MyColumnMeta):
            my = my.field(on_update="CURRENT_TIMESTAMP")
```

Supported `my.field()` options:

| Keyword | Type | Description | Example |
|---------|------|-------------|---------|
| `unsigned` | `bool` | `UNSIGNED` on integer columns | `unsigned=True` |
| `charset` | `str` | Per-column character set | `charset="utf8mb4"` |
| `collate` | `str` | Per-column collation | `collate="utf8mb4_unicode_ci"` |
| `on_update` | `str` | `ON UPDATE` expression (typically for TIMESTAMP) | `on_update="CURRENT_TIMESTAMP"` |

For MariaDB, use `MdbColumnMeta` and `mdb.field()` which extends `my.field()` with `invisible` and `sequence` options.

Cross-backend column attributes (`comment`, `public`) are set directly on the inner class, not on the spec object.

See [Type Mapping](databases/postgresql/type-mapping.md) for the full SQLAlchemy-to-PostgreSQL type normalization table.
