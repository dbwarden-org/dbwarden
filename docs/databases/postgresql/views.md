# Views

**Handler**: `ViewHandler` (DIFF phase)

Views are model-derived via `PGViewMeta`.

## Regular Views

```python
from dbwarden.databases.pgsql import PGViewMeta

class ActiveUser(Base):
    __tablename__ = "active_users"

    id: Mapped[int] = mapped_column(Integer)
    email: Mapped[str] = mapped_column(String(255))

    class Meta(PGViewMeta):
        pg_view_query = "SELECT id, email, name FROM users WHERE active = true"
        pg_view_materialized = False
```

Generated DDL:
```sql
CREATE OR REPLACE VIEW active_users AS SELECT id, email, name FROM users WHERE active = true;
```

## Materialized Views

```python
class OrderSummary(Base):
    __tablename__ = "order_summary"

    user_id: Mapped[int] = mapped_column(Integer)
    total: Mapped[float] = mapped_column(Integer)

    class Meta(PGViewMeta):
        pg_view_query = "SELECT user_id, count(*) AS total FROM orders GROUP BY user_id"
        pg_view_materialized = True
```

Generated DDL:
```sql
CREATE MATERIALIZED VIEW order_summary AS SELECT user_id, count(*) AS total FROM orders GROUP BY user_id;
```

## WITH Options

### Security Barrier

Control whether the view acts as a security barrier:

```python
class Meta(PGViewMeta):
    pg_view_query = "SELECT id, email FROM users WHERE active = true"
    pg_view_options = "security_barrier"
```

```sql
CREATE OR REPLACE VIEW active_users WITH (security_barrier) AS ...;
```

Security barrier views prevent leaked subquery optimizations when used for row-level access control. Use this for views that expose a subset of a table based on session variables.

### WITH CHECK OPTION

Control what rows can be inserted/updated through the view:

```python
class Meta(PGViewMeta):
    pg_view_query = "SELECT id, email, name FROM users WHERE active = true"
    pg_view_check_option = "LOCAL"  # or "CASCADED"
```

```sql
CREATE OR REPLACE VIEW active_users AS ... WITH LOCAL CHECK OPTION;
```

| Option | Behaviour |
|--------|-----------|
| `LOCAL` | Check only this view's WHERE clause |
| `CASCADED` (default) | Check this view's WHERE and all underlying views' WHERE clauses |

Without `WITH CHECK OPTION`, rows can be inserted or updated through the view even if they would not satisfy the view's WHERE clause (they become invisible through the view but exist in the underlying table).

## Auto-Refresh

Set `pg_view_auto_refresh = True` to emit `REFRESH MATERIALIZED VIEW` in subsequent migrations:

```python
class Meta(PGViewMeta):
    pg_view_query = "..."
    pg_view_materialized = True
    pg_view_auto_refresh = True
```

```sql
REFRESH MATERIALIZED VIEW order_summary;
```

### REFRESH CONCURRENTLY

To avoid table locking during refresh:

```python
class Meta(PGViewMeta):
    pg_view_query = "..."
    pg_view_materialized = True
    pg_view_auto_refresh = True
    pg_view_refresh_concurrently = True
```

```sql
REFRESH MATERIALIZED VIEW CONCURRENTLY order_summary;
```

`CONCURRENTLY` requires a unique index on the materialized view. It takes longer but allows concurrent reads and writes.

### WITH DATA / WITH NO DATA

Control whether the materialized view is populated on creation:

| Option | Behaviour |
|--------|-----------|
| `WITH DATA` (default) | Populate immediately |
| `WITH NO DATA` | Create empty; must `REFRESH` before querying |

```python
class Meta(PGViewMeta):
    pg_view_query = "..."
    pg_view_materialized = True
    pg_view_with_data = False
```

## Schema-Qualified Views

```python
class Meta(PGViewMeta):
    pg_view_query = "SELECT id, email FROM users"
    pg_view_materialized = False
    pg_schema = "app"
```

```sql
CREATE OR REPLACE VIEW app.active_users AS SELECT id, email FROM users;
```

## Recursive Views

```sql
CREATE RECURSIVE VIEW view_name (col1, col2, ...) AS
    SELECT ...   -- non-recursive term
    UNION ALL
    SELECT ...   -- recursive term
;
```

Recursive views are declared via `pg_view_query` with the full recursive query text.

## Temporary Views

```sql
CREATE TEMPORARY VIEW temp_active_users AS SELECT * FROM active_users;
```

Temporary views are session-scoped and dropped automatically at session end.

## Updatable Views

PostgreSQL automatically makes simple views updatable (single table, no aggregates, no DISTINCT, no set operations). Complex views require `INSTEAD OF` triggers for updates.

## Indexes on Materialized Views

Materialized views support indexes. Create indexes directly on the materialized view table:

```python
class Meta(PGViewMeta):
    pg_view_query = "..."
    pg_view_materialized = True
    bg_view_materialized = True  # Note: use model's Meta if needed
```

Indexes on materialized views are created via `pg_indexes` on `PGTableMeta` (not `PGViewMeta`). For materialized views with `pg_view_auto_refresh`, indexes persist across refreshes.

## INSTEAD OF Triggers

For complex views that need to support INSERT/UPDATE/DELETE, use `INSTEAD OF` triggers. `pg_triggers` requires the `dbwarden-pgsql-extensions` plugin (`dbwarden plugin add dbwarden-pgsql-extensions`).

```python
pg_triggers=[{
    "name": "trg_active_users_insert",
    "table": "active_users",
    "function": "insert_active_user",
    "timing": "INSTEAD OF",
    "events": ["INSERT"],
    "for_each": "ROW",
}]
```

See [Functions & Triggers](functions-and-triggers.md) for trigger configuration.

## Lifecycle

| Operation | DDL |
|-----------|-----|
| Create regular view | `CREATE OR REPLACE VIEW name AS query;` |
| Create matview | `CREATE MATERIALIZED VIEW name AS query;` |
| Refresh matview | `REFRESH MATERIALIZED VIEW name;` |
| Refresh matview concurrently | `REFRESH MATERIALIZED VIEW CONCURRENTLY name;` |
| Drop | `DROP VIEW IF EXISTS name CASCADE;` / `DROP MATERIALIZED VIEW IF EXISTS name CASCADE;` |
