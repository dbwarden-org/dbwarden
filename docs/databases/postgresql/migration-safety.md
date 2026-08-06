# Migration Safety

DBWarden classifies migration changes using the `Safety` enum:

```python
from dbwarden.engine.safety import Safety

assert Safety.SAFE == "SAFE"
assert Safety.INFO == "INFO"
assert Safety.WARN == "WARN"
assert Safety.CRITICAL == "CRITICAL"
```

## Safety Classification by Object Type

### Tables & Columns

| Change Type | Severity | Flag Required |
|-------------|----------|---------------|
| Add column | `INFO` | None |
| Drop column | `WARNING` | `--force` |
| Change column type (safe) | `INFO` | None |
| Change column type (warn) | `WARNING` | `--force` |
| Change column type (critical) | `WARNING` | `--force` |
| Change column comment | `INFO` | None |
| Change nullable (SET/DROP NOT NULL) | `INFO` | None |
| Change default (SET/DROP DEFAULT) | `INFO` | None |
| Add autoincrement | `INFO` | None |
| Remove autoincrement | `WARNING` | `--force` |
| Change PG column meta (storage, compression, collation) | `WARNING` | `--force` |
| Rename column | `WARNING` | `--force` |

### Table Properties

| Change Type | Severity | Flag Required |
|-------------|----------|---------------|
| Change fillfactor | `INFO` | None |
| Change tablespace | `WARNING` | `--force` |
| Change inheritance | `WARNING` | `--force` |
| Change exclude constraints | `WARNING` | `--force` |
| Change table comment | `INFO` | None |
| Change object type (logged/unlogged) | `WARNING` | `--force` |
| Change ON COMMIT | `WARNING` | `--force` |

### Indexes

| Change Type | Severity | Flag Required |
|-------------|----------|---------------|
| Add index | `INFO` | None |
| Drop index | `WARNING` | `--force` |
| Change index expression / columns | `WARNING` | `--force` |
| Add/drop INCLUDE column | `INFO` | None |
| Change index storage parameters | `INFO` | None |
| Change index tablespace | `WARNING` | `--force` |

### Constraints

| Change Type | Severity | Flag Required |
|-------------|----------|---------------|
| Add FK | `INFO` | None |
| Drop FK | `WARNING` | `--force` |
| Change FK options | `WARNING` | `--force` |
| Add unique constraint | `INFO` | None |
| Drop unique constraint | `WARNING` | `--force` |
| Add check constraint | `INFO` | None |
| Drop check constraint | `WARNING` | `--force` |
| Add exclude constraint | `INFO` | None |
| Drop exclude constraint | `WARNING` | `--force` |
| Change constraint deferrability | `INFO` | None |

### Sequences

| Change Type | Severity | Flag Required |
|-------------|----------|---------------|
| Add sequence | `INFO` | None |
| Drop sequence | `WARNING` | `--force` |
| Change sequence options (start, increment, etc.) | `WARNING` | `--force` |

### Types

| Change Type | Severity | Flag Required |
|-------------|----------|---------------|
| Add enum value | `INFO` | None |
| Add enum type | `INFO` | None |
| Drop enum type | `WARNING` | `--force` |
| Add domain | `INFO` | None |
| Drop domain | `WARNING` | `--force` |
| Change domain definition | `WARNING` | `--force` |
| Add composite type | `INFO` | None |
| Drop composite type | `WARNING` | `--force` |
| Change composite type columns | `WARNING` | `--force` |

### Functions & Procedures

| Change Type | Severity | Flag Required |
|-------------|----------|---------------|
| Add function | `INFO` | None |
| Drop function | `WARNING` | `--force` |
| Change function body | `WARNING` | `--force` |
| Add procedure | `INFO` | None |
| Drop procedure | `WARNING` | `--force` |

### Triggers

| Change Type | Severity | Flag Required |
|-------------|----------|---------------|
| Add trigger | `INFO` | None |
| Drop trigger | `WARNING` | `--force` |
| Change trigger timing/events | `WARNING` | `--force` |
| Add constraint trigger | `INFO` | None |
| Drop constraint trigger | `WARNING` | `--force` |

### Roles & Grants

| Change Type | Severity | Flag Required |
|-------------|----------|---------------|
| Add role | `INFO` | None |
| Drop role | `WARNING` | `--force` |
| Change role options | `WARNING` | `--force` |
| Add grant | `INFO` | None |
| Revoke grant | `WARNING` | `--force` |
| Add default privilege | `INFO` | None |
| Revoke default privilege | `WARNING` | `--force` |

### Event Triggers

| Change Type | Severity | Flag Required |
|-------------|----------|---------------|
| Add event trigger | `INFO` | None |
| Drop event trigger | `WARNING` | `--force` |
| Change event trigger tags | `INFO` | None |

### Extended Statistics

| Change Type | Severity | Flag Required |
|-------------|----------|---------------|
| Add extended statistic | `INFO` | None |
| Drop extended statistic | `WARNING` | `--force` |
| Change kinds / columns | `WARNING` | `--force` |
| Change statistics target | `INFO` | None |

### Views

| Change Type | Severity | Flag Required |
|-------------|----------|---------------|
| Add view | `INFO` | None |
| Drop view | `WARNING` | `--force` |
| Change view query | `WARNING` | `--force` |
| Refresh materialized view | `INFO` | None |
| Change view schema | `WARNING` | `--force` |

### Schemas

| Change Type | Severity | Flag Required |
|-------------|----------|---------------|
| Add schema | `INFO` | None |
| Drop schema | `WARNING` | `--force` |

### RLS & Policies

| Change Type | Severity | Flag Required |
|-------------|----------|---------------|
| Enable RLS | `INFO` | None |
| Disable RLS | `WARNING` | `--force` |
| Force RLS | `INFO` | None |
| Add policy | `INFO` | None |
| Drop policy | `INFO` | None |
| Change policy expression | `INFO` | None |

### Partitions

| Change Type | Severity | Flag Required |
|-------------|----------|---------------|
| Add partition | `INFO` | None |
| Detach partition | `WARNING` | `--force` |
| Change partition strategy | `CRITICAL` | `--force` |

## Safe / Warn / Critical Type Changes

### Safe Type Changes (INFO)

| Conversion | Example |
|------------|---------|
| `VARCHAR(n)` → `VARCHAR(m)` (widening) | `VARCHAR(50)` → `VARCHAR(100)` |
| `VARCHAR` → `TEXT` | `VARCHAR` → `TEXT` |
| Storing to same type family | No change |
| `TIMESTAMP` → `TIMESTAMPTZ` | Adding timezone info |
| `json` → `jsonb` | JSON to binary JSON |
| Adding `NOT NULL` when column has no NULLs | Safe when data validates |

### Warn Type Changes (WARNING)

| Conversion | Example |
|------------|---------|
| `VARCHAR(n)` → `VARCHAR(m)` (narrowing) | `VARCHAR(100)` → `VARCHAR(50)` |
| `INTEGER` → `BIGINT` | Widening (safe in PG, uses USING) |
| `BIGINT` → `INTEGER` | Narrowing (potential data loss) |
| `TEXT` → `VARCHAR(n)` | Truncation risk |
| `NUMERIC(p,s)` → `NUMERIC(p',s')` with precision loss | May truncate |
| Any type change that requires USING | Needs manual verification |

### Critical Type Changes (CRITICAL)

| Conversion | Example |
|------------|---------|
| Dropping the only column of a table | Always critical |
| Removing enum values | Not supported by PostgreSQL |
| Changing partition key columns | Requires full table rewrite + data migration |
| Converting from `SETOF`/`TABLE` return type | Schema breakage |
