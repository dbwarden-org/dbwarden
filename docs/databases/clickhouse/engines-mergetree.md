# MergeTree engine family

## Factories

All MergeTree variants have typed factory functions in `dbwarden.databases.clickhouse`:

```python
from dbwarden.databases.clickhouse import (
    merge_tree, replacing_merge_tree, replicated_merge_tree,
    summing_merge_tree, aggregating_merge_tree,
    collapsing_merge_tree, versioned_collapsing_merge_tree,
    graphite_merge_tree,
)
```

| Factory | Engine name | Signature |
|---------|-------------|-----------|
| `merge_tree()` | `MergeTree` | `()` |
| `replacing_merge_tree(ver?)` | `ReplacingMergeTree` | `(version_col: str \| None = None)` |
| `replicated_merge_tree(zk, replica, ...)` | `ReplicatedMergeTree` | `(zookeeper_path, replica_name, *args)` |
| `summing_merge_tree(...)` | `SummingMergeTree` | `(*columns: str)` |
| `aggregating_merge_tree()` | `AggregatingMergeTree` | `()` |
| `collapsing_merge_tree(sign)` | `CollapsingMergeTree` | `(sign_col: str)` |
| `versioned_collapsing_merge_tree(sign, ver)` | `VersionedCollapsingMergeTree` | `(sign_col, version_col)` |
| `graphite_merge_tree(section?)` | `GraphiteMergeTree` | `(config_section: str = "default")` |

Example:

```python
class Meta(CHTableMeta):
    ch = ch_table(
        engine=replicated_merge_tree(
            "/clickhouse/tables/events",
            "{replica}",
            "ver",
        ),
        order_by=["event_date", "id"],
    )
```

Generated DDL:

```sql
CREATE TABLE events (
    event_date Date,
    id Int64
) ENGINE = ReplicatedMergeTree('/clickhouse/tables/events', '{replica}', 'ver')
ORDER BY (event_date, id)
```

## MergeTreeSettings

`ch_table(settings=MergeTreeSettings(...))` type-checks known MergeTree settings:

```python
from dbwarden.databases.clickhouse import MergeTreeSettings

settings: MergeTreeSettings = {
    "index_granularity": 8192,
    "ttl_only_drop_parts": True,
    "min_bytes_for_wide_part": 10485760,
}
```

Boolean values are automatically converted to `"0"` / `"1"`. Integer values are stringified. All values are rendered as strings before reaching the server.

## What changes are allowed

| Property | Allowed |
|----------|---------|
| Engine variant | Only with `--force` (full recreate) |
| ZK path / replica name | Only with `--force` |
| Settings | Any key-value via `MODIFY SETTING` (where supported by server) |
| ORDER BY | Append-only |
| PARTITION BY | Never |

## Additional model examples

### ReplacingMergeTree with version column

```python
class Product(Base):
    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column()
    price: Mapped[float] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=replacing_merge_tree(version_col="updated_at"),
            order_by="sku",
        )
```

Deduplicates by `sku`, keeping the row with the latest `updated_at`.

### CollapsingMergeTree for mutable state

```python
class OrderState(Base):
    __tablename__ = "order_state"

    order_id: Mapped[str] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column()
    amount: Mapped[float] = mapped_column()
    sign: Mapped[int8] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=collapsing_merge_tree(sign_col="sign"),
            order_by="order_id",
        )
```

Cancellations emit a row with `sign = -1` that collapses with the original `sign = 1`.

### SummingMergeTree

```python
class DailySummary(Base):
    __tablename__ = "daily_summary"

    dt: Mapped[date] = mapped_column()
    product: Mapped[str] = mapped_column()
    revenue: Mapped[float] = mapped_column()
    units: Mapped[int] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=summing_merge_tree("revenue"),
            order_by=["dt", "product"],
        )
```

`revenue` and `units` are summed automatically during merge.

### GraphiteMergeTree

```python
class GraphiteMetrics(Base):
    __tablename__ = "graphite_metrics"

    path: Mapped[str] = mapped_column()
    value: Mapped[float] = mapped_column()
    timestamp: Mapped[datetime] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=graphite_merge_tree(config_section="rollup_default"),
            order_by=["path", "timestamp"],
            partition_by="toYYYYMM(timestamp)",
        )
```

## Rollback behavior

Engine changes with `--force` trigger the full recreate pipeline. See [Safety](safety.md).
