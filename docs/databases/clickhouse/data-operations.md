# Data operations

Data operations (`data_op()`) are non-DDL statements that mutate data or trigger maintenance. They are not declared in the model: they are ad-hoc operations.

## Additional model examples

### Batch partition cleanup

```python
# Freeze, back up, then drop old partitions
from dbwarden.databases.clickhouse import data_op

# Freeze for backup
for m in ["2023-01", "2023-02", "2023-03"]:
    data_op(f"ALTER TABLE events FREEZE PARTITION '{m}'")

# After backup verified, drop
for m in ["2023-01", "2023-02", "2023-03"]:
    data_op(f"ALTER TABLE events DROP PARTITION '{m}'")
```

### Conditional mutation with setting override

```python
# Large mutation with timeout
data_op("""
    ALTER TABLE events
        UPDATE status = 'archived'
        WHERE event_date < '2020-01-01'
        SETTINGS mutations_sync = 2
""")
```

### OPTIMIZE with deduplicate

```python
# Force full merge and deduplication on all parts
data_op("OPTIMIZE TABLE events FINAL DEDUPLICATE")

# With column-specific deduplication
data_op("OPTIMIZE TABLE events FINAL DEDUPLICATE BY id, event_date")
```

### Multi-step migration with data ops

```python
def migrate_events():
    # 1. Create new table via migrate
    # 2. Backfill from old partition
    data_op("""
        ALTER TABLE events_v2
            REPLACE PARTITION '2024-01'
            FROM events_v1
    """)
    # 3. Drop old partition
    data_op("ALTER TABLE events_v1 DROP PARTITION '2024-01'")
    # 4. Verify
    data_op("OPTIMIZE TABLE events_v2 FINAL")
```

## Partition operations

```python
from dbwarden.databases.clickhouse import data_op

# Attach a detached partition
data_op("ALTER TABLE events ATTACH PARTITION '2024-01'")

# Replace one partition with another
data_op("ALTER TABLE events REPLACE PARTITION '2024-02' FROM staging_events")

# Drop a partition
data_op("ALTER TABLE events DROP PARTITION '2024-01'")

# Clear column in partition
data_op("ALTER TABLE events CLEAR COLUMN payload IN PARTITION '2024-01'")

# Freeze partition for backup
data_op("ALTER TABLE events FREEZE PARTITION '2024-01'")

# Unfreeze
data_op("ALTER TABLE events UNFREEZE PARTITION '2024-01'")
```

## Mutations

```python
# DELETE
data_op("ALTER TABLE events DELETE WHERE event_date < '2023-01-01'")

# UPDATE
data_op("ALTER TABLE events UPDATE payload = 'redacted' WHERE id = 123")
```

## OPTIMIZE

```python
# Merge parts
data_op("OPTIMIZE TABLE events FINAL")

# With partition
data_op("OPTIMIZE TABLE events PARTITION '2024-01' FINAL")

# Deduplicate
data_op("OPTIMIZE TABLE events FINAL DEDUPLICATE")
```

## POPULATE

```python
# Populate a materialized view
data_op("ALTER TABLE mv_name POPULATE")
```

This is a data-op rather than a DDL property because it is a write concern, not structural. See [Materialized views](materialized-views.md).

## Secret rotation

Named collection secrets are rotated through ClickHouse's secret store:

```python
# Refresh credentials from secret store
data_op("ALTER NAMED COLLECTION kafka_prod UPDATE sasl_password = SECRET 'new_secret_id'")
```

## Safety

| Operation | Safety | Notes |
|-----------|--------|-------|
| ATTACH PARTITION | INFO | Cheap metadata operation |
| REPLACE PARTITION | WARN | Overwrites target |
| DROP PARTITION | WARN | Data loss within a partition |
| CLEAR COLUMN | WARN | Data cleared for partition |
| DELETE mutation | WARN | Async, causes part rewrites |
| UPDATE mutation | WARN | Async, causes part rewrites |
| OPTIMIZE FINAL | INFO | Heavy IO |
| POPULATE | INFO | Inserts current data |
| Secret rotation | INFO | |

## Rollback behavior

Data operations are **not reversible** by dbwarden: they are ad-hoc mutations. Plan accordingly: test on staging, back up partitions before DROP or REPLACE.
