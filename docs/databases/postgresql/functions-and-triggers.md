# Functions & Triggers

**Requires the `dbwarden-pgsql-extensions` plugin:** `dbwarden plugin add dbwarden-pgsql-extensions`. The plugin owns the `pg_functions` and `pg_triggers` config keys and their object handlers, so declaring them without it installed raises `DBWardenConfigError` at config load.

Functions and triggers are **config-driven** objects processed during the PREAMBLE phase (before table diffing). They are declared in `database_config(...)`.

## Functions

Declared via `pg_functions`. Each function definition includes the body, language, and options.

```python
pg_functions=[
    {
        "name": "update_timestamp",
        "language": "plpgsql",
        "body": """
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
        """,
        "returns": "trigger",
    },
    {
        "name": "count_users",
        "language": "sql",
        "body": "SELECT count(*) FROM users",
        "returns": "bigint",
        "volatility": "STABLE",
    },
]
```

### Lifecycle

| Operation | DDL |
|-----------|-----|
| Create | `CREATE FUNCTION name (...args...) RETURNS type AS $$ body $$ LANGUAGE lang;` |
| Drop | `DROP FUNCTION IF EXISTS name (...args...) CASCADE;` |

Changes are detected as drop-then-create. There is no `CREATE OR REPLACE` for changed bodies.

### Options

| Config Key | SQL |
|------------|-----|
| `volatility` | `VOLATILE` / `STABLE` / `IMMUTABLE` |
| `security_definer` | `SECURITY DEFINER` |
| `leakproof` | `LEAKPROOF` |
| `parallel` | `PARALLEL UNSAFE` / `RESTRICTED` / `SAFE` |
| `cost` | `COST n` |
| `rows` | `ROWS n` |

Volatility semantics:

| Volatility | Behaviour | Optimizer Assumptions |
|------------|-----------|-----------------------|
| `VOLATILE` | Can return different results on each call (default) | No optimizations |
| `STABLE` | Same results within same statement | Can be used in index scan conditions |
| `IMMUTABLE` | Same results for same arguments always | Can be pre-evaluated, used in expression indexes, partition pruning |

### Return Types

| Returns | Description |
|---------|-------------|
| `trigger` | Trigger function (returns `TRIGGER`) |
| `void` | No return value |
| `table (...)` | Set-returning function (use `rows` for estimate) |
| `setof type` | Set-returning function (alternative syntax) |
| Any PG type | Scalar return |

### Arguments

```python
{
    "name": "add_user",
    "args": [
        {"name": "p_name", "type": "text"},
        {"name": "p_email", "type": "text"},
    ],
    "returns": "int",
    "language": "sql",
    "body": "INSERT INTO users (name, email) VALUES (p_name, p_email) RETURNING id",
}
```

### Parameter Modes

| Mode | Description |
|------|-------------|
| `IN` | Default: input parameter |
| `OUT` | Output parameter, acts as return value |
| `INOUT` | Input-output parameter (both accepts and returns) |
| `VARIADIC` | Variable number of arguments (last parameter only) |

```python
{
    "name": "get_user_stats",
    "args": [
        {"name": "p_user_id", "type": "int", "mode": "IN"},
        {"name": "out_count", "type": "bigint", "mode": "OUT"},
    ],
    "returns": "bigint",
    "language": "sql",
    "body": "SELECT count(*) FROM orders WHERE user_id = p_user_id",
}
```

### Function Overloading

PostgreSQL supports multiple functions with the same name but different argument types. dbwarden tracks the full argument signature. Two functions with the same name but different `args` are treated as distinct objects.

### WINDOW Functions

```python
{
    "name": "rank_per_category",
    "returns": "int",
    "language": "sql",
    "body": "SELECT rank() OVER (PARTITION BY category ORDER BY score DESC)",
    "args": [{"name": "category", "type": "text"}],
}
```

Set `RETURNS TABLE (...) ` or `RETURNS SETOF` with `rows` estimate.

## Procedures

PostgreSQL 11+ supports `CREATE PROCEDURE`, distinct from functions in that procedures can use transaction control (`COMMIT` / `ROLLBACK`).

```python
pg_functions=[
    {
        "name": "transfer_funds",
        "language": "plpgsql",
        "body": """
            BEGIN
                UPDATE accounts SET balance = balance - amount WHERE id = from_id;
                UPDATE accounts SET balance = balance + amount WHERE id = to_id;
                COMMIT;
            END;
        """,
        "returns": "void",
        "kind": "procedure",
    },
]
```

Key differences from functions:

| Aspect | Function | Procedure |
|--------|----------|-----------|
| Transaction control | No | Yes (`COMMIT`/`ROLLBACK`) |
| Called via | `SELECT func()` | `CALL proc()` |
| Return value | Required (can be void) | No return value |
| `kind` field | Omit or `"function"` | `"procedure"` |

## Triggers

Declared via `pg_triggers`. Each trigger binds a function to a table event.

```python
pg_triggers=[
    {
        "name": "trg_users_updated_at",
        "table": "users",
        "function": "update_timestamp",
        "timing": "BEFORE",
        "events": ["UPDATE"],
        "for_each": "ROW",
    },
]
```

### Lifecycle

| Operation | DDL |
|-----------|-----|
| Create | `CREATE TRIGGER name timing event ON table FOR EACH ROW EXECUTE FUNCTION func();` |
| Alter | `ALTER TRIGGER name ON table RENAME TO new_name;` |
| Drop | `DROP TRIGGER IF EXISTS name ON table;` |

### Events

One or more of: `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`.

### Timing

| Timing | Description |
|--------|-------------|
| `BEFORE` | Fires before the event |
| `AFTER` | Fires after the event |
| `INSTEAD OF` | Replaces the event (views only) |

### UPDATE OF Columns

Fire the trigger only when specific columns are updated:

```python
pg_triggers=[{
    "name": "trg_user_email",
    "table": "users",
    "function": "send_email_verification",
    "timing": "AFTER",
    "events": ["UPDATE OF email"],
    "for_each": "ROW",
}]
```

### Condition

Use `condition` to add a `WHEN` clause:

```python
pg_triggers=[{
    "name": "trg_prevent_delete",
    "table": "users",
    "function": "prevent_delete",
    "timing": "BEFORE",
    "events": ["DELETE"],
    "for_each": "ROW",
    "condition": "OLD.is_protected",
}]
```

### Trigger Function Context

Trigger functions access row data through special variables:

| Variable | Type | Description |
|----------|------|-------------|
| `NEW` | `RECORD` | New row for INSERT/UPDATE (NULL for DELETE) |
| `OLD` | `RECORD` | Old row for UPDATE/DELETE (NULL for INSERT) |
| `TG_OP` | `text` | Operation: `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE` |
| `TG_TABLE_NAME` | `text` | Table that fired the trigger |
| `TG_TABLE_SCHEMA` | `text` | Schema of the table |
| `TG_NAME` | `text` | Trigger name |
| `TG_WHEN` | `text` | Timing: `BEFORE`, `AFTER`, or `INSTEAD OF` |
| `TG_LEVEL` | `text` | `ROW` or `STATEMENT` |
| `TG_NARGS` | `int` | Number of arguments passed to the trigger |
| `TG_ARGV` | `text[]` | Arguments passed to the trigger |

### Constraint Triggers

Constraint triggers are deferred triggers that fire at transaction end. Declared separately from regular triggers:

```python
pg_triggers=[{
    "name": "trg_check_balance",
    "table": "accounts",
    "function": "verify_balance",
    "timing": "AFTER",
    "events": ["UPDATE"],
    "for_each": "ROW",
    "constraint": True,
    "deferrable": True,
    "initially": "DEFERRED",
}]
```

Generated DDL:
```sql
CREATE CONSTRAINT TRIGGER trg_check_balance
AFTER UPDATE ON accounts
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION verify_balance();
```

### Trigger Arguments

```python
pg_triggers=[{
    "name": "trg_log_changes",
    "table": "users",
    "function": "log_changes",
    "timing": "AFTER",
    "events": ["UPDATE"],
    "for_each": "ROW",
    "args": ["user_audit", "ignore_columns:password_hash"],
}]
```

Arguments are passed as `TG_ARGV[0]`, `TG_ARGV[1]`, etc. in the trigger function.
