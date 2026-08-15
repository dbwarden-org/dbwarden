# Configuration API Reference

Complete reference for the `database_config()` function and the equivalent
`DbwardenDatabase` declarative API.

This is a reference page. For step-by-step guides, see
[Quick Start](../configuration/quick-start.md), [Concepts](../configuration/concepts.md),
or [Production Patterns](../configuration/production-patterns.md).

## Function Signature

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
    **plugin_config: Any,
) -> DatabaseHandle:
    """Register a database in dbwarden and return a handle with session dependencies."""
```

## Plugin-contributed arguments

Backend object keys such as `pg_roles`, `pg_functions`, and `ch_grants` are **not** part of core. Each is owned by the plugin that also supplies its object handler, and arrives through `**plugin_config`.

Core validates every extra keyword against the plugins actually installed:

- A key owned by a plugin that is **not installed** raises `DBWardenConfigError` naming the plugin to install.
- A key **no plugin owns** raises `DBWardenConfigError` as an unexpected keyword argument, so typos fail immediately.

Both fire when your `dbwarden.py` is loaded, not at migration time.

| Config keys | Plugin |
|---|---|
| `pg_roles`, `pg_default_privileges` | `dbwarden-pgsql-rbac` |
| `pg_domains`, `pg_sequences`, `pg_composite_types` | `dbwarden-pgsql-types` |
| `pg_extensions`, `pg_functions`, `pg_triggers`, `pg_event_triggers`, `pg_extended_statistics` | `dbwarden-pgsql-extensions` |
| `ch_named_collections`, `ch_roles`, `ch_users`, `ch_row_policies`, `ch_quotas`, `ch_settings_profiles`, `ch_grants` | `dbwarden-ch-rbac` |

Install with `dbwarden plugin add <name>`. `pg_schema` and `pg_migration_lock_timeout` are core and need no plugin.

## Declarative API

For configuration that benefits from inheritance, define a concrete subclass of
`DbwardenDatabase`. Every explicit `database_config()` field is available as a
class attribute, and concrete subclasses register automatically when imported.

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

Plugin keys may also be declared directly as class attributes:

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
# Primary is default
primary = database_config(
analytics = database_config(database_name="analytics", ...)  # default=False implied
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
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://localhost/myapp",
    model_paths=["app.models"],
    model_tables=["users", "posts", "comments"],
)

audit = database_config(
    database_name="audit",
    database_type="postgresql",
    database_url_sync="postgresql://localhost/audit",
    model_paths=["app.models"],
    model_tables=["audit_logs"],
)
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
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://localhost/myapp",
    migration_table="custom_migrations",
)
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
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://localhost/myapp",
    seed_table="custom_seeds",
)
```

Use this when integrating with an existing database that already reserves the seed table name.

### `auto_apply_seeds`

When `True`, dbwarden automatically applies pending code seeds after each successful `migrate` run.

- Defaults to `False`
- Applies per database entry
- Can be overridden per-run with `--apply-seeds` / `--no-apply-seeds` CLI flags on `migrate`

**Example:**

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://localhost/myapp",
    auto_apply_seeds=True,
)
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
-  Use SQLite locally for speed (if production is PostgreSQL)
-  Target a separate development database instance
-  Test migrations safely before running against production
-  Each developer has isolated database
-  Easy to reset (just delete the file)

**Example:**
```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://prod-host/myapp",
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./dev.db",
)
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

DATABASE_URL = os.getenv("DATABASE_URL")

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync=DATABASE_URL,
    secure_values=True,  #  Enable secure display
)
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
from dbwarden import database_config

primary = database_config(database_name="primary", ...)
analytics = database_config(database_name="analytics", ...)
```

```python
# routes.py
from ..dbwarden import primary, analytics

@router.get("/users")
async def get_users(session: primary.async_session):
    return await session.execute(...)

@router.get("/reports")
def get_reports(session: analytics.sync_session):
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
from dbwarden import database_config


primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:password@localhost:5432/mydb",
)
```

### With local development (recommended)

```python
from dbwarden import database_config


primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:password@localhost:5432/mydb",
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./development.db",
)
```

### Multi-database setup

```python
from dbwarden import database_config


primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:password@localhost:5432/main",
    model_paths=["app.models.api"],
)

analytics = database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="http://clickhouse:password@clickhouse-host:8123/analytics",
    model_paths=["app.models.analytics"],
)
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
