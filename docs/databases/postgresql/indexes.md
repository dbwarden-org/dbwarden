# Indexes

Indexes are handled by `IndexHandler` during the DIFF phase.

## Supported Index Types

| Method | Description |
|--------|-------------|
| B-tree | Default, for equality and range queries |
| Hash | Equality-only, smaller than btree |
| GiST | Geometric, full-text, and custom data types |
| GIN | JSONB, array, full-text, and tsvector |
| BRIN | Block range indexes for large tables |
| SP-GiST | Space-partitioned GiST |

## Declaring Indexes

Model-level indexes use `PgIndexSpec`:

```python
from dbwarden.databases.pgsql import PgIndexSpec

class Meta(PGTableMeta):
    pg_indexes = [
        PgIndexSpec("ix_users_email", ["email"], unique=True, using="btree"),
        PgIndexSpec("ix_users_data", ["data"], using="gin",
            postgresql_ops={"data": "jsonb_path_ops"}),
    ]
```

`PgIndexSpec` supports:

| Field | Description |
|-------|-------------|
| `name` | Index name (auto-generated if omitted) |
| `columns` | Indexed columns |
| `unique` | `CREATE UNIQUE INDEX` |
| `using` | Access method (`btree`, `gin`, `gist`, `hash`, `brin`, `spgist`) |
| `where` | Partial index predicate |
| `include` | `INCLUDE` columns (covering indexes) |
| `expression` | Expression index (e.g., `lower(email)`) |
| `with_params` | Storage parameters (`fillfactor`, `autovacuum_*`) |
| `tablespace` | `TABLESPACE name` |
| `nulls_not_distinct` | `NULLS NOT DISTINCT` (PG 15+) |
| `column_sorting` | Per-column `ASC` / `DESC` / `NULLS FIRST` / `NULLS LAST` |
| `postgresql_ops` | Per-column operator classes |
| `concurrently` | `CREATE INDEX CONCURRENTLY` (default `True`) |

## Partial Indexes

```python
PgIndexSpec("ix_users_active", ["email"], where="active = true")
```

```sql
CREATE INDEX CONCURRENTLY ix_users_active ON users (email) WHERE active = true;
```

## Expression Indexes

```python
PgIndexSpec("ix_users_lower_email", columns=[], expression="lower(email)")
```

```sql
CREATE INDEX CONCURRENTLY ix_users_lower_email ON users (lower(email));
```

Expression indexes are stored with empty `columns` and the expression text in the `expression` field. Changes to the expression are detected and produce a `DROP INDEX` + `CREATE INDEX` cycle.

## Covering Indexes (INCLUDE)

Non-key columns stored in the index to enable index-only scans:

```python
PgIndexSpec("ix_users_email_covering", ["email"],
    include=["name", "avatar_url"])
```

```sql
CREATE INDEX CONCURRENTLY ix_users_email_covering ON users (email) INCLUDE (name, avatar_url);
```

`INCLUDE` columns are not used for index scans but are available as output columns without a table lookup. This makes index-only scans possible for queries that select only `email`, `name`, and `avatar_url`.

## Multi-Column Indexes

Column order matters for query planning:

```python
PgIndexSpec("ix_orders_user_date", ["user_id", "created_at DESC"])
```

B-tree multi-column indexes support queries on the leftmost columns. This index supports:
- `WHERE user_id = 1` (leftmost prefix)
- `WHERE user_id = 1 AND created_at > '2024-01-01'` (range on second column)
- Does NOT support: `WHERE created_at > '2024-01-01'` (no leftmost column)

## Operator Classes

```python
PgIndexSpec("ix_users_data", ["data"],
    using="gin",
    postgresql_ops={"data": "jsonb_path_ops"})
```

```sql
CREATE INDEX CONCURRENTLY ix_users_data ON users USING GIN (data jsonb_path_ops);
```

### Common Operator Classes

| Access Method | Operator Class | Use Case |
|---------------|---------------|----------|
| GIN | `jsonb_path_ops` | Smaller, faster JSONB path queries |
| GIN | `array_ops` (default) | Array containment queries |
| GiST | `inet_ops` | IP address range queries |
| GiST | `tsvector_ops` | Full-text search |
| BRIN | `bloom_ops` | Bloom filter indexes |

## Column Sorting

```python
PgIndexSpec("ix_orders_date", ["created_at DESC NULLS LAST", "id ASC"])
```

```sql
CREATE INDEX CONCURRENTLY ix_orders_date ON users (created_at DESC NULLS LAST, id ASC);
```

## BRIN Parameters

BRIN indexes accept access-method-specific storage parameters:

```python
PgIndexSpec("ix_logs_created_at", ["created_at"],
    using="brin",
    with_params={"pages_per_range": 64, "autosummarize": True})
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `pages_per_range` | `128` | Number of pages per block range. Lower values = finer granularity, larger index |
| `autosummarize` | `off` | Automatically summarize new pages on insert |

## REINDEX

DBWarden does not auto-generate `REINDEX` statements. When an index becomes corrupted or bloated, recreate it manually:

```sql
REINDEX INDEX CONCURRENTLY ix_users_email;
REINDEX TABLE CONCURRENTLY users;
REINDEX DATABASE CONCURRENTLY mydb;
```

`CONCURRENTLY` avoids locking, but requires extra resources and can fail if the index is a unique index with duplicates.

## NULLS NOT DISTINCT

```python
PgIndexSpec("ix_users_email_unique", ["email"],
    unique=True, nulls_not_distinct=True)
```

Without `NULLS NOT DISTINCT`, a unique index allows multiple NULL values (PostgreSQL treats NULLs as distinct by default). With `NULLS NOT DISTINCT` (PG 15+), only one NULL is permitted.

## Unique Constraint vs Unique Index

For declaring uniqueness, DBWarden offers two paths:

| Path | API | Use case |
|------|-----|----------|
| **Constraint** | `UniqueSpec` in `uniques` / `pg_uniques` | Business rule, FK-targetable, appears in `information_schema` |
| **Index** | `unique=True` on `PgIndexSpec` | Performance-focused, simpler configuration |

See [Constraints](constraints.md#uniquespec-vs-uniquetrue-on-pgindexspec) for the full comparison and guidance.

## Migration Safety

| Change | Severity |
|--------|----------|
| Add index | `INFO` |
| Drop index | `WARNING` |
| Change index expression / columns | `WARNING` |
| Add/drop INCLUDE column | `INFO` |
| Change storage parameters | `INFO` |
| Change tablespace | `WARNING` |

See [Migration Safety](migration-safety.md) for the full classification table.
