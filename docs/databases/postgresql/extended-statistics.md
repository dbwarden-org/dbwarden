# Extended Statistics

**Requires the `dbwarden-pgsql-extensions` plugin:** `dbwarden plugin add dbwarden-pgsql-extensions`. The `extended_statistics` object handler ships in that plugin, not in core.

**Handler**: `ExtendedStatisticsHandler` (PREAMBLE phase, config-driven, PG 14+)

Extended statistics give the query planner better estimates for correlated columns.

```python
pg_extended_statistics=[
    {
        "name": "stats_users_email_city",
        "table": "users",
        "kinds": ["d", "f"],
        "columns": "email, city",
    },
]
```

## Kind Codes

| Code | Kind | Description | PG Version |
|------|------|-------------|------------|
| `d` | ndistinct | Distinct-value counts for column groups | 10+ |
| `f` | dependencies | Functional dependency statistics | 10+ |
| `m` | MCV | Most-common-values lists | 10+ |
| `e` | expressions | Expression statistics (PG 14+) | 14+ |

## Lifecycle

| Operation | DDL |
|-----------|-----|
| Create | `CREATE STATISTICS name (kinds) ON columns FROM table;` |
| Alter | `ALTER STATISTICS name SET STATISTICS target;` |
| Drop | `DROP STATISTICS IF EXISTS name;` |
| Analyze | `ANALYZE table;` (required to populate statistics) |

### ALTER STATISTICS

The statistics target controls sample size. Higher values produce better estimates at the cost of longer `ANALYZE` time:

```sql
ALTER STATISTICS stats_users_email_city SET STATISTICS 1000;
```

Values range from `-1` (use system default, typically 100) to `10000`.

### ANALYZE Requirements

`CREATE STATISTICS` defines the statistics object but does not populate it. Run `ANALYZE` on the table against the database directly to collect the first statistics sample:

```sql
ANALYZE users;
```

## Examples

```python
# ndistinct + dependencies on correlated columns
{"name": "stats_order_date_status", "table": "orders",
 "kinds": ["d", "f"], "columns": "order_date, status"}

# MCV on a high-cardinality group
{"name": "stats_product_category", "table": "products",
 "kinds": ["m"], "columns": "category_id, price"}

# Expression statistics (PG 14+)
{"name": "stats_user_email_domain", "table": "users",
 "kinds": ["d", "m"], "expressions": ["lower(email)"]}
```

Generated DDL:
```sql
CREATE STATISTICS stats_users_email_city (ndistinct, dependencies) ON email, city FROM users;
```

## Schema Support

Extended statistics can be scoped to a schema:

```python
{"name": "stats_order_date_status", "table": "orders",
 "schema": "app", "kinds": ["d", "f"], "columns": "order_date, status"}
```

## Migration Safety

| Change | Severity |
|--------|----------|
| Add extended statistic | `INFO` |
| Drop extended statistic | `WARNING` |
| Change kinds / columns | `WARNING` |
| Change statistics target | `INFO` |

See [Migration Safety](migration-safety.md) for the full classification table.
