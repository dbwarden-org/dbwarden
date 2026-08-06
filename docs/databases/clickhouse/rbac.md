# RBAC (roles, users, policies, quotas, profiles, grants)

**Requires the `dbwarden-ch-rbac` plugin:** `dbwarden plugin add dbwarden-ch-rbac`. The plugin owns both the `ch_*` RBAC config keys and the object handlers that emit their DDL. Core ships the spec dataclasses only, so declaring these keys without the plugin installed raises `DBWardenConfigError` when your `dbwarden.py` loads.

All RBAC objects are declared in the config layer via `database_config()`.

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
        # Authentication: declare-only via named collection
        named_collection="ldap_prod",
        # Or directly (but values are not stored/compared):
        # identified_by="sha256_password", password="secret"
        default_role="analyst",
        settings={"max_memory_usage": 10000000000},
    ),
]
```

Generated DDL:

```sql
CREATE USER IF NOT EXISTS alice
DEFAULT ROLE analyst
SETTINGS max_memory_usage = 10000000000;
```

## Row policies

```python
ch_row_policies=[
    ChRowPolicySpec(
        name="analyst_filter",
        table="events",
        as_restriction="event_date >= '2024-01-01'",
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
        interval={"month": [1000000, 0, 0]},
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
        settings={"max_memory_usage": 10000000000},
        constraints={"max_memory_usage": "READONLY"},
    ),
]
```

Generated DDL:

```sql
CREATE SETTINGS PROFILE IF NOT EXISTS strict
SETTINGS max_memory_usage = 10000000000 CONSTRAINED READONLY;
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
        named_collection("ldap_corp", keys={"ldap_server": "ldap.corp.example.com"}),
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
                "max_memory_usage": 5000000000,
                "max_result_rows": 10000,
            },
            constraints={
                "max_memory_usage": "READONLY",
                "max_result_rows": "READONLY",
            },
        ),
    ],
    ch_users=[
        ChUserSpec(
            name="alice",
            named_collection="ldap_corp",
            default_role="analyst",
            settings_profile="strict_read",
        ),
        ChUserSpec(
            name="bob",
            identified_by="sha256_password",
            password="changeme",  # declare-only: not diffed after creation
            default_role="readonly",
        ),
    ],
    ch_row_policies=[
        ChRowPolicySpec(
            name="analyst_filter",
            table="analytics.events",
            as_restriction="event_date >= '2024-01-01'",
        ),
    ],
    ch_quotas=[
        ChQuotaSpec(
            name="monthly_cap",
            interval={"month": [100000, 0, 0]},
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
        "identified_by": "sha256_password",
        "password": "s3cret",
        "default_role": "engineer",
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

`DROP USER`, `DROP ROLE`, etc. require `--clickhouse-allow-drop-rbac`:

```bash
dbwarden migrate -d analytics --clickhouse-allow-drop-rbac
```

Without it, RBAC drop statements are skipped:

```
INFO: RBAC drop skipped: use --clickhouse-allow-drop-rbac to enable
```

This prevents accidental deactivation of users during migration runs.

## What changes are allowed

| Change | Safety |
|--------|--------|
| Add RBAC object | INFO |
| Drop RBAC object | WARN (gated by `--clickhouse-allow-drop-rbac`) |
| Modify user settings | INFO |
| Modify grant set | INFO |
| Change row policy expression | WARN |
| Change quota interval | INFO |
| Change settings profile | INFO |
| Named collection swap | INFO |

## Rollback behavior

Every RBAC CREATE has a DROP rollback and vice versa. Settings changes revert via `ALTER USER ... SETTINGS ...`.
