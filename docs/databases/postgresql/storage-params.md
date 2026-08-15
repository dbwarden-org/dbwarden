# Storage Parameters

**Table storage parameters require the `dbwarden-pgsql-extensions` plugin:** `dbwarden plugin add dbwarden-pgsql-extensions`. The `storage_params` object handler ships in that plugin. Index-level storage parameters are part of core index handling and need no plugin.

PostgreSQL supports table-level and index-level storage parameters that control physical storage behaviour, autovacuum tuning, and performance characteristics.

dbwarden tracks storage parameters through the `with_params` field on `PgIndexSpec` and table-level handlers.

## Table Storage Parameters

Set via `ALTER TABLE t SET (param = value, ...)` using the handler infrastructure.

### Autovacuum Parameters

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `autovacuum_enabled` | `bool` | Enables/disables autovacuum for this table | `True` |
| `autovacuum_vacuum_threshold` | `int` | Minimum number of dead tuples before vacuum | `50` |
| `autovacuum_vacuum_scale_factor` | `float` | Fraction of table dead tuples before vacuum | `0.2` |
| `autovacuum_vacuum_ins_threshold` | `int` | Minimum INSERT dead tuples before vacuum (PG 17+) | `1000` |
| `autovacuum_vacuum_ins_scale_factor` | `float` | Fraction of INSERT dead tuples before vacuum (PG 17+) | `0.2` |
| `autovacuum_analyze_threshold` | `int` | Minimum modified tuples before analyze | `50` |
| `autovacuum_analyze_scale_factor` | `float` | Fraction of modified tuples before analyze | `0.1` |
| `autovacuum_vacuum_cost_delay` | `int` | Millisecond delay between vacuum cost units | `2` |
| `autovacuum_vacuum_cost_limit` | `int` | Cost limit before vacuum pauses | `-1` (use global) |
| `autovacuum_freeze_min_age` | `int` | Minimum age before FREEZE | `50000000` |
| `autovacuum_freeze_max_age` | `int` | Maximum age before forced FREEZE | `200000000` |
| `autovacuum_freeze_table_age` | `int` | Age at which whole-table freeze is considered | `150000000` |
| `autovacuum_multixact_freeze_min_age` | `int` | Minimum multixact age before freeze | `5000000` |
| `autovacuum_multixact_freeze_max_age` | `int` | Maximum multixact age before forced freeze | `200000000` |
| `autovacuum_multixact_freeze_table_age` | `int` | Table-level multixact freeze age | `150000000` |

### Tuning Parameters

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `fillfactor` | `int` | Percentage of page space to fill (1-100) | `100` |
| `toast_tuple_target` | `int` | Minimum tuple size before TOAST (PG 11+) | `2048` |
| `parallel_workers` | `int` | Number of parallel workers for scans (PG 11+) | `0` (use global) |
| `vacuum_truncate` | `bool` | Allow vacuum to truncate empty pages (PG 14+) | `True` |
| `log_autovacuum_min_duration` | `int` | Log autovacuum actions exceeding this (ms) (PG 14+) | `-1` (disabled) |

### Table-Level Fillfactor

The `pg_fillfactor` attribute on `PGTableMeta` sets the fillfactor storage parameter:

```python
class Meta(PGTableMeta):
    pg_fillfactor = 80
```

```sql
ALTER TABLE users SET (fillfactor = 80);
```

Fillfactor applies per-table for heap storage and per-index for B-tree indexes. A lower fillfactor leaves more free space for future updates, reducing page splits at the cost of denser storage.

### Toast Tuple Target

Controls when inline values are moved to TOAST storage. Lower values move larger tuples to TOAST earlier:

```sql
ALTER TABLE users SET (toast_tuple_target = 1024);
```

## Index Storage Parameters

Set via the `with_params` field on `PgIndexSpec`:

```python
PgIndexSpec("ix_users_email", ["email"],
    with_params={"fillfactor": 70})
```

```sql
CREATE INDEX CONCURRENTLY ix_users_email ON users (email) WITH (fillfactor = 70);
```

### Access Method-Specific Parameters

| Access Method | Parameter | Type | Default | Description |
|---------------|-----------|------|---------|-------------|
| B-tree | `fillfactor` | `int` | `90` | Page fill percentage |
| GiST | `buffering` | `str \| bool` | `auto` | `on`, `off`, or `auto` |
| BRIN | `pages_per_range` | `int` | `128` | Pages per block range |
| BRIN | `autosummarize` | `bool` | `off` | Auto-summarize on insert |
| Hash | `fillfactor` | `int` | `100` | Page fill percentage |

Example: BRIN parameters:

```python
PgIndexSpec("ix_orders_created_at", ["created_at"],
    using="brin",
    with_params={"pages_per_range": 32, "autosummarize": True})
```

```sql
CREATE INDEX CONCURRENTLY ix_orders_created_at ON orders USING BRIN (created_at) WITH (pages_per_range = 32, autosummarize = on);
```

## Using `with_params`

The `with_params` field on `PgIndexSpec` accepts a dict of parameter name-value pairs:

| Field | Value Type | Description |
|-------|------------|-------------|
| Key | `str` | PostgreSQL storage parameter name (e.g. `fillfactor`, `autosummarize`) |
| Value | `str \| int \| bool` | Parameter value. Booleans render as `on`/`off` |

Parameters are rendered as `WITH (key1 = val1, key2 = val2, ...)` in the `CREATE INDEX` statement.

## Changing Storage Parameters

Storage parameter changes are detected during the DIFF phase. Parameter changes for tables and indexes produce `ALTER TABLE ... SET (...)` or `ALTER INDEX ... SET (...)` DDL. Safety classification varies:

| Change | Severity |
|--------|----------|
| fillfactor change | `INFO` |
| autovacuum setting change | `INFO` |
| BRIN parameter change | `INFO` |
| Index `with_params` change | `INFO` |

See [Migration Safety](migration-safety.md) for the full classification table.
