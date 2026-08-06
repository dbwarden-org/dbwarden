# Columns and types

## Column-level Meta

Use `CHColumnMeta` inner classes on the model, named after the column:

```python
from dbwarden.databases.clickhouse import CHTableMeta, CHColumnMeta, ch

class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[str] = mapped_column()
    event_time: Mapped[datetime] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(engine=merge_tree(), order_by="event_time")

        class payload(CHColumnMeta):
            ch = ch.field(codec="ZSTD(3)")

        class event_time(CHColumnMeta):
            ch = ch.field(default_expression="now()")
```

Generated DDL:

```sql
CREATE TABLE events (
    id Int64,
    payload String CODEC(ZSTD(3)),
    event_time DateTime DEFAULT now()
) ENGINE = MergeTree() ORDER BY event_time
```

## Additional model examples

### Column with codec and LowCardinality

```python
class UserSession(Base):
    __tablename__ = "user_sessions"

    user_id: Mapped[int] = mapped_column(primary_key=True)
    browser: Mapped[str] = mapped_column()
    ip: Mapped[str] = mapped_column()
    duration: Mapped[int] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(engine=merge_tree(), order_by="user_id")

        class browser(CHColumnMeta):
            ch = ch.field(
                codec="ZSTD(3)",
                low_cardinality=True,
            )

        class ip(CHColumnMeta):
            ch = ch.field(
                codec="LZ4HC(9)",
                nullable=True,
            )

        class duration(CHColumnMeta):
            ch = ch.field(
                default_expression="0",
                ttl="now() - toIntervalDay(30)",
            )
```

Generated DDL:

```sql
CREATE TABLE user_sessions (
    user_id Int64,
    browser LowCardinality(String) CODEC(ZSTD(3)),
    ip Nullable(String) CODEC(LZ4HC(9)),
    duration Int64 DEFAULT 0 TTL now() - toIntervalDay(30)
) ENGINE = MergeTree() ORDER BY user_id
```

### Column with alias and comment

```python
class Metrics(Base):
    __tablename__ = "metrics"

    width: Mapped[float] = mapped_column()
    height: Mapped[float] = mapped_column()
    area: Mapped[float] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(engine=merge_tree(), order_by="width")

        class area(CHColumnMeta):
            ch = ch.field(
                alias="width * height",
                comment="Calculated area in pixels",
            )
```

### REMOVE clause example

```python
# Removing a codec from a column
class Meta(CHTableMeta):
    ch = ch_table(engine=merge_tree(), order_by="id")

    class payload(CHColumnMeta):
        ch = ch.field(codec=None)  # emits MODIFY COLUMN payload REMOVE CODEC
```

## `ch.field()` options

| Parameter | Type | SQL | REMOVE form |
|-----------|------|-----|-------------|
| `codec` | `str` | `CODEC(ZSTD(3))` | `MODIFY COLUMN c REMOVE CODEC` |
| `default_expression` | `str` | `DEFAULT now()` | `MODIFY COLUMN c REMOVE DEFAULT` |
| `materialized` | `str` | `MATERIALIZED expr` | `MODIFY COLUMN c REMOVE MATERIALIZED` |
| `alias` | `str` | `ALIAS expr` | `MODIFY COLUMN c REMOVE ALIAS` |
| `ephemeral` | `str` | `EPHEMERAL expr` | `MODIFY COLUMN c REMOVE EPHEMERAL` |
| `ttl` | `str` | `TTL expr` | `MODIFY COLUMN c REMOVE TTL` |
| `low_cardinality` | `bool` | `LowCardinality(String)` | Wrapped into type |
| `nullable` | `bool` | `Nullable(String)` | Wrapped into type |
| `comment` | `str` | `COMMENT ON COLUMN` | `MODIFY COLUMN c REMOVE COMMENT` |

Setting a property to `None` (or omitting it) and then setting it to a value emits `MODIFY COLUMN ... <property>`. The reverse (removing a property) emits the `REMOVE` form. This is a write-only asymmetry in ClickHouse that dbwarden handles for you.

## Type normalization

SQLAlchemy types are normalized to ClickHouse native types:

| SQLAlchemy type | ClickHouse type |
|----------------|-----------------|
| `Integer`, `BIGINT` | `Int32`, `Int64` |
| `VARCHAR`, `String` | `String` |
| `FLOAT(53)`, `REAL` | `Float64`, `Float32` |
| `NUMERIC(p,s)` | `Decimal(p,s)` |
| `BOOLEAN` | `Bool` |
| `ARRAY(Integer)` | `Array(Int32)` |
| `Enum` | `Enum8` / `Enum16` |
| `UUID` | `UUID` |
| `JSON` | `JSON` |
| `DATETIME`, `DATETIME64` | `DateTime`, `DateTime64` |

## What changes are allowed

| Change | Safety | Notes |
|--------|--------|-------|
| Add column | INFO | Standard `ALTER TABLE ADD COLUMN` |
| Drop column | WARN | Requires `--force` |
| Type change (compatible) | INFO | e.g. `Int32` → `Int64` |
| Type change (incompatible) | CRITICAL | e.g. `String` → `Int64`, requires `--force` + recreate |
| Codec change | WARN | Relatively cheap |
| Default / Materialized / Alias change | WARN | |
| TTL change | WARN | |
| LowCardinality / Nullable toggle | CRITICAL | Requires `--force` |
| Comment change | INFO | |
| REMOVE any property | INFO | Emitted as `MODIFY COLUMN c REMOVE ...` |

## Rollback behavior

Column changes emit inverse operations. `ADD COLUMN` rolls back as `DROP COLUMN`. A `MODIFY COLUMN` rolls back as `MODIFY COLUMN` with the previous state.
