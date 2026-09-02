# Configuration API Reference

Complete reference for the `DbwardenDatabase` declarative API and the equivalent
`database_config()` function.

This is a reference page. For step-by-step guides, see
[Quick Start](../configuration/quick-start.md), [Concepts](../configuration/concepts.md),
or [Production Patterns](../configuration/production-patterns.md).

## Declarative API (recommended)

Define a concrete subclass of `DbwardenDatabase`. Every field is available as a
class attribute, and concrete subclasses register automatically when imported.

```python
from dbwarden import DbwardenDatabase


class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://user:pass@localhost:5432/myapp"
    model_paths = ["app.models"]
```

### Inheritance

For configuration that benefits from inheritance, use an abstract base class:

```python
from dbwarden import DbwardenDatabase


class Shared(DbwardenDatabase):
    __abstract__ = True
    database_type = "postgresql"
    model_paths = ["app.models"]
    plugin_config = {
        "pg_roles": ["app_owner"],
    }


class Primary(Shared):
    database_name = "primary"
    database_url_sync = "postgresql://user:pass@localhost/myapp"
    default = True
```

### Plugin keys as class attributes

Plugin keys may be declared directly as class attributes:

```python
class Primary(DbwardenDatabase):
    database_name = "primary"
    database_url_sync = "postgresql://user:pass@localhost/myapp"
    pg_roles = ["app_owner"]
```

`Primary.handle` is the `DatabaseHandle` returned by the function API. Mutable
values such as `model_paths`, `model_tables`, and `plugin_config` are copied for
each registered subclass, so child classes can override them without mutating
their base class.

## Function API

The equivalent `database_config()` function:

```python
def database_config(
    *,
    database_name: str,
    database_type: Literal["sqlite", "postgresql", "mysql", "mariadb", "clickhouse"] = "sqlite",
    database_url_sync: str | None = None,
    database_url_async: str | None = None,
    default: bool = False,
    migrations_dir: str | None = None,
    migration_table: str | None = None,
    seed_table: str | None = None,
    auto_apply_seeds: bool = False,
    model_paths: list[str] | None = None,
    model_tables: list[str] | None = None,
    dev_database_type: str | None = None,
    dev_database_url: str | None = None,
    overlap_models: bool = False,
    secure_values: bool = False,
    pg_schema: str | None = None,
    pg_migration_lock_timeout: int | None = None,
    ch_cluster: str | None = None,
    ch_replicated_database: bool = False,
    clickhouse_lock_ttl: int | None = None,
    lock_namespace: str | None = None,
    **plugin_config: Any,
) -> DatabaseHandle:
    """Register a database in dbwarden and return a handle with session dependencies."""
```

## Required arguments

| Argument | Type | Description |
|----------|------|-------------|
| `database_name` | `str` | unique name for this database in your project |
| `database_type` | `str` | backend type: `sqlite`, `postgresql`, `mysql`, `mariadb`, or `clickhouse` (default: `"sqlite"`) |

At least one of `database_url_sync` or `database_url_async` must be provided.

## Optional arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `database_url_sync` | `str | None` | `None` | synchronous connection URL (used by migrations, CLI, and sync sessions) |
| `database_url_async` | `str | None` | `None` | async connection URL (used by async sessions; falls back to `database_url_sync` if omitted) |
| `default` | `bool` | `False` | if `True`, this database is used when `--database` is omitted |
| `skip_if_missing` | `bool` | `False` | if `True`, skip this database when its connection URL is not available (e.g., optional databases in multi-DB setups) |
| `migrations_dir` | `str | None` | `None` | custom migration directory path (defaults to `migrations/<database_name>`) |
| `migration_table` | `str | None` | `None` | custom migration tracking table name (defaults to `_dbwarden_migrations`) |
| `seed_table` | `str | None` | `None` | custom seed tracking table name (defaults to `_dbwarden_seeds`) |
| `auto_apply_seeds` | `bool` | `False` | if `True`, automatically apply pending code seeds after `migrate` |
| `model_paths` | `list[str] | None` | `None` | list of Python import paths containing SQLAlchemy models for this database |
| `model_tables` | `list[str] | None` | `None` | optional filter: only these table names are owned by this database |
| `dev_database_type` | `str | None` | `None` | backend type for local development (used with `--dev`) |
| `dev_database_url` | `str | None` | `None` | connection URL for local development (used with `--dev`) |
| `overlap_models` | `bool` | `False` | if `True`, allow model path overlap with other databases |
| `secure_values` | `bool` | `False` | if `True`, display commands show variable names instead of resolved values |
| `pg_schema` | `str | None` | `None` | PostgreSQL schema name; sets `search_path` on connection so all unqualified references use that schema |
| `pg_migration_lock_timeout` | `int | None` | `None` | PostgreSQL lock timeout in seconds for migration DDL; prevents indefinite waits on conflicting locks |
| `ch_cluster` | `str | None` | `None` | ClickHouse cluster name; appends `ON CLUSTER '<name>'` to every DDL statement. Mutually exclusive with `ch_replicated_database` |
| `ch_replicated_database` | `bool` | `False` | ClickHouse replicated database engine; DDL propagates automatically via ZooKeeper, `ON CLUSTER` must be omitted. Mutually exclusive with `ch_cluster` |
| `clickhouse_lock_ttl` | `int | None` | `None` | ClickHouse lease TTL in seconds (CH-0/CH-1). Defaults to 120 if not set. |
| `lock_namespace` | `str | None` | `None` | Lock scope. Allows independent lock streams. Defaults to `"default"` if not set. |

## Field descriptions

### `database_name`

A unique identifier for this database within your project.

**Requirements:**

- Must be unique across all entries in your config source
- Used in CLI `--database` / `-d` flags to select this database
- Becomes part of migration filename prefix (for versioned migrations)

**Examples:**
```python
database_name="primary"
database_name="analytics"
database_name="legacy"
```

Use descriptive names that reflect the database's purpose: `primary`, `analytics`, `audit_logs`, etc.

### `database_type`

The database backend technology. Each value determines:

- URL parsing behavior
- SQL dialect and syntax handling
- Available features (transactions, DDL, constraints)

Valid values: `sqlite`, `postgresql`, `mysql`, `mariadb`, `clickhouse`

### `database_url_sync` and `database_url_async`

Connection URL strings in the format:

```
[dialect+driver://user:password@]host[:port][/database][?options]
```

- **`database_url_sync`**  used by CLI commands (`migrate`, `init`, etc.) and any sync session
- **`database_url_async`**  used by async sessions (FastAPI); falls back to `database_url_sync` if omitted

At least one must be provided. If only `database_url_sync` is given, async sessions will use it (with async driver substitution like `postgresql://...`  `postgresql+asyncpg://...`).

Examples:

```python
# Both sync and async (recommended for FastAPI projects)
database_url_sync = "postgresql://user:password@localhost:5432/mydb"
database_url_async = "postgresql+asyncpg://user:password@localhost:5432/mydb"

# Sync only (CLI-only projects)
database_url_sync = "postgresql://user:password@localhost:5432/mydb"

# SQLite (relative path)
database_url_sync = "sqlite:///./development.db"

# MySQL
database_url_sync = "mysql://user:password@localhost:3306/mydb"

# ClickHouse
database_url_sync = "http://user:password@clickhouse-host:8123/mydb"
```

### `default`

When `True`, this database is selected when `--database` / `-d` is not specified.

**Rule:** Exactly one entry must have `default=True`.

**Example:**
```python
class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://user:pass@localhost:5432/mydb"

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    database_type = "clickhouse"
    database_url_sync = "clickhouse://localhost:8123/analytics"
    # default=False implied
```

Exactly one database must have `default=True`. Having zero or multiple defaults will cause a validation error.

### `migrations_dir`

Path where this database's migration files are stored.

- Defaults to `migrations/<database_name>`
- Each database should have its own directory to avoid collision
- Versioned migration files go here (`NNNN_description.sql`)
- Repeatable migration files go here (`RA__*.sql`, `ROC__*.sql`)

### `model_paths`

A list of Python import paths where dbwarden should discover SQLAlchemy model definitions.

**When required:**

- **Single database:** Optional (dbwarden scans entire codebase)
- **Multiple databases:** Required for each database

**How it works:** dbwarden imports each path and inspects classes inheriting from `DeclarativeBase` or `declarative_base()`.

**Examples:**
```python
# Single module
model_paths=["app.models"]

# Multiple modules
model_paths=["app.models.primary", "app.legacy"]

# Nested modules
model_paths=["app.models.api.v1", "app.models.api.v2"]
```

Specifying `model_paths` makes discovery faster and more predictable, even for single-database projects.

See [Multi-Database Guide](../configuration/multi-database.md) for organizing models across databases.

### `model_tables`

A downstream filter applied after model discovery. Only tables whose string
name appears in this list are owned by this database. All other discovered
tables are ignored.

**When to use:**

- **Multi-database shared `model_paths`:** Two databases share the same
  import path but own different subsets of tables.
- **Selective deployment:** A microservice owns only a few tables from a
  shared models package.

**How it works:**
1. dbwarden discovers all models via `model_paths`
2. If `model_tables` is set, it validates every name exists among the
   discovered tables
3. Only the matching tables participate in migrations, diffs, and exports

**Example:**
```python
class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://localhost/myapp"
    model_paths = ["app.models"]
    model_tables = ["users", "posts", "comments"]

class Audit(DbwardenDatabase):
    database_name = "audit"
    database_type = "postgresql"
    database_url_sync = "postgresql://localhost/audit"
    model_paths = ["app.models"]
    model_tables = ["audit_logs"]
```

**Overlap validation:** If two databases both set `model_tables` with
overlapping names, dbwarden raises an error (same behavior as
`model_paths` overlap). Set `overlap_models=True` to allow it.

**Must be valid SQL identifiers.** Dotted (schema-qualified) names are not
supported in the initial release.

### `migration_table`

Name of the table dbwarden uses to record applied migrations and repeatable migration checksums.

- Defaults to `_dbwarden_migrations`
- Must be a valid SQL identifier
- Applies per database entry
- Only affects migration tracking metadata; lock tables are separate

**Example:**

```python
class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://localhost/myapp"
    migration_table = "custom_migrations"
```

Use this when:

- integrating with an existing database that already reserves a migrations table name
- isolating dbwarden metadata under a project-specific convention

### `seed_table`

Name of the table dbwarden uses to record applied seeds.

- Defaults to `_dbwarden_seeds`
- Must be a valid SQL identifier
- Applies per database entry

**Example:**

```python
class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://localhost/myapp"
    seed_table = "custom_seeds"
```

Use this when integrating with an existing database that already reserves the seed table name.

### `auto_apply_seeds`

When `True`, dbwarden automatically applies pending code seeds after each successful `migrate` run.

- Defaults to `False`
- Applies per database entry
- Can be overridden per-run with `--apply-seeds` / `--no-apply-seeds` CLI flags on `migrate`

**Example:**

```python
class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://localhost/myapp"
    auto_apply_seeds = True
```

Use this when:

- you want seeds to stay in sync with schema changes without manual `seed apply` steps
- deploying code seeds that define reference data or lookup tables
- running in CI/CD where every migration cycle should also re-seed

### `dev_database_type` and `dev_database_url`

These define an alternate connection for local development workflows.

When `--dev` is passed to any dbwarden command:

- `database_type` is swapped to `dev_database_type`
- `database_url_sync` / `database_url_async` are swapped to `dev_database_url`

**Benefits:**

- Use SQLite locally for speed (if production is PostgreSQL)
- Target a separate development database instance
- Test migrations safely before running against production
- Each developer has isolated database
- Easy to reset (just delete the file)

**Example:**
```python
class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://prod-host/myapp"
    dev_database_type = "sqlite"
    dev_database_url = "sqlite:///./dev.db"
```

Use with:
```bash
$ dbwarden --dev migrate  # Uses SQLite
$ dbwarden migrate        # Uses PostgreSQL
```

Use SQLite with `dev_database_url="sqlite:///./dev.db"` for the fastest local iteration loop.

See [Dev Mode](../configuration/dev-mode.md) for complete workflow and patterns.

### `overlap_models`

By default, dbwarden prevents model path overlap between databases.

Set `overlap_models=True` when:

- Two databases legitimately share model definitions
- You understand the behavior implications (both databases will include overlapping tables)

### `secure_values`

When enabled, CLI display commands show the original variable/expression for non-literal arguments instead of resolved values.

**Use when:**

- Your config uses environment variables or expressions for secrets
- You want terminal output to avoid exposing credentials
- Running commands in CI/CD with logged output

**Example:**

```python
import os
from dbwarden import DbwardenDatabase

DATABASE_URL = os.getenv("DATABASE_URL")

class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = DATABASE_URL
    secure_values = True  # Enable secure display
```

**Without `secure_values`:**
```bash
$ dbwarden settings show
URL: postgresql://user:SECRET_PASSWORD@prod-host/myapp
```

**With `secure_values=True`:**
```bash
$ dbwarden settings show --all
URL: DATABASE_URL (expression)
```

Always set `secure_values=True` in production to prevent credential exposure in logs.

### `ch_cluster`

ClickHouse cluster name. When set, dbwarden appends `ON CLUSTER '<name>'` to every DDL statement (CREATE, ALTER, DROP, RENAME, DETACH, ATTACH) for this database.

**Use when:**

- You have a multi-node ClickHouse cluster and want DDL distributed automatically
- You want explicit control over which DDL goes to which cluster

**Example:**

```python
class Analytics(DbwardenDatabase):
    database_name = "analytics"
    database_type = "clickhouse"
    database_url_sync = "clickhouse://clickhouse1:8123/analytics"
    ch_cluster = "production_cluster"
```

**Validation:**

- Mutually exclusive with `ch_replicated_database` (setting both raises `ConfigurationError`)
- Must be a non-empty string when set
- Cluster name must match a cluster defined in ClickHouse's `remote_servers.xml`

See [ON Cluster](../databases/clickhouse/on-cluster.md) for full details on DDL injection, supported statement types, and the recreate pipeline.

### `ch_replicated_database`

When `True`, dbwarden uses the ClickHouse `Replicated` database engine. DDL propagates automatically through ZooKeeper, so `ON CLUSTER` must be omitted.

**Use when:**

- You want automatic DDL replication via ZooKeeper without explicit `ON CLUSTER` clauses
- Your tables use `Replicated*` engine variants

**Example:**

```python
class Analytics(DbwardenDatabase):
    database_name = "analytics"
    database_type = "clickhouse"
    database_url_sync = "clickhouse://clickhouse1:8123/analytics"
    ch_replicated_database = True
```

**Validation:**

- Mutually exclusive with `ch_cluster` (setting both raises `ConfigurationError`)
- When `True`, use `Replicated*` engine variants (e.g., `replicated_merge_tree()`) in your models

See [ON Cluster](../databases/clickhouse/on-cluster.md) for the comparison between ON CLUSTER and replicated database modes.

### `clickhouse_lock_ttl`

Lease TTL in seconds for ClickHouse migration locking (CH-0 and CH-1 profiles). Controls how long a migration lock is held before auto-expiring. Default is 120 seconds.

**Use when:**

- Your migrations take longer than 2 minutes and the lock expires mid-run
- You want a shorter lock window for faster failover

**Example:**

```python
class Analytics(DbwardenDatabase):
    database_name = "analytics"
    database_type = "clickhouse"
    database_url_sync = "clickhouse://clickhouse1:8123/analytics"
    clickhouse_lock_ttl = 600  # 10 minutes
```

### `lock_namespace`

Lock scope for migration locking. Allows independent lock streams so different operations (schema migrations, data operations) can run concurrently without conflicting.

**Use when:**

- You run schema migrations and data operations as separate processes
- You need independent lock streams for different deployment stages

**Example:**

```python
# Schema migrations
class SchemaDb(DbwardenDatabase):
    database_name = "analytics"
    database_type = "clickhouse"
    database_url_sync = "clickhouse://clickhouse1:8123/analytics"
    lock_namespace = "schema"

# Data operations
class DataDb(DbwardenDatabase):
    database_name = "analytics"
    database_type = "clickhouse"
    database_url_sync = "clickhouse://clickhouse1:8123/analytics"
    lock_namespace = "data"
```

## Return value: `DatabaseHandle`

`database_config()` returns a `DatabaseHandle` object with two
properties designed as FastAPI dependency annotations:

| Property | Resolves to (SQL) | Resolves to (ClickHouse) |
|----------|-------------------|--------------------------|
| `.async_session` | `Annotated[AsyncSession, Depends(...)]` | `Annotated[AsyncClient, Depends(...)]` |
| `.sync_session` | `Annotated[Session, Depends(...)]` | `Annotated[Client, Depends(...)]` |

Use them as type hints in FastAPI route parameters. The handle serves as
a namespace so you never confuse which database a session belongs to:

```python
# dbwarden.py
from dbwarden import DbwardenDatabase

class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://user:pass@localhost/myapp"

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    database_type = "clickhouse"
    database_url_sync = "clickhouse://clickhouse1:8123/analytics"
```

```python
# routes.py
from ..dbwarden import Primary, Analytics

@router.get("/users")
async def get_users(session: Primary.handle.async_session):
    return await session.execute(...)

@router.get("/reports")
def get_reports(session: Analytics.handle.sync_session):
    return session.execute(...)
```

Use `.async_session` for async route handlers and `.sync_session` for sync
handlers. The deprecated `.session` property (aliased to `.async_session`)
will be removed in a future version.

`DatabaseHandle` is still useful as a typed container even without FastAPI. Access
`handle._name` and `handle._db_type` for the raw config values.

## Configuration rules (enforced at load time)

dbwarden validates your config to prevent dangerous misconfigurations:

| Rule | Error message (if violated) |
|------|---------------------------|
| Exactly one `default=True` | `Exactly one default=True required` |
| Unique `database_name` across all entries | `Duplicate database_name` |
| Unique `database_url_sync` across all entries | `Duplicate database_url_sync` |
| Unique physical target (even across credentials) | `Duplicate database target detected` |
| Required `model_paths` when multiple databases | `model_paths is required when more than one database is configured` |
| Explicit `overlap_models` when paths overlap | `model_paths overlap detected` |
| `model_tables` (if set) must not overlap across databases | `model_tables overlap detected` |
| If `dev_database_type` set, `dev_database_url` also required | `dev_database_url is required when dev_database_type is set` |

## Loading and resolution

Config is loaded by importing your Python config source and executing `database_config(...)` calls.

The resolution priority is:

1. Look for `dbwarden.py` in the current directory or parent directories
2. If `DBWARDEN_CONFIG_MODULE` environment variable is set, use that module
3. Full scan for any file containing `database_config(...)` calls

If more than one discovery source is found, dbwarden fails with an ambiguity error.

`dbwarden.py` is the default convention, but it is not the only valid location. Any discovered Python file inside the project can call `database_config(...)`.

### Security sandbox

Config files are loaded with path traversal protection that ensures the file is within the project tree. See [Configuration Concepts  Config Loading Security](../configuration/concepts.md#config-loading-security-sandbox).

## Examples

### Minimal single-database setup

```python
from dbwarden import DbwardenDatabase

class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://user:password@localhost:5432/mydb"
```

### With local development (recommended)

```python
from dbwarden import DbwardenDatabase

class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://user:password@localhost:5432/mydb"
    dev_database_type = "sqlite"
    dev_database_url = "sqlite:///./development.db"
```

### Multi-database setup

```python
from dbwarden import DbwardenDatabase

class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://user:password@localhost:5432/main"
    model_paths = ["app.models.api"]

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    database_type = "clickhouse"
    database_url_sync = "http://clickhouse:password@clickhouse-host:8123/analytics"
    model_paths = ["app.models.analytics"]
```

### ClickHouse with ON CLUSTER

```python
from dbwarden import DbwardenDatabase

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    database_type = "clickhouse"
    database_url_sync = "clickhouse://clickhouse1:8123/analytics"
    model_paths = ["app.models.analytics"]
    ch_cluster = "production_cluster"
```

### ClickHouse with replicated database

```python
from dbwarden import DbwardenDatabase

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    database_type = "clickhouse"
    database_url_sync = "clickhouse://clickhouse1:8123/analytics"
    model_paths = ["app.models.analytics"]
    ch_replicated_database = True
```

## Quick Reference

| Parameter | Required? | Default | Use When |
|-----------|-----------|---------|----------|
| `database_name` |  Yes | - | Always |
| `database_type` |  No | `"sqlite"` | Non-SQLite backends |
| `database_url_sync` |  Conditional | `None` | CLI or sync sessions |
| `database_url_async` |  No | `None` | Async sessions (FastAPI) |
| `default` |  No | `False` | Mark one database as default |
| `migrations_dir` |  No | `migrations/<name>` | Custom migration directory |
| `seed_table` |  No | `_dbwarden_seeds` | Custom seed tracking table |
| `auto_apply_seeds` |  No | `False` | Auto-apply seeds after migrate |
| `migration_table` |  No | `_dbwarden_migrations` | Custom migration tracking table |
| `model_paths` |  Conditional | `None` | Multi-database or explicit discovery |
| `model_tables` |  No | `None` | Filter discovered tables by name |
| `dev_database_type` |  No | `None` | Local development |
| `dev_database_url` |  No | `None` | Local development |
| `overlap_models` |  No | `False` | Shared models (read replicas) |
| `secure_values` |  No | `False` | Hide credentials in output |
| `pg_schema` |  No | `None` | PostgreSQL schema name for search_path |
| `pg_migration_lock_timeout` |  No | `None` | PostgreSQL lock timeout (seconds) for migrations |
| `ch_cluster` |  No | `None` | ClickHouse cluster name for ON CLUSTER DDL |
| `ch_replicated_database` |  No | `False` | ClickHouse replicated database engine |
| `clickhouse_lock_ttl` |  No | `None` | ClickHouse lease TTL in seconds |
| `lock_namespace` |  No | `None` | Lock scope for independent streams |

## Related Documentation

**Getting Started:**

- [Quick Start](../configuration/quick-start.md) - Your first configuration
- [Concepts](../configuration/concepts.md) - How configuration works

**Guides:**

- [Connection URLs](../configuration/connection-urls.md) - Database URL formats
- [Model Discovery](../configuration/model-discovery.md) - How `model_paths` works
- [Dev Mode](../configuration/dev-mode.md) - Local development
- [Multi-Database](../configuration/multi-database.md) - Multiple databases
- [Production Patterns](../configuration/production-patterns.md) - Real-world examples

**Help:**

- [Troubleshooting](../configuration/troubleshooting.md) - Common issues
