# Config Keys

PostgreSQL features are configured through keys on your `DbwardenDatabase`
class or in the equivalent `database_config(...)` call. These keys live
alongside the connection URL and model paths.

**Most of these keys are contributed by plugins.** The plugin owns both the config key and the object handler that emits its DDL. Declaring a key whose plugin is not installed raises `DBWardenConfigError` when your `dbwarden.py` loads, naming the plugin to install. Keys that no plugin owns are rejected as unknown arguments, so typos fail immediately instead of being silently ignored.

| Config key | Required plugin |
|---|---|
| `pg_roles`, `pg_default_privileges` | `dbwarden-pgsql-rbac` |
| `pg_domains`, `pg_sequences`, `pg_composite_types` | `dbwarden-pgsql-types` |
| `pg_extensions`, `pg_functions`, `pg_triggers`, `pg_event_triggers`, `pg_extended_statistics` | `dbwarden-pgsql-extensions` |
| `pg_schema`, `pg_migration_lock_timeout` | None, core |

Install with `dbwarden plugin add <name>`.

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:pass@localhost:5432/mydb",
    # Config keys below
    pg_schema="app",
    pg_extensions=["uuid-ossp", "pgcrypto"],
    pg_roles=[...],
    pg_domains=[...],
    pg_sequences=[...],
    pg_functions=[...],
    pg_triggers=[...],
    pg_default_privileges=[...],
    pg_composite_types=[...],
    pg_extended_statistics=[...],
    pg_event_triggers=[...],
    pg_migration_lock_timeout=30,
)
```

## `pg_schema`

Default schema for unqualified table references. Sets the connection's `search_path`.

| Type | Default |
|------|---------|
| `str \| None` | `None` |

```python
pg_schema="app"
```

## `pg_extensions`

SQL extensions to create (equivalent to `CREATE EXTENSION IF NOT EXISTS`).

| Type | Default |
|------|---------|
| `list[str]` | `[]` |

```python
pg_extensions=["uuid-ossp", "pgcrypto", "postgis"]
```

Generated DDL: `CREATE EXTENSION IF NOT EXISTS "uuid-ossp";`

## `pg_roles`

Roles to create or alter. Each entry supports PostgreSQL role options.

| Type | Default |
|------|---------|
| `list[dict]` | `[]` |

```python
pg_roles=[
    {"name": "app_user", "login": True, "password": "encrypted"},
    {"name": "readonly", "login": True, "connection_limit": 5},
]
```

Generated DDL: `CREATE ROLE app_user WITH LOGIN PASSWORD 'encrypted';`

### Role Keys

| Key | Type | Description |
|-----|------|-------------|
| `name` | `str` | Role name |
| `login` | `bool` | `LOGIN` / `NOLOGIN` |
| `password` | `str` | `PASSWORD` (plain or `encrypted`) |
| `superuser` | `bool` | `SUPERUSER` / `NOSUPERUSER` |
| `createdb` | `bool` | `CREATEDB` / `NOCREATEDB` |
| `createrole` | `bool` | `CREATEROLE` / `NOCREATEROLE` |
| `inherit` | `bool` | `INHERIT` / `NOINHERIT` |
| `replication` | `bool` | `REPLICATION` / `NOREPLICATION` |
| `bypassrls` | `bool` | `BYPASSRLS` / `NOBYPASSRLS` |
| `connection_limit` | `int` | `CONNECTION LIMIT n` |
| `valid_until` | `str` | `VALID UNTIL 'timestamp'` |
| `in_role` | `str` | `IN ROLE parent_role` |
| `membership` | `list[str]` | `IN GROUP members` |

## `pg_domains`

Domain type declarations.

| Type | Default |
|------|---------|
| `list[dict]` | `[]` |

```python
pg_domains=[
    {
        "name": "us_postal_code",
        "type": "text",
        "not_null": True,
        "check": "VALUE ~ '^\d{5}(-\d{4})?$'",
    },
]
```

Generated DDL:
```sql
CREATE DOMAIN us_postal_code AS text NOT NULL CHECK (VALUE ~ '^\d{5}(-\d{4})?$');
```

### Domain Keys

| Key | Type | Description |
|-----|------|-------------|
| `name` | `str` | Domain name |
| `type` | `str` | Base type |
| `schema` | `str` | Schema (optional) |
| `default` | `str` | Default expression |
| `not_null` | `bool` | `NOT NULL` constraint |
| `check` | `str` | `CHECK` expression |

## `pg_sequences`

Sequence declarations.

| Type | Default |
|------|---------|
| `list[dict]` | `[]` |

```python
pg_sequences=[
    {
        "name": "order_number_seq",
        "start": 1000,
        "increment": 1,
        "minvalue": 1,
        "maxvalue": 999999,
        "cycle": True,
        "owned_by": None,
    },
]
```

Generated DDL:
```sql
CREATE SEQUENCE order_number_seq START WITH 1000 INCREMENT BY 1 MINVALUE 1 MAXVALUE 999999 CYCLE;
```

### Sequence Keys

| Key | Type | Description |
|-----|------|-------------|
| `name` | `str` | Sequence name |
| `schema` | `str` | Schema (optional) |
| `start` | `int` | `START WITH` |
| `increment` | `int` | `INCREMENT BY` |
| `minvalue` | `int` | `MINVALUE` |
| `maxvalue` | `int` | `MAXVALUE` |
| `cycle` | `bool` | `CYCLE` / `NO CYCLE` |
| `owned_by` | `str \| None` | `OWNED BY table.column` |

## `pg_functions`

Function declarations. Supports SQL, PL/pgSQL, and other languages.

| Type | Default |
|------|---------|
| `list[dict]` | `[]` |

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
        "args": [],
    },
]
```

### Function Keys

| Key | Type | Description |
|-----|------|-------------|
| `name` | `str` | Function name |
| `schema` | `str` | Schema (optional) |
| `language` | `str` | Language (`sql`, `plpgsql`, `c`, etc.) |
| `body` | `str` | Function body |
| `returns` | `str` | Return type |
| `args` | `list[dict]` | Arguments: `[{"name": "x", "type": "int"}]` |
| `volatility` | `str` | `VOLATILE`, `STABLE`, or `IMMUTABLE` |
| `security_definer` | `bool` | `SECURITY DEFINER` |
| `leakproof` | `bool` | `LEAKPROOF` |
| `parallel` | `str` | `PARALLEL UNSAFE`, `RESTRICTED`, or `SAFE` |
| `cost` | `int` | `COST` |
| `rows` | `int` | `ROWS` (for `RETURNS SETOF`) |

## `pg_triggers`

Trigger declarations. Each trigger references a table and an existing function.

| Type | Default |
|------|---------|
| `list[dict]` | `[]` |

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

Generated DDL:
```sql
CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_timestamp();
```

### Trigger Keys

| Key | Type | Description |
|-----|------|-------------|
| `name` | `str` | Trigger name |
| `table` | `str` | Table name |
| `schema` | `str` | Schema (optional) |
| `function` | `str` | Function to execute |
| `func_schema` | `str` | Function schema (optional) |
| `timing` | `str` | `BEFORE`, `AFTER`, or `INSTEAD OF` |
| `events` | `list[str]` | `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE` |
| `for_each` | `str` | `ROW` or `STATEMENT` |
| `condition` | `str` | `WHEN` clause (optional) |
| `args` | `list[str]` | Arguments passed to function |

## `pg_default_privileges`

Default privileges applied per schema, role, or object type.

| Type | Default |
|------|---------|
| `list[dict]` | `[]` |

```python
pg_default_privileges=[
    {
        "schema": "public",
        "role": "app_user",
        "kind": "TABLES",
        "privileges": "SELECT, INSERT, UPDATE, DELETE",
    },
]
```

Generated DDL:
```sql
ALTER DEFAULT PRIVILEGES FOR ROLE app_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;
```

### Default Privilege Keys

| Key | Type | Description |
|-----|------|-------------|
| `schema` | `str` | Schema name |
| `role` | `str` | Target role |
| `kind` | `str` | Object type: `TABLES`, `SEQUENCES`, `FUNCTIONS`, `TYPES`, `SCHEMAS` |
| `privileges` | `str` | Comma-separated privileges |

## `pg_composite_types`

Composite type declarations.

| Type | Default |
|------|---------|
| `list[dict]` | `[]` |

```python
pg_composite_types=[
    {
        "name": "address",
        "columns": [
            {"name": "street", "type": "text"},
            {"name": "city", "type": "text"},
            {"name": "zip", "type": "text"},
        ],
    },
]
```

Generated DDL:
```sql
CREATE TYPE address AS (street text, city text, zip text);
```

### Composite Type Keys

| Key | Type | Description |
|-----|------|-------------|
| `name` | `str` | Type name |
| `schema` | `str` | Schema (optional) |
| `columns` | `list[dict]` | List of `{"name": ..., "type": ...}` |

## `pg_extended_statistics`

Extended statistics objects for the query planner (PG 14+).

| Type | Default |
|------|---------|
| `list[dict]` | `[]` |

```python
pg_extended_statistics=[
    {
        "name": "stats_users_email_city",
        "table": "users",
        "kinds": ["d", "f"],
        "columns": "email, city",
    },
]
```

Generated DDL:
```sql
CREATE STATISTICS stats_users_email_city (ndistinct, dependencies) ON email, city FROM users;
```

### Extended Statistics Keys

| Key | Type | Description |
|-----|------|-------------|
| `name` | `str` | Statistics name |
| `table` | `str` | Table name |
| `schema` | `str` | Schema (optional) |
| `kinds` | `list[str]` | Kind codes: `d` (ndistinct), `f` (dependencies), `m` (MCV), `e` (expressions, PG 14+) |
| `columns` | `str` | Comma-separated column names |
| `expressions` | `list[str]` | Expression columns (PG 14+) |

## `pg_event_triggers`

Event triggers fired on DDL events at the database level.

| Type | Default |
|------|---------|
| `list[dict]` | `[]` |

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

Generated DDL:
```sql
CREATE EVENT TRIGGER trg_ddl_audit ON ddl_command_start WHEN TAG IN ('CREATE TABLE', 'ALTER TABLE') EXECUTE FUNCTION audit_ddl();
```

### Event Trigger Keys

| Key | Type | Description |
|-----|------|-------------|
| `name` | `str` | Trigger name |
| `event` | `str` | Event: `ddl_command_start`, `ddl_command_end`, `sql_drop`, `table_rewrite` |
| `function` | `str` | Function to execute |
| `func_schema` | `str` | Function schema (optional) |
| `tags` | `list[str]` | DDL command tags to filter (optional) |
| `enabled` | `str` | `O` (enabled), `D` (disabled), `R` (replica), `A` (always) |

## `pg_migration_lock_timeout`

Timeout (seconds) for `LOCK TABLE` statements during migration DDL to prevent indefinite blocking.

| Type | Default |
|------|---------|
| `int \| None` | `None` |

```python
pg_migration_lock_timeout=30
```

When set, emits `SET lock_timeout = '30s'` before each migration statement.
