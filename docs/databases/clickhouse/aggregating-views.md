# Aggregating views (`AggregatingMergeTree`)

## Overview

An aggregating view is a coherent triad:

1. An **AggregatingMergeTree target table** whose columns have
   `AggregateFunction(...)` types derived from aggregate expressions.
2. A **materialized view** that uses `<func>State(...)` combinators in its
   SELECT, `TO` the target.
3. The **source table** (referenced, not created; it must already exist).

Because the target column types and the MV combinators both derive from the
same single list of `AggExpr`, they are guaranteed consistent; the
correspondence that is manual and drift-prone in the string-SELECT form is
here derived and safe.

## Declaring aggregating views

Use `AggregatingView` as the base class, `aggregating_view()` in `Meta`:

```python
from sqlalchemy import func
from dbwarden.databases.clickhouse import AggregatingView, CHViewMeta, aggregating_view, agg

class EventsHourly(AggregatingView):
    __tablename__ = "events_hourly"

    class Meta(CHViewMeta):
        ch = aggregating_view(
            source=Event,
            group_by=[func.toStartOfHour(Event.event_time).label("hour")],
            aggregates=[
                agg.sum(Event.amount).as_("total_amount"),
                agg.count().as_("event_count"),
            ],
            order_by=["hour"],
            partition_by="toYYYYMM(hour)",
        )
```

This generates:

1. The target table `events_hourly` with `AggregatingMergeTree` engine and
   columns `hour DateTime`, `total_amount AggregateFunction(sum, Float64)`,
   `event_count AggregateFunction(count)`.
2. A materialized view `events_hourly_mv TO events_hourly` whose SELECT uses
   `sumState`, `countState`; automatically derived from the `AggExpr` list.
3. The MV reads FROM `Event`.

### The source parameter

`source` may be:

- A model class (preferred); its `__tablename__` is resolved at spec
  construction time.
- A string class name (forward reference); resolved at discovery time when
  all models are loaded.
- A bare table name string; used as-is.

When you pass a model class, rename safety is automatic: if the source table
is renamed, regeneration picks up the new name.

### Aggregate expressions

Every aggregate must have an alias (`.as_(name)`):

```python
aggregates=[
    agg.count().as_("event_count"),
    agg.sum("amount", "Float64").as_("total_amount"),
    agg.uniq("user_id").as_("unique_users"),
]
```

Supported aggregate functions include `sum`, `count`, `min`, `max`, `avg`,
`uniq`, `uniq_exact`, `any`, `any_last`, `groupArray`, `groupUniqArray`,
`quantile`, and the `raw` escape hatch for combinators not enumerated above.

## Configuring the target table

The `AggregatingViewSpec` is a frozen dataclass. Configure it via
`aggregating_view()` keyword arguments:

| Parameter | Description |
|-----------|-------------|
| `source` | Source model class or table name |
| `group_by` | GROUP BY keys: ColumnElement or string |
| `aggregates` | AggExpr list (each with `.as_()`) |
| `order_by` | ORDER BY for the target |
| `partition_by` | Optional PARTITION BY |
| `ttl` | Optional TTL expression(s) |
| `settings` | Optional engine SETTINGS |

## Full example

```python
from sqlalchemy import func, String
from dbwarden.databases.clickhouse import (
    AggregatingView, CHViewMeta, aggregating_view, agg,
)

class EventStats(AggregatingView):
    __tablename__ = "event_stats"

    class Meta(CHViewMeta):
        ch = aggregating_view(
            source=PageView,
            group_by=[
                PageView.url.label("url"),
                func.toDate(PageView.viewed_at).label("day"),
            ],
            aggregates=[
                agg.count().as_("views"),
                agg.uniq(PageView.session_id).as_("unique_sessions"),
                agg.sum(PageView.duration).as_("total_duration"),
            ],
            order_by=["url", "day"],
            partition_by="toYYYYMM(day)",
            ttl="day + INTERVAL 90 DAY DELETE",
        )
```

## Populating an aggregating view

Use the `populate()` helper from `data_ops` to generate an `INSERT ... SELECT`
DataOp that backfills the target table:

```python
from dbwarden.databases.clickhouse import data_ops, AggregatingView

pop = data_ops.populate(EventStats.Meta.ch)
```

This produces a `DataOp` whose forward SQL is equivalent to:

```sql
INSERT INTO event_stats
SELECT
    url,
    toDate(viewed_at) AS day,
    countState() AS views,
    uniqState(session_id) AS unique_sessions,
    sumState(duration) AS total_duration
FROM page_view
GROUP BY url, toDate(viewed_at)
```

## Discoverability

`AggregatingView` subclasses are automatically registered in
`ChView._ch_view_registry` and discovered by `ch_view_tables_from_models()`.
They contribute both the aggregating target model and the materialized view to
the model list.
