# Grants & Roles

**Requires the `dbwarden-pgsql-rbac` plugin:** `dbwarden plugin add dbwarden-pgsql-rbac`. The plugin owns the `pg_roles` and `pg_default_privileges` config keys along with the handlers described below, so declaring them without it installed raises `DBWardenConfigError` at config load.

Grants are handled by `GrantsHandler` (DIFF phase). Roles are handled by `RoleHandler` (PREAMBLE phase). Default privileges are handled by `DefaultPrivilegesHandler` (PREAMBLE phase).

## Table Grants

Grants are model-derived, declared per-table on the model.

### Grant Types

| Operation | DDL |
|-----------|-----|
| Grant | `GRANT privileges ON TABLE table TO role;` |
| Revoke | `REVOKE privileges ON TABLE table FROM role;` |

Supported privileges: `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `REFERENCES`, `TRIGGER`, `ALL`.

### Column-Level Privileges

Grant access to specific columns only:

```sql
GRANT SELECT (email, name) ON TABLE users TO read_only_role;
GRANT UPDATE (email) ON TABLE users TO app_user;
```

### GRANT OPTION

Allow the grantee to grant the same privilege to others:

```sql
GRANT SELECT ON TABLE users TO admin_user WITH GRANT OPTION;
```

### All Tables in Schema

Bulk grant using `ALL TABLES IN SCHEMA`:

```sql
GRANT SELECT ON ALL TABLES IN SCHEMA public TO read_only_role;
```

## Schema Grants

Schema grants use the same `GrantsHandler` with `object_type="schema"`:

| Operation | DDL |
|-----------|-----|
| Grant | `GRANT USAGE ON SCHEMA schema TO role;` |
| Revoke | `REVOKE USAGE ON SCHEMA schema FROM role;` |

Additional schema privileges:

| Privilege | Description |
|-----------|-------------|
| `USAGE` | Allows access to objects in the schema |
| `CREATE` | Allows creating objects in the schema |
| `ALL` | All schema privileges |

## Database-Level Grants

| Operation | DDL |
|-----------|-----|
| Grant | `GRANT privilege ON DATABASE db TO role;` |
| Revoke | `REVOKE privilege ON DATABASE db FROM role;` |

Supported database privileges: `CREATE`, `CONNECT`, `TEMPORARY` (or `TEMP`), `ALL`.

## Type-Level Grants

```sql
GRANT USAGE ON TYPE my_enum TO app_user;
```

## Roles

**Handler**: `RoleHandler` (PREAMBLE phase, config-driven)

```python
pg_roles=[
    {"name": "app_user", "login": True, "password": "encrypted"},
    {"name": "readonly", "login": True, "connection_limit": 5},
]
```

### Lifecycle

| Operation | DDL |
|-----------|-----|
| Create | `CREATE ROLE name WITH options` |
| Alter | `ALTER ROLE name WITH options` |
| Drop | `DROP ROLE IF EXISTS name` |

Roles are filtered to non-bootstrap roles (excludes `pg_*` roles and default cluster roles).

### ADMIN OPTION

A role with `ADMIN OPTION` can grant its role membership to others:

```python
{"name": "app_admin", "login": True, "membership": ["app_user"], "admin_option": True}
```

### Role Membership and INHERIT

Role membership grants privileges of the parent role. With `INHERIT` (default), member roles automatically inherit privileges. Without `INHERIT`, use `SET ROLE parent_role` to activate them.

```sql
-- app_user inherits privileges of base_user
CREATE ROLE app_user INHERIT IN ROLE base_user;
```

### SET ROLE / SET SESSION AUTHORIZATION

```sql
SET ROLE app_user;              -- Switch to role within session
SET SESSION AUTHORIZATION app_user;  -- Switch session user
```

## Default Privileges

**Handler**: `DefaultPrivilegesHandler` (PREAMBLE phase, config-driven)

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

### Lifecycle

| Operation | DDL |
|-----------|-----|
| Grant | `ALTER DEFAULT PRIVILEGES FOR ROLE role IN SCHEMA schema GRANT privileges ON kind TO role;` |
| Revoke | `ALTER DEFAULT PRIVILEGES FOR ROLE role IN SCHEMA schema REVOKE privileges ON kind FROM role;` |

### Object Kinds

| Kind | Objects Covered |
|------|----------------|
| `TABLES` | Tables, views, materialized views |
| `SEQUENCES` | Sequences |
| `FUNCTIONS` | Functions, procedures |
| `TYPES` | Types, domains |
| `SCHEMAS` | Schemas |

### REVOKE Behaviour

`REVOKE` supports `CASCADE` and `RESTRICT`:

```sql
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM read_only_role CASCADE;
```

`CASCADE` revokes the privilege from all users who received it through the target role. `RESTRICT` (default) fails if dependent privileges exist.

## BYPASSRLS and RLS Interaction

The `BYPASSRLS` role attribute lets a role bypass Row-Level Security entirely:

```python
{"name": "admin_user", "login": True, "bypassrls": True}
```

Roles without `BYPASSRLS` are subject to all RLS policies on accessed tables. See [RLS & Policies](rls-and-policies.md) for details.
