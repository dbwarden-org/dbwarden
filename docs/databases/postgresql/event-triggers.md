# Event Triggers

**Requires the `dbwarden-pgsql-extensions` plugin:** `dbwarden plugin add dbwarden-pgsql-extensions`. The `event_trigger` object handler ships in that plugin, not in core.

**Handler**: `EventTriggerHandler` (PREAMBLE phase, config-driven)

Event triggers fire on database-level DDL events. They are scoped to the entire database cluster (not per-schema).

```python
pg_event_triggers=[
    {
        "name": "trg_ddl_audit",
        "event": "ddl_command_start",
        "function": "audit_ddl",
        "tags": ["CREATE TABLE", "ALTER TABLE"],
    },
]
```

## Events

| Event | Fires On |
|-------|----------|
| `ddl_command_start` | Before any DDL statement |
| `ddl_command_end` | After any DDL statement |
| `sql_drop` | When objects are dropped |
| `table_rewrite` | When `ALTER TABLE` rewrites a table |

## DDL Command Tags

Available tags for `WHEN TAG IN` filtering (selected common tags):

| Tag Category | Tags |
|--------------|------|
| DDL | `CREATE TABLE`, `ALTER TABLE`, `DROP TABLE`, `CREATE INDEX`, `ALTER INDEX`, `DROP INDEX` |
| Schema | `CREATE SCHEMA`, `ALTER SCHEMA`, `DROP SCHEMA` |
| Type | `CREATE TYPE`, `ALTER TYPE`, `DROP TYPE` |
| Function | `CREATE FUNCTION`, `ALTER FUNCTION`, `DROP FUNCTION` |
| Trigger | `CREATE TRIGGER`, `ALTER TRIGGER`, `DROP TRIGGER` |
| View | `CREATE VIEW`, `ALTER VIEW`, `DROP VIEW` |
| Sequence | `CREATE SEQUENCE`, `ALTER SEQUENCE`, `DROP SEQUENCE` |
| Extension | `CREATE EXTENSION`, `ALTER EXTENSION`, `DROP EXTENSION` |

The full list of supported tags is available in the PostgreSQL documentation under "Server Event Trigger Command Tags".

## Function Context Variables

Event trigger functions access DDL context through special session variables:

| Variable | Type | Description |
|----------|------|-------------|
| `TG_EVENT` | `text` | Event name: `ddl_command_start`, `ddl_command_end`, `sql_drop`, `table_rewrite` |
| `TG_TAG` | `text` | Command tag: `CREATE TABLE`, `ALTER TABLE`, etc. |
| `TG_TABLE_SCHEMA` | `text` | Schema of the target object (when applicable) |
| `TG_TABLE_NAME` | `text` | Name of the target object (when applicable) |

Example function using context variables:

```sql
CREATE FUNCTION audit_ddl()
RETURNS event_trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO ddl_audit_log (event, tag, schema, object, occurred_at)
    VALUES (TG_EVENT, TG_TAG, TG_TABLE_SCHEMA, TG_TABLE_NAME, NOW());
END;
$$;
```

## Function Signature Requirements

Event trigger functions must:
- Take **no arguments**
- Return type `event_trigger`
- Be created before the event trigger that references them

## Lifecycle

| Operation | DDL |
|-----------|-----|
| Create | `CREATE EVENT TRIGGER name ON event WHEN TAG IN ('tag1', 'tag2') EXECUTE FUNCTION func();` |
| Alter | `ALTER EVENT TRIGGER name DISABLE;` / `ALTER EVENT TRIGGER name ENABLE;` / `ALTER EVENT TRIGGER name RENAME TO new_name;` |
| Drop | `DROP EVENT TRIGGER IF EXISTS name;` |

## Enabled State

| Value | Meaning |
|-------|---------|
| `O` | Enabled (default) |
| `D` | Disabled |
| `R` | Enabled in replica mode |
| `A` | Always enabled |

## Notes

- Event triggers require a superuser to create
- The backing function must be created first (see [Functions & Triggers](functions-and-triggers.md))
- `DROP EVENT TRIGGER` does not auto-drop the backing function
- Tags filter which DDL commands fire the trigger; absent tags means all DDL commands
- If an event trigger function raises an exception, the DDL command is aborted and rolled back
- Use `sql_drop` with care: objects have already been removed from catalogs, so `TG_TABLE_SCHEMA` and `TG_TABLE_NAME` may be NULL for dropped objects; use `pg_event_trigger_dropped_objects()` to get the list
