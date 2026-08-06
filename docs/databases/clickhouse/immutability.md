# Immutability: what can never change

This page is the single most important thing to read before writing your first ClickHouse model. PG users expect most properties to be mutable via `ALTER`. ClickHouse is different: many properties are design-time commitments that can never be changed, and others require a full table rebuild.

## Model example: table with immutable properties

```python
class Orders(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column()
    amount: Mapped[float] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=merge_tree(),
            order_by=["created_at", "id"],
            partition_by="toYYYYMM(created_at)",
            primary_key=["id"],
            sample_by="intHash64(id)",
        )
```

Once applied, `partition_by`, `primary_key`, `sample_by` and `engine` can never be altered. Only `order_by` can be extended by appending new columns: `["created_at", "id"]` → `["created_at", "id", "status"]`.

## What can never change

| Property | Constraint | Mechanism |
|----------|------------|-----------|
| `PARTITION BY` | Cannot be altered after creation | ClickHouse does not support `ALTER TABLE MODIFY PARTITION BY`. The only fix is a full table rebuild. |
| `PRIMARY KEY` | Cannot be altered | Unlike PG where you can `ALTER TABLE ... SET WITHOUT CLUSTER`, ClickHouse's primary key is baked into the storage order at creation. |
| `SAMPLE BY` | Cannot be altered | Same as partition by: set at CREATE time only. |
| `ENGINE` | Cannot be `ALTER`ed | Changing `MergeTree` to `ReplicatedMergeTree` (or vice versa) requires a full data copy. See below. |

## What extends only

| Property | Behavior | Example |
|----------|----------|---------|
| `ORDER BY` | New columns can be appended to the end. Existing columns cannot be removed or reordered. | `ORDER BY (a, b)` → `ORDER BY (a, b, c)` is valid. `ORDER BY (b, a)` is not. |

This is enforced by dbwarden: the differ refuses an `ORDER BY` change that is not an append-only extension. If you try, you get a CRITICAL-safety classification and must use `--force` to trigger a recreate.

## Model example: change requiring recreate

This ORDER BY change is refused by dbwarden:

```python
# Current model
class Meta(CHTableMeta):
    ch = ch_table(
        engine=replicated_merge_tree("/zk/orders", "{replica}"),
        order_by=["created_at", "id"],
    )

# Attempted change (reordered, not append-only)
class Meta(CHTableMeta):
    ch = ch_table(
        engine=replicated_merge_tree("/zk/orders", "{replica}"),
        order_by=["id", "created_at"],  # REORDERED: not append-only
    )
```

dbwarden emits: `CRITICAL: Changing ORDER BY from (created_at, id) to (id, created_at) requires --force`. The correct extension:

```python
class Meta(CHTableMeta):
    ch = ch_table(
        order_by=["created_at", "id", "status"],  # append-only: OK
    )
```

## What requires `--force` (recreate)

These properties trigger the recreate pipeline (DETACH source → CREATE new → INSERT INTO ... SELECT → RENAME → ATTACH) when changed:

| Change | Safety |
|--------|--------|
| Engine change (including ZK path / replica name) | CRITICAL |
| ORDER BY non-extension change (remove/reorder columns) | CRITICAL |
| PRIMARY KEY change | CRITICAL |
| PARTITION BY change | CRITICAL |
| SAMPLE BY change | INFO |
| Object type change (`table` ↔ `materialized_view`) | CRITICAL |
| MV target (`ch_to_table`) change | CRITICAL |
| Column type change (incompatible) | CRITICAL |
| LowCardinality / Nullable wrapper change | CRITICAL |

### Model example: full recreate with AggregateFunction

```python
# Source column type change triggers MV recreate
# Current: amount is Float64
class Meta(CHTableMeta):
    ch = ch_table(
        engine=merge_tree(),
        order_by="date",
        ch_select="SELECT date, agg.sumState(amount) AS state FROM events GROUP BY date",
    )

# If amount changes to Float32, the AggregateFunction signature changes:
#   AggregateFunction(sum, Float64) -> AggregateFunction(sum, Float32)
# This is CRITICAL and requires --force + recreate
```

**The 40GB-vs-500GB rebuild argument.** A 40GB table recreates in minutes. A 500GB table needs planning: provision a second table, backfill, verify, swap. dbwarden's recreate pipeline handles the orchestration but the cost is real. Test on a staging environment first.

## AggregateFunction signatures are incompatible state

`AggregateFunction(sum, Float64)` and `AggregateFunction(sum, Float32)` are different types. An MV that selects `sum(value)` where `value` is `Float64` produces `AggregateFunction(sum, Float64)`. If the source column type changes, the MV's aggregate type is locked: it cannot be `ALTER`ed to `Float32`. The only fix is to drop and recreate the MV.

This is not a dbwarden limitation: it is ClickHouse's columnar storage model. dbwarden will flag it as a CRITICAL change requiring a recreate.

## What dbwarden refuses entirely

dbwarden will not emit `ALTER TABLE ... MODIFY ORDER BY` for a non-extension change. It will refuse to emit `ALTER TABLE ... MODIFY PARTITION BY` (ClickHouse doesn't support it). It will refuse to emit an engine change without the recreate flag.

The error messages name the constraint and the flag required to override:
```
CRITICAL: Changing ORDER BY from (a, b) to (c, d) requires --force and
triggers a full table recreate (DETACH -> CREATE -> INSERT -> ATTACH).
```
