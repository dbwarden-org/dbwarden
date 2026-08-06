# RLS & Policies

**Requires the `dbwarden-pgsql-rbac` plugin:** `dbwarden plugin add dbwarden-pgsql-rbac`. The policies handler ships in that plugin, not in core.

**Handler**: `PoliciesHandler` (DIFF phase)

Row-Level Security and policies are model-derived, declared on `PGTableMeta`.

## Enabling RLS

```python
class Meta(PGTableMeta):
    pg_rls = True
```

Generated DDL:
```sql
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
```

### Default Deny Behaviour

When RLS is enabled but no policy applies to the current user, the default behaviour is to **deny all access**. Every row is invisible for read operations, and all write operations are blocked. At least one permissive policy must exist for the user to access data.

### FORCE / NO FORCE

```python
class Meta(PGTableMeta):
    pg_rls = True
    pg_rls_force = True
```

Generated DDL:
```sql
ALTER TABLE users FORCE ROW LEVEL SECURITY;
```

To disable force (without disabling RLS):

```python
class Meta(PGTableMeta):
    pg_rls = True
    pg_rls_force = False
```

```sql
ALTER TABLE users NO FORCE ROW LEVEL SECURITY;
```

The force flag only emits when it explicitly changes. If `pg_rls_force` is absent or `False` on both the snapshot and model sides, no `FORCE`/`NO FORCE` DDL is generated. This avoids churn on indexes that only toggle RLS without changing force.

`FORCE ROW LEVEL SECURITY` applies RLS to the table owner, who would normally bypass it. `NO FORCE` (default) exempts the owner.

## Policies

Policies are declared as a list on `PGTableMeta`:

```python
class Meta(PGTableMeta):
    pg_rls = True
    pg_policies = [
        {
            "name": "tenant_isolation",
            "using": "tenant_id = current_setting('app.tenant_id')::int",
            "roles": ["app_user"],
            "permissive": True,
        },
    ]
```

### Policy Keys

| Key | Description |
|-----|-------------|
| `name` | Policy name |
| `using` | `USING` expression (row visibility) |
| `with_check` | `WITH CHECK` expression (row modification) |
| `roles` | Roles the policy applies to (absent = all roles) |
| `permissive` | `PERMISSIVE` (default) or `RESTRICTIVE` |
| `command` | `ALL` (default), `SELECT`, `INSERT`, `UPDATE`, `DELETE` |

### Permissive vs Restrictive

Multiple policies interact differently based on their type:

| Policy Type | Combination Logic |
|-------------|-------------------|
| `PERMISSIVE` (default) | **OR**: access is granted if ANY permissive policy allows it |

| `RESTRICTIVE` | **AND**: access is denied if ANY restrictive policy blocks it |
Restrictive policies filter results after permissive policies. Use restrictive policies to implement mandatory access controls that override permissive policies:

```python
pg_policies = [
    # Permissive: tenant-level access
    {"name": "tenant_access", "using": "tenant_id = current_setting('app.tenant_id')::int",
     "permissive": True},
    # Restrictive: block access to sensitive rows regardless of tenant
    {"name": "block_sensitive", "using": "NOT is_sensitive",
     "permissive": False, "command": "SELECT"},
]
```

### Lifecycle

| Operation | DDL |
|-----------|-----|
| Add policy | `CREATE POLICY name ON table FOR command USING (expr)` |
| Alter policy | `ALTER POLICY name ON table USING (new_expr)` |
| Drop policy | `DROP POLICY IF EXISTS name ON table` |
| Enable RLS | `ALTER TABLE table ENABLE ROW LEVEL SECURITY` |
| Disable RLS | `ALTER TABLE table DISABLE ROW LEVEL SECURITY` |
| Force RLS | `ALTER TABLE table FORCE ROW LEVEL SECURITY` |
| Remove force | `ALTER TABLE table NO FORCE ROW LEVEL SECURITY` |

## BYPASSRLS Role Attribute

The `BYPASSRLS` role attribute lets a role bypass all RLS policies:

```python
pg_roles=[
    {"name": "admin_user", "login": True, "bypassrls": True},
]
```

This is equivalent to running without RLS for that role. See [Grants & Roles](grants-and-roles.md) for role configuration.

## RLS and COPY

`COPY TO` on a table with RLS respects policies; only rows visible through the user's policies are exported. `COPY FROM` on a table with RLS checks `WITH CHECK` policies for each inserted row.

## RLS and Unique Constraints

Unique constraints (including PKs) are **not** RLS-aware. A user may see a unique constraint violation caused by a row they cannot see. This is a PostgreSQL limitation: RLS filters query results but does not filter constraint enforcement.

## RLS and FK Constraints

Foreign key constraints are enforced server-side regardless of RLS. A user may not be able to `SELECT` a referenced row but can still insert a referencing row if the FK validates.

## Migration Safety

| Change | Severity |
|--------|----------|
| Enable RLS | `INFO` |
| Disable RLS | `WARNING` |
| Force RLS | `INFO` |
| Add policy | `INFO` |
| Drop policy | `INFO` |
| Change policy expression | `INFO` |

See [Migration Safety](migration-safety.md) for the full classification table.
