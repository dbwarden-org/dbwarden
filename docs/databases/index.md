# Supported Databases

DBWarden supports PostgreSQL (the default and first-class backend), MySQL, MariaDB, SQLite, and ClickHouse.

A **round-trip** backend is one where DBWarden can both read schema (via `generate-models`) and write schema (via `make-migrations` / `migrate`).

## Backend Matrix

| Backend | `database_type` | Typical URL | Round-Trip |
|---------|------------------|-------------|------------|
| PostgreSQL | `postgresql` | `postgresql://user:pass@host:5432/db` | Yes |
| MySQL | `mysql` | `mysql://user:pass@host:3306/db` | Yes |
| MariaDB | `mariadb` | `mariadb://user:pass@host:3306/db` | No |
| ClickHouse | `clickhouse` | `clickhouse://user:pass@host:8123/db` | Yes |
| SQLite | `sqlite` | `sqlite:///./app.db` | Dev only |

## Optional Dependency Groups

When you install `dbwarden`, the `[postgres]` extra is included by default (providing the PostgreSQL driver). For other backends you must specify the corresponding extra:

| Extra | Command | Driver |
|-------|---------|--------|
| `[postgres]` | Included by default | `psycopg2-binary` |
| `[mysql]` | `uv add "dbwarden[mysql]"` | `pymysql` |
| `[mariadb]` | `uv add "dbwarden[mariadb]"` | `pymysql` |
| `[clickhouse]` | `uv add "dbwarden[clickhouse]"` | `clickhouse-connect` |

See [Installation](../installation.md) for full details.

## Config Examples

PostgreSQL:

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:password@localhost:5432/main",
)
```

MySQL:

```python
legacy = database_config(
    database_name="legacy",
    database_type="mysql",
    database_url_sync="mysql://user:password@localhost:3306/legacy",
)
```

SQLite:

```python
dev = database_config(
    database_name="dev",
    database_type="sqlite",
    database_url_sync="sqlite:///./development.db",
)
```

ClickHouse:

```python
analytics = database_config(
    database_name="analytics",
    database_type="clickhouse",
    database_url_sync="clickhouse://user:password@localhost:8123/analytics",
)
```

## Internal Connection Handling

DBWarden uses SQLAlchemy engines, with backend-specific URL normalization where needed.

Conceptual flow:

```python
def get_engine(config):
    url = config.sqlalchemy_url
    if config.database_type == "clickhouse":
        url = normalize_clickhouse_dialect(url)
    return create_engine(url)
```

Connections include retry logic: `get_db_connection()` wraps engine connections with up to 5 attempts and exponential backoff when the database is temporarily unavailable (e.g. during a restart or network hiccup). Engines are cached and reused across calls.

For PostgreSQL schema support, set `pg_schema` in `database_config(...)`. DBWarden sets `search_path` on connection so all unqualified references use that schema:

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:pass@localhost:5432/main",
    pg_schema="app",
)
```

At the model level, set `pg_schema` on `PGTableMeta` or `PGViewMeta` to scope a specific table or view to a schema. This takes precedence over the config-level `search_path`. See [PostgreSQL Deep Dive](postgresql/index.md) for full details.

## Development Database Strategy

Recommended pattern:

- Production-like primary DB (for example PostgreSQL)
- SQLite for dev DB via `dev_database_url`
- Run local commands with `--dev`

```python
primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:password@localhost:5432/main",
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./development.db",
)
```

```bash
$ dbwarden --dev make-migrations "sync models" -d primary
$ dbwarden --dev migrate -d primary
```

## Translation Note

When targeting SQLite in dev mode, DBWarden translates unsupported backend-specific types/defaults.

- Unknown/unsupported types fallback to `TEXT` with warnings
- `--strict-translation` turns those warnings into errors

Details: [SQL Translation](../sql-translation.md)

## Backend-Specific Notes

Each backend has deep-dive documentation:

| Backend | Guide |
|---------|-------|
| PostgreSQL | [PostgreSQL Deep Dive](postgresql/index.md) |
| MySQL / MariaDB | [MySQL Deep Dive](mysql.md) |
| SQLite | [SQL Databases](sql-databases.md) |
| ClickHouse | [ClickHouse Deep Dive](clickhouse/index.md) |

### PostgreSQL

PostgreSQL is a **first-class backend** with full round-trip support. All metadata: identity columns, collation, storage, compression, generated columns, fillfactor, tablespace, inheritance, exclude constraints, deferrable FKs, and advanced index options, is captured by the snapshot, diffed correctly, and emitted as valid DDL.

See [PostgreSQL Deep Dive](postgresql/index.md) for the complete reference.

### MySQL

MySQL is a **first-class backend** with full round-trip support. All metadata: engine, charset, collation, row format, auto_increment, unsigned columns, ON UPDATE, and column comments, is captured by the snapshot, diffed correctly, and emitted as valid DDL.

Key MySQL DDL behavior:

- **DDL is NOT transactional**: each statement auto-commits; partial failure possible
- Column type/nullable changes use `MODIFY COLUMN` (requires full column definition)
- Table comments use `ALTER TABLE t COMMENT = '...'` (not `COMMENT ON`)
- Column comments use `MODIFY COLUMN ... COMMENT '...'` (full column definition preserved)
- Auto-increment toggle uses `MODIFY COLUMN ... AUTO_INCREMENT`
- FK drop uses `DROP FOREIGN KEY` (not `DROP CONSTRAINT`)

See [MySQL Deep Dive](mysql.md) for the complete reference.

### MariaDB

MariaDB is supported as a separate `database_type` (`mariadb`), but it does **not** have round-trip support. You can use MariaDB as a target database for migrations, but `generate-models` and full schema introspection are not available. Use `make-migrations` to write migrations manually.

See [MySQL Deep Dive](mysql.md) for MariaDB-specific notes.

### SQLite

- Great for local tests and dev loops via `--dev` mode
- Limited DDL: no `ALTER COLUMN TYPE`, no `SET/DROP NOT NULL`, no FK alterations
- `--safe-type-change` emits a comment (not supported)
- Type affinity differs from server databases
- See [SQL Translation](../sql-translation.md) for dev-mode type mapping

### ClickHouse

ClickHouse has full round-trip support: `generate-models` reads schema from a live ClickHouse server, and `make-migrations` / `migrate` auto-generates DDL for table operations.

- HTTP-based wire protocol; DBWarden uses ClickHouse client, not SQLAlchemy session
- DDL operations are mostly auto-generated: table rename, column type change, nullable and LowCardinality changes, projections, RBAC objects, and engine recreation. Placeholder rollback is refused by the strict rollback contract unless the migration is explicitly irreversible.
- Full engine metadata support via `class Meta(CHTableMeta)` with `ChEngineSpec`, `ProjectionSpec`, `CHColumnMeta`
- Supports materialized views, projections, dictionaries, replicated engines
- See [ClickHouse Deep Dive](clickhouse/index.md) for full details

## Recommended Verification Workflow

```bash
# local loop on dev DB
$ dbwarden --dev migrate -d primary

# pre-release validation on production-like DB
$ dbwarden migrate -d primary
$ dbwarden status -d primary
```
