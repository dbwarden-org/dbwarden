# Declaring tables

## The `ch_table()` builder (preferred path)

Use `ch_table()` inside a `class Meta(CHTableMeta)` block. This is the primary, recommended API:

```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from dbwarden.databases.clickhouse import (
    CHTableMeta, merge_tree, ch_table,
)

class Base(DeclarativeBase):
    pass

class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_date: Mapped[date] = mapped_column()
    payload: Mapped[str] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=merge_tree(),
            order_by=["event_date", "id"],
            partition_by="toYYYYMM(event_date)",
            ttl=["event_date + toIntervalYear(1)"],
            settings={"index_granularity": "8192"},
        )
```

`ch_table()` returns a `ChTableSpec` dataclass. Full signature:

| Parameter | Type | SQL |
|-----------|------|-----|
| `engine` | `ChEngineSpec` | `ENGINE = MergeTree()` |
| `order_by` | `str` or `list[str]` | `ORDER BY (col1, col2)` |
| `primary_key` | `str` or `list[str]` | `PRIMARY KEY (col1)` |
| `partition_by` | `str` | `PARTITION BY toYYYYMM(col)` |
| `sample_by` | `str` | `SAMPLE BY intHash64(col)` |
| `ttl` | `str` or `list[str]` | `TTL expr1, expr2` |
| `settings` | `MergeTreeSettings` dict | `SETTINGS key=value` |
| `projections` | `list[ProjectionSpec]` | `PROJECTION name (SELECT ...)` |
| `indexes` | `list[ChIndexSpec]` | `ALTER TABLE ... ADD INDEX ...` |

## Generated DDL

```sql
CREATE TABLE IF NOT EXISTS events (
    id Int64,
    event_date Date,
    payload String
) ENGINE = MergeTree()
ORDER BY (event_date, id)
PARTITION BY toYYYYMM(event_date)
TTL event_date + toIntervalYear(1)
SETTINGS index_granularity = 8192;
```

## Loose `ch_*` attrs (legacy)

Individual `ch_engine`, `ch_order_by`, etc. class attributes on `Meta` still work but are **deprecated**:

```python
class Meta(CHTableMeta):
    ch_engine = ChEngineSpec("MergeTree")       # deprecated
    ch_order_by = ["event_date", "id"]           # deprecated
    ch_partition_by = "toYYYYMM(event_date)"     # deprecated
```

**Migration path:** Replace all loose `ch_*` attrs with a single `ch = ch_table(...)` assignment. Both forms coexist in the same codebase during migration; the loose path emits a `DeprecationWarning`.

## Additional model examples

### Replicated table with custom settings

```python
class ReplicatedEvents(Base):
    __tablename__ = "replicated_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column()
    value: Mapped[str] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=replicated_merge_tree(
                "/clickhouse/tables/{shard}/replicated_events",
                "{replica}",
            ),
            order_by=["ts", "id"],
            partition_by="toYYYYMM(ts)",
            ttl=["ts + toIntervalMonth(3)"],
            settings={
                "index_granularity": 4096,
                "min_bytes_for_wide_part": 10485760,
            },
        )
```

### Table with multiple projections and indexes

```python
class AnalyticsEvents(Base):
    __tablename__ = "analytics_events"

    dt: Mapped[date] = mapped_column()
    user_id: Mapped[int] = mapped_column()
    event_type: Mapped[str] = mapped_column()
    amount: Mapped[float] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=summing_merge_tree("amount"),
            order_by=["dt", "user_id", "event_type"],
            partition_by="toYYYYMM(dt)",
            settings={"allow_experimental_full_text_index": 1},
            projections=[
                ch_projection(
                    name="user_summary",
                    select=["user_id", "sum(amount)", "count()"],
                    group_by=["user_id"],
                ),
            ],
            indexes=[
                ch_index(
                    name="type_bloom",
                    expression="event_type",
                    type="bloom_filter(0.02)",
                ),
            ],
        )
```

### Distributed table

```python
class DistributedEvents(Base):
    __tablename__ = "distributed_events"

    id: Mapped[int] = mapped_column(primary_key=True)

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=distributed_engine(
                cluster="analytics_cluster",
                database="analytics",
                table="events",
                sharding_key="rand()",
            ),
        )
```

## What changes are allowed

See [Immutability](immutability.md) for the full rules.

| Property | Allowed changes |
|----------|----------------|
| `order_by` | Append-only extension |
| `settings` | Any key-value change |
| `ttl` | Any expression change |
| `projections` | Add/drop by name |
| `indexes` | Add/drop by name |
| `engine` | Only with `--force` (recreate) |
| `partition_by` | Never |
| `primary_key` | Only with `--force` (recreate) |
| `sample_by` | Never |

## Rollback behavior

Every `ALTER` and `CREATE` statement has a rollback. The rollback for a CREATE is DROP. The rollback for an ALTER is the inverse ALTER. For recreates, the rollback restores the original table.

See [Safety](safety.md) for the full rebuild pipeline.
