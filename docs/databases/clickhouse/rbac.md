# RBAC (roles, users, policies, quotas, profiles, grants)

**Requires the `dbwarden-ch-rbac` plugin:** `dbwarden plugin add dbwarden-ch-rbac`. The plugin owns both the `ch_*` RBAC config keys and the object handlers that emit their DDL. Core ships the spec dataclasses only, so declaring these keys without the plugin installed raises `DBWardenConfigError` when your `dbwarden.py` loads.

All RBAC objects are declared in the config layer. The examples use the
supported `database_config()` function form for plugin-owned settings; new
projects may declare the same plugin configuration on a `DbwardenDatabase`
subclass.

## Config keys

```python
from dbwarden import database_config
from dbwarden.databases.clickhouse import (
    ChRoleSpec, ChUserSpec, ChRowPolicySpec,
    ChQuotaSpec, ChSettingsProfileSpec, ChGrantSpec,
)

database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="clickhouse://...",
    ch_roles=[...],
    ch_users=[...],
    ch_row_policies=[...],
    ch_quotas=[...],
    ch_settings_profiles=[...],
    ch_grants=[...],
)
```

## Roles

```python
ch_roles=[
    ChRoleSpec(name="analyst"),
    ChRoleSpec(name="admin"),
]
```

Generated DDL:

```sql
CREATE ROLE IF NOT EXISTS analyst;
CREATE ROLE IF NOT EXISTS admin;
```

No diff beyond name: roles are identifiers.

## Users

```python
ch_users=[
    ChUserSpec(
        name="alice",
        # Authentication is declare-only: written at creation, never diffed.
        auth="sha256_password BY 'secret'",
        default_roles=("analyst",),
    ),
]
```

Generated DDL:

```sql
CREATE USER IF NOT EXISTS alice
IDENTIFIED WITH sha256_password BY 'secret'
HOST ANY
DEFAULT ROLE analyst;
```

## Row policies

```python
ch_row_policies=[
    ChRowPolicySpec(
        name="analyst_filter",
        table="events",
        using="event_date >= '2024-01-01'",
        to_roles=("analyst",),
    ),
]
```

Generated DDL:

```sql
CREATE ROW POLICY IF NOT EXISTS analyst_filter
ON events
AS PERMISSIVE
FOR SELECT USING event_date >= '2024-01-01'
TO analyst;
```

## Quotas

```python
ch_quotas=[
    ChQuotaSpec(
        name="monthly_reads",
        interval="1 MONTH",
        limits={"queries": 1000000, "errors": 0, "result rows": 0},
        to_roles=("analyst",),
    ),
]
```

Generated DDL:

```sql
CREATE QUOTA IF NOT EXISTS monthly_reads
FOR INTERVAL 1 MONTH
MAX QUERIES 1000000, ERRORS 0, RESULT ROWS 0
TO analyst;
```

## Settings profiles

```python
ch_settings_profiles=[
    ChSettingsProfileSpec(
        name="strict",
        settings={"max_memory_usage": "10000000000"},
    ),
]
```

Generated DDL:

```sql
CREATE SETTINGS PROFILE IF NOT EXISTS strict
SETTINGS max_memory_usage = 10000000000;
```

## Grants

```python
ch_grants=[
    ChGrantSpec(
        privileges=["SELECT", "INSERT"],
        on="analytics.events",
        to="analyst",
    ),
]
```

Generated DDL:

```sql
GRANT SELECT, INSERT ON analytics.events TO analyst;
```

## Additional model examples

### Complete RBAC config with multiple objects

```python
database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="clickhouse://localhost:9000",
    ch_named_collections=[
        named_collection("ldap_corp", ldap_server="ldap.corp.example.com"),
    ],
    ch_roles=[
        ChRoleSpec("readonly"),
        ChRoleSpec("analyst"),
        ChRoleSpec("admin"),
    ],
    ch_settings_profiles=[
        ChSettingsProfileSpec(
            name="strict_read",
            settings={
                "max_memory_usage": "5000000000",
                "max_result_rows": "10000",
            },
        ),
    ],
    ch_users=[
        ChUserSpec(
            name="alice",
            auth="ldap BY 'ldap.corp.example.com'",
            default_roles=("analyst",),
            settings_profile="strict_read",
        ),
        ChUserSpec(
            name="bob",
            auth="sha256_password BY 'changeme'",  # declare-only: not diffed after creation
            default_roles=("readonly",),
        ),
    ],
    ch_row_policies=[
        ChRowPolicySpec(
            name="analyst_filter",
            table="analytics.events",
            using="event_date >= '2024-01-01'",
        ),
    ],
    ch_quotas=[
        ChQuotaSpec(
            name="monthly_cap",
            interval="1 MONTH",
            limits={"queries": 100000, "errors": 0, "result rows": 0},
        ),
    ],
    ch_grants=[
        ChGrantSpec(privileges=["SELECT"], on="analytics.*", to="readonly"),
        ChGrantSpec(privileges=["SELECT", "INSERT"], on="analytics.*", to="analyst"),
        ChGrantSpec(privileges=["ALL"], on="analytics.*", to="admin"),
    ],
)
```

### Dict config (raw path)

```python
database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="clickhouse://localhost:9000",
    ch_roles=[{"name": "analyst"}, {"name": "engineer"}],
    ch_users=[{
        "name": "carol",
        "auth": "sha256_password BY 's3cret'",
        "default_roles": ("engineer",),
    }],
)
```

## `storage != 'users.xml'` filter

dbwarden refuses to manage roles, users, or any RBAC object stored in `users.xml`:

```
ERROR: Cannot manage RBAC objects stored in users.xml.
Set storage = 'replicated' or use ClickHouse-native RBAC.
```

This is checked at config load time. If the server reports that RBAC storage is `users.xml`, all RBAC operations are skipped with a clear error.

## Drop gating

`DROP USER`, `DROP ROLE`, etc. are gated by the `dbwarden-ch-rbac` plugin. The plugin must be installed and configured to allow RBAC drops.

Without the plugin or proper configuration, RBAC drop statements are skipped:

```
INFO: RBAC drop skipped: plugin not configured for drops
```

This prevents accidental deactivation of users during migration runs.

## What changes are allowed

| Change | Safety |
|--------|--------|
| Add RBAC object | INFO |
| Drop RBAC object | WARN (gated by plugin configuration) |
| Modify user settings | INFO |
| Modify grant set | INFO |
| Change row policy expression | WARN |
| Change quota interval | INFO |
| Change settings profile | INFO |
| Named collection swap | INFO |

## Rollback behavior

Every RBAC CREATE has a DROP rollback and vice versa. Settings changes revert via `ALTER USER ... SETTINGS ...`.
# ClickHouse RBAC

The examples use the supported `database_config(...)` function form for
plugin-owned settings. New projects may declare the same plugin configuration
on a `DbwardenDatabase` subclass.
