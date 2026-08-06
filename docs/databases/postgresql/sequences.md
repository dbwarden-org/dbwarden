# Sequences

**Requires the `dbwarden-pgsql-types` plugin:** `dbwarden plugin add dbwarden-pgsql-types`. The `sequence` object handler ships in that plugin, not in core.

**Handler**: `SequenceHandler` (PREAMBLE phase, config-driven)

```python
pg_sequences=[
    {
        "name": "order_number_seq",
        "start": 1000,
        "increment": 1,
        "minvalue": 1,
        "maxvalue": 999999,
        "cycle": True,
    },
]
```

## Lifecycle

| Operation | DDL |
|-----------|-----|
| Create | `CREATE SEQUENCE name START WITH 1000 INCREMENT BY 1 MINVALUE 1 MAXVALUE 999999 CYCLE;` |
| Alter | `ALTER SEQUENCE name INCREMENT BY 10 MAXVALUE 9999999;` |
| Drop | `DROP SEQUENCE IF EXISTS name;` |

### ALTER SEQUENCE

Modify sequence parameters after creation:

```sql
ALTER SEQUENCE order_number_seq INCREMENT BY 10;
ALTER SEQUENCE order_number_seq RESTART WITH 5000;
ALTER SEQUENCE order_number_seq MAXVALUE 9999999;
ALTER SEQUENCE order_number_seq NO CYCLE;
```

DBWarden detects changes to any sequence option and emits `ALTER SEQUENCE` instead of drop+create when feasible.

## Options

| Key | SQL |
|-----|-----|
| `start` | `START WITH n` |
| `increment` | `INCREMENT BY n` |
| `minvalue` | `MINVALUE n` |
| `maxvalue` | `MAXVALUE n` |
| `cycle` | `CYCLE` / `NO CYCLE` |
| `cache` | `CACHE n` |
| `owned_by` | `OWNED BY table.column` |

### CACHE Option

`CACHE n` pre-allocates sequence values in memory for better performance:

```python
{
    "name": "order_number_seq",
    "start": 1000,
    "increment": 1,
    "cache": 100,
}
```

```sql
CREATE SEQUENCE order_number_seq START WITH 1000 INCREMENT BY 1 CACHE 100;
```

Higher cache values improve multi-session throughput but increase gaps on crashes (cached values are lost).

## Ownership

When `owned_by` is set, dropping the owning table/column automatically drops the sequence. If `owned_by` is `None`, the sequence is standalone and persists after table drops.

Transfer sequence ownership:

```sql
ALTER SEQUENCE order_number_seq OWNED BY orders.order_number;
```

## Schema-Qualified Sequences

Reference a sequence in a specific schema:

```python
{
    "name": "app.order_number_seq",
    "start": 1000,
}
```

## Sequence Functions

| Function | Description |
|----------|-------------|
| `nextval('name')` | Advance and return next value |
| `currval('name')` | Return last value obtained in session |
| `lastval()` | Return last value from any sequence in session |
| `setval('name', n)` | Set current value |
| `setval('name', n, is_called)` | Set value with `is_called` flag |

## Sequence Privileges

| Privilege | Description |
|-----------|-------------|
| `USAGE` | Allows `nextval` and `currval` |
| `SELECT` | Allows `currval` only |
| `UPDATE` | Allows `setval` |
| `ALL` | All sequence privileges |

```sql
GRANT USAGE ON SEQUENCE order_number_seq TO app_user;
```

## Sequence vs Auto-increment

Auto-increment on PK columns (SERIAL/IDENTITY) automatically creates and manages sequences. Independent sequences declared via `pg_sequences` are useful for manual sequence management, custom numbering, or shared sequences across tables.

### Sequence Gaps

Sequence values are not intended to be gap-free. Gaps can occur from:

| Cause | Description |
|-------|-------------|
| `CACHE` loss | Cached values lost on crash |
| Rollback | `nextval` is not rolled back on transaction abort |
| `setval` | Manual value adjustment |
| Concurrent deletes | Rows deleted do not free sequence values |

## Migration Safety

| Change | Severity |
|--------|----------|
| Add sequence | `INFO` |
| Drop sequence | `WARNING` |
| Change sequence options (start, increment, etc.) | `WARNING` |

See [Migration Safety](migration-safety.md) for the full classification table.
