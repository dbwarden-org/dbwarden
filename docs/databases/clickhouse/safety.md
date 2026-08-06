# Safety classification

Every change dbwarden detects is classified. Destructive operations require `--force` and emit warnings with reasons.

## Classification levels

| Level | Color | Behavior |
|-------|-------|----------|
| `INFO` | Green | Applied automatically. Safe metadata changes: ADD COLUMN, ADD INDEX, ADD PROJECTION, SETTING changes, TTL changes. |
| `WARN` | Yellow | Applied automatically but logged as a warning. Attention recommended: DROP COLUMN, DROP TABLE, DROP INDEX, mutations, partition DROP/REPLACE. |
| `CRITICAL` | Red | Skipped unless `--force` is passed. Requires explicit acknowledgement: engine changes, ORDER BY non-extension, PRIMARY KEY changes, MV TO target change, type incompatibility, LowCardinality/Nullable toggle. |

## The `--force` flag

```bash
# Preview what would run
dbwarden make-migrations --plan --force -d analytics

# Apply with force
dbwarden migrate --force -d analytics
```

`--force` is all-or-nothing for CRITICAL items in the plan. There is no per-item override.

## The recreate pipeline

When a CRITICAL change requires a full table rebuild, dbwarden executes:

```
DETACH TABLE source
CREATE TABLE source_new (...new definition...)
INSERT INTO source_new SELECT * FROM source
RENAME TABLE source TO source_old,
             source_new TO source
ATTACH TABLE source_old
```

Steps:
1. **DETACH**: unmounts the table metadata from the database (data remains on disk)
2. **CREATE new**: creates a table with the new definition
3. **INSERT INTO ... SELECT**: copies all data from old to new (blocking if `source` is still receiving writes)
4. **RENAME**: atomically swaps source and source_old under an exclusive lock
5. **ATTACH**: remounts the old table as a backup under its new name

After the pipeline, `source` has the new definition and `source_old` holds the original data. Verification steps:

```sql
SELECT count(*) FROM source
SELECT engine, create_table_query FROM system.tables WHERE name = 'source'
```

If something went wrong, swap back:

```sql
RENAME TABLE source TO source_broken,
             source_old TO source
```

## Rollback of a recreate

The rollback is the reverse pipeline:

```
DETACH TABLE source_old
CREATE TABLE source (...original definition...)
INSERT INTO source SELECT * FROM source_old
RENAME TABLE source TO source_old2,
             source_old TO source
ATTACH TABLE source_old2
```

This restores the original table and keeps the failed new table as `source_old2`.

## When to use `--force`

- **Staging/testing**: always use `--force` to verify the pipeline works
- **Production small tables** (< 40 GB): `--force` with a maintenance window
- **Production large tables** (> 40 GB): avoid `--force`. Instead, manually plan a zero-downtime migration using `clickhouse-copier` or double-write during backfill

## Additional model examples

### Model that triggers CRITICAL classification

```python
# Current table for reference:
class OldEvents(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_date: Mapped[date] = mapped_column()
    class Meta(CHTableMeta):
        ch = ch_table(engine=merge_tree(), order_by=["event_date", "id"])

# Change: remove a column from ORDER BY (non-extension)
class UpdatedEvents(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_date: Mapped[date] = mapped_column()
    class Meta(CHTableMeta):
        ch = ch_table(engine=merge_tree(), order_by=["event_date"])  # 'id' removed
```

Plan output:

```
CRITICAL: Changing ORDER BY from (event_date, id) to (event_date) requires --force
Apply with: dbwarden migrate --force -d analytics
```

### Safe change that passes without --force

```python
# Add a column and extend ORDER BY
class SafeEvents(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_date: Mapped[date] = mapped_column()
    status: Mapped[str] = mapped_column()
    class Meta(CHTableMeta):
        ch = ch_table(engine=merge_tree(), order_by=["event_date", "id", "status"])
```

Plan output:

```
ALTER TABLE events ADD COLUMN status String   (INFO)
ALTER TABLE events MODIFY ORDER BY (event_date, id, status)   (INFO)
```

### Recreate pipeline example with verification

```bash
# 1. Preview the recreate
$ dbwarden make-migrations --plan --force -d analytics

# 2. Apply
$ dbwarden migrate --force -d analytics

# 3. Verify the new table
$ clickhouse-client -q "SELECT count(*) FROM events"
$ clickhouse-client -q "SELECT engine, create_table_query FROM system.tables WHERE name = 'events'"

# 4. If rollback needed:
$ clickhouse-client -q "RENAME TABLE events TO events_broken, events_old TO events"
```

## ClickHouse-native safety features

dbwarden also respects ClickHouse's server-side `allow_ddl` and `readonly` settings. If the server refuses a DDL statement, dbwarden logs the error and continues with the remaining plan items.
