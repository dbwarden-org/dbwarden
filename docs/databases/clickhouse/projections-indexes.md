# Projections & indexes

Both projections and secondary indexes are acceleration structures, declared inside `ch_table()`.

## Projections

```python
from dbwarden.databases.clickhouse import projection

class Meta(CHTableMeta):
    ch = ch_table(
        engine=merge_tree(),
        order_by=["event_date", "id"],
        projections=[
            projection(
                name="daily_agg",
                query="SELECT event_date, count(), sum(amount) GROUP BY event_date",
            ),
        ],
    )
```

Generated DDL (the projection is part of CREATE TABLE or added via ALTER):

```sql
CREATE TABLE events (
    event_date Date,
    id Int64,
    amount Float64,
    PROJECTION daily_agg
    (
        SELECT event_date, count(), sum(amount)
        GROUP BY event_date
        ORDER BY event_date
    )
) ENGINE = MergeTree()
ORDER BY (event_date, id)
```

`ch_projection()` parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Projection name |
| `select` | `list[str]` | SELECT expressions |
| `group_by` | `list[str]` | GROUP BY columns |
| `order_by` | `list[str]` | ORDER BY within projection |

## Skip indexes

```python
from dbwarden.databases.clickhouse import skip_index

class Meta(CHTableMeta):
    ch = ch_table(
        engine=merge_tree(),
        order_by=["event_date", "id"],
        indexes=[
            skip_index(
                name="payload_idx",
                columns=["payload"],
                type="tokenbf_v1(1024, 3, 0)",
                granularity=1,
            ),
            ch_index(
                name="date_bloom",
                expression="event_date",
                type="bloom_filter(0.05)",
                granularity=4,
            ),
        ],
    )
```

Generated DDL:

```sql
CREATE TABLE events (
    payload String,
    INDEX payload_idx payload TYPE tokenbf_v1(1024, 3, 0) GRANULARITY 1,
    INDEX date_bloom event_date TYPE bloom_filter(0.05) GRANULARITY 4
) ENGINE = MergeTree()
ORDER BY (event_date, id)
```

`ch_index()` parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Index name |
| `expression` | `str` | Column or expression to index |
| `type` | `str` | Index type + parameters |
| `granularity` | `int` | Number of granules (default 1) |

Supported index types: `minmax`, `set(max_rows)`, `bloom_filter(false_positive)`, `ngrambf_v1(n, size, hashes, seed)`, `tokenbf_v1(size, hashes, seed)`, `hypothesis`, `inverted` (experimental), `vector_similarity` (experimental).

## Additional model examples

### Two projections on one table

```python
class Orders(Base):
    __tablename__ = "orders"

    dt: Mapped[date] = mapped_column()
    product: Mapped[str] = mapped_column()
    category: Mapped[str] = mapped_column()
    revenue: Mapped[float] = mapped_column()
    qty: Mapped[int] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=merge_tree(),
            order_by=["dt", "product"],
            partition_by="toYYYYMM(dt)",
            projections=[
                ch_projection(
                    name="category_summary",
                    select=["category", "sum(revenue)", "sum(qty)"],
                    group_by=["category"],
                ),
                ch_projection(
                    name="product_top",
                    select=["product", "sum(revenue)"],
                    group_by=["product"],
                    order_by=["sum(revenue) DESC"],
                ),
            ],
        )
```

### Multiple index types

```python
class LogSearch(Base):
    __tablename__ = "log_search"

    ts: Mapped[datetime] = mapped_column()
    level: Mapped[str] = mapped_column()
    message: Mapped[str] = mapped_column()
    ip: Mapped[str] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=merge_tree(),
            order_by=["ts", "ip"],
            indexes=[
                ch_index(
                    name="level_idx",
                    expression="level",
                    type="set(10)",
                    granularity=4,
                ),
                ch_index(
                    name="msg_bloom",
                    expression="message",
                    type="bloom_filter(0.01)",
                    granularity=1,
                ),
                ch_index(
                    name="ip_minmax",
                    expression="ip",
                    type="minmax",
                    granularity=8,
                ),
            ],
        )
```

### MATERIALIZE workflow

```python
# 1. Add projection to model
# 2. Generate migration (ADD PROJECTION is INFO)
# 3. Apply migration
dbwarden migrate -d analytics
# 4. Materialize on existing data
from dbwarden.databases.clickhouse import data_op
data_op("ALTER TABLE orders MATERIALIZE PROJECTION category_summary")
```

## MATERIALIZE as data operation

Indexes and projections written to new parts automatically, but existing parts need a `MATERIALIZE` operation:

```sql
ALTER TABLE events MATERIALIZE INDEX payload_idx
ALTER TABLE events MATERIALIZE PROJECTION daily_agg
```

dbwarden treats `MATERIALIZE` as a [data operation](data-operations.md), not DDL:

```python
with data_ops() as ops:
    ops.materialize_index("events", "payload_idx")
    ops.materialize_projection("events", "daily_agg")
```

## What changes are allowed

| Change | Safety |
|--------|--------|
| Add projection | INFO (new parts only; existing need MATERIALIZE) |
| Drop projection | WARN |
| Add index | INFO (new parts only; existing need MATERIALIZE) |
| Drop index | WARN |
| Change projection definition | CRITICAL: requires drop + recreate |
| Change index definition | CRITICAL: requires drop + recreate |

## Rollback behavior

Projections and indexes follow ALTER semantics: ADD rolls back as DROP and vice versa. MATERIALIZE is a data-op that is not structurally reversible: it is idempotent in practice.
