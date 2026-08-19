# Schemas

**Handler**: `SchemaHandler` (PREAMBLE phase)

## Config-Level Schema

Set `pg_schema` in `database_config(...)` to set the connection's `search_path`:

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:pass@localhost:5432/main",
    pg_schema="app",
)
```

All unqualified table references use this schema. The `_dbwarden_seeds` tracking table is created in the schema specified by `search_path`.

`generate-models` also uses this schema: it sets `search_path` before introspection and lists tables from the configured schema. Snapshot queries against `pg_catalog` use quoted schema/table identifiers, so mixed-case or quoted table names are resolved correctly.

## Model-Level Schema

Set `pg_schema` on `PGTableMeta` or `PGViewMeta`:

```python
class Meta(PGTableMeta):
    pg_schema = "app"
```

When a model has `pg_schema`, all DDL references the fully qualified name (`app.users`). This takes precedence over the config-level `search_path`.

## Lifecycle

| Operation | DDL |
|-----------|-----|
| Create | `CREATE SCHEMA IF NOT EXISTS name;` |
| Drop | `DROP SCHEMA IF EXISTS name CASCADE;` |

## Schema Ownership

When a schema is created, it is owned by the role that created it. Set a different owner:

```sql
ALTER SCHEMA app OWNER TO app_admin;
```

## Schema Privileges

Schemas require privileges for access:

| Privilege | Effect |
|-----------|--------|
| `USAGE` | Allows access to objects in the schema |
| `CREATE` | Allows creating new objects in the schema |

```sql
GRANT USAGE ON SCHEMA app TO app_user;
GRANT CREATE ON SCHEMA app TO app_admin;
```

Without `USAGE` on a schema, a user cannot see or access any objects within it, even if they have table-level privileges.

## Search Path Resolution

PostgreSQL resolves unqualified names by searching schemas in `search_path` order:

```
current_schema (first match wins) -> pg_catalog -> public
```

Use the `current_schema` function to check the effective search path:

```sql
SELECT current_schema; -- Returns the first schema in the path
SHOW search_path;      -- Returns the full search path string
```

The config-level `pg_schema` becomes the first entry in `search_path`, which means it takes precedence for all unqualified references.

## pg_catalog vs public

- `pg_catalog` is always searched unless explicitly excluded
- `public` schema is accessible by default to all roles
- Custom schemas require explicit `USAGE` grants

## Temporary Schema

PostgreSQL creates a `pg_temp_*` schema for temporary tables. Temporary tables take precedence over permanent tables when their schema is first in `search_path`. You can reference `pg_temp.schema_name` explicitly.

## Extensions

Extensions are created during the PREAMBLE phase via `pg_extensions`, which requires the `dbwarden-pgsql-extensions` plugin (`dbwarden plugin add dbwarden-pgsql-extensions`). Without it, the key raises `DBWardenConfigError` at config load.

```python
pg_extensions=["uuid-ossp", "pgcrypto"]
```

Generated DDL: `CREATE EXTENSION IF NOT EXISTS "uuid-ossp";`

## Code Seeds and Schema

Code seeds automatically qualify the table name with the model's `pg_schema`. If `User` has `pg_schema = "app"`, the seed INSERT becomes `INSERT INTO app.users (...) VALUES (...)`.
