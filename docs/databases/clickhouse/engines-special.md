# Special engines

These engines do not participate in `ORDER BY`: they are storage-format-specific serverside readers.

## Factories

```python
from dbwarden.databases.clickhouse import (
    null, memory, merge,
    set_engine, join_engine, dictionary_engine,
    log, tiny_log, stripe_log,
)
```

## Additional model examples

### Null engine as MV sink

```python
class NullSink(Base):
    __tablename__ = "null_sink"

    payload: Mapped[str] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=null(),
        )

class ViewFromSink(Base):
    __tablename__ = "view_from_sink"

    value: Mapped[int] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=merge_tree(),
            order_by="value",
            ch_to_table="sink_dest",
            ch_select="SELECT count(*) AS value FROM null_sink",
        )
```

### Merge engine for partitioned read

```python
class AllEvents(Base):
    __tablename__ = "all_events"

    date: Mapped[date] = mapped_column()
    payload: Mapped[str] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=merge(
                source_database="analytics",
                table_regex="events_202[0-9]_*",
            ),
        )
```

### Dictionary engine with explicit dictionary

```python
class CountryDict(Base):
    __tablename__ = "country_dict"

    code: Mapped[str] = mapped_column()
    name: Mapped[str] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(engine=dictionary_engine())
```

The dictionary is declared separately via `ch_dictionary()`. See [Dictionaries](dictionaries.md).

## Null

```python
engine = null()
```

DDL: `ENGINE = Null`. Accepts any data and discards it. Used as the target of a materialized view that does its own aggregation.

## Memory

```python
engine = memory()
```

DDL: `ENGINE = Memory`. In-memory storage, lost on restart. Schema management only.

## Merge

```python
engine = merge(
    source_database="analytics",
    table_regex="events_.*",
)
```

DDL: `ENGINE = Merge('analytics', 'events_.*')`. A virtual table that reads from multiple tables whose names match the regex.

## Set

```python
engine = set_engine()
```

DDL: `ENGINE = Set`. Always in-memory. Use for IN-query acceleration.

## Join

```python
engine = join_engine(
    join_type="LEFT",
    strictness="ALL",
)
```

DDL: `ENGINE = Join(LEFT, ALL)`. Specialized for JOIN queries.

## Dictionary

```python
engine = dictionary_engine()
```

DDL: `ENGINE = Dictionary(<dict_name>)`. References a [Dictionary](dictionaries.md) object by name.

## Log, TinyLog, StripeLog

```python
engine = log()
engine = tiny_log()
engine = stripe_log()
```

DDLs: `ENGINE = Log`, `TinyLog`, `StripeLog`. Append-only file-based storage. No ORDER BY, no parts merging. StripeLog is multithreaded on read; TinyLog is the simplest.

## What changes are allowed

These engines have no ORDER BY, so immutability rules don't apply in the same way. An engine change (e.g., Memory → MergeTree) requires `--force` and a recreate.

| Change | Safety |
|--------|--------|
| Engine variant | CRITICAL with `--force` |
| Merge source/target | INFO |
| Join type/strictness | WARN |

## Rollback behavior

Engine changes trigger recreate. See [Safety](safety.md) for the pipeline.
