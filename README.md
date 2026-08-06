<p align="center">
  <img src="https://raw.githubusercontent.com/dbwarden-org/dbwarden/refs/heads/main/assets/icon.png" alt="DBWarden" width="128"/>
</p>
<p align="center">
  <strong style="font-size: 2.5em;">DBWarden</strong>
</p>
<p align="center">
    <em>Your SQLAlchemy models are your migrations.</em>
</p>
<p align="center">
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/Python-3.12.7%2B-3776AB?logo=python&logoColor=white&style=for-the-badge" alt="Python">
  </a>
  <a href="https://pypi.org/project/dbwarden/">
    <img src="https://img.shields.io/pypi/v/dbwarden?logo=pypi&logoColor=white&style=for-the-badge" alt="PyPI">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-10AC84?style=for-the-badge" alt="License">
  </a>
  <a href="https://deepwiki.com/dbwarden-org/dbwarden/">
    <img src="https://img.shields.io/badge/DeepWiki-8A2BE2?logo=readthedocs&logoColor=white&style=for-the-badge" alt="DeepWiki">
  </a>
  <a href="https://codecov.io/gh/dbwarden-org/dbwarden">
    <img src="https://img.shields.io/codecov/c/github/dbwarden-org/dbwarden?logo=codecov&logoColor=white&style=for-the-badge" alt="Codecov">
  </a>
</p>

<p align="center">
  <strong><a href="https://dbwarden.emiliano-go.com/">Full documentation</a></strong>
  &nbsp;|&nbsp;
  <strong><a href="https://github.com/dbwarden-org/dbwarden">Source Code</a></strong>
</p>

---

DBWarden is a declarative database migration and schema management tool for SQLAlchemy. You declare the schema you want in your SQLAlchemy models, and DBWarden derives everything else: migration SQL, rollbacks, snapshots, and safety checks.

There are no migration scripts to write or maintain. There is no migration runtime. Your models are the contract. The database is kept in sync with them.

## At a glance

- Migrations generated from your models, not written by hand
- Plain SQL output: reviewable, committable, executable anywhere
- Strict rollback contract with placeholder rollback refused by default
- Pre-deploy impact analysis: know what breaks before it ships
- Offline migration generation for CI pipelines without a live database
- Schema snapshots for deterministic diffs and rename detection
- Typed `class Meta` system with import-time validation
- Multi-database support: PostgreSQL, MySQL, ClickHouse, MariaDB, SQLite
- Extensible plugin system with official plugins for seeds, RBAC, FastAPI, sandbox testing, and PostgreSQL/ClickHouse extensions
- Reverse-engineer live databases into models with `generate-models` (supports `--base` for custom imports)

## Why DBWarden

Schema management tools fall into two camps. Imperative tools have you author *changes*: revision scripts that describe how to get from one schema version to the next. Declarative tools have you author the *desired state* and derive the changes for you. DBWarden is declarative: your SQLAlchemy models are the single definition of what the schema should be.

Most imperative tools ask you to maintain two representations of your schema: your ORM models and your migration files. When they drift, you find out at deploy time.

DBWarden eliminates the second representation. Your SQLAlchemy models are the schema definition. DBWarden reads them, diffs them against the current database state, and generates the SQL to close the gap (including rollback) without you writing a line of migration code.

This also means:

- No migration runtime to install or version
- No generated Python scripts that quietly do the wrong thing
- No schema drift discovered in production: drift is caught at `make-migrations` time
- Migrations that can be generated in CI without a database connection

DBWarden is not a wrapper around Alembic. It is a different approach to the same problem. Alembic asks you to describe *how* to change the database; DBWarden asks you to describe *what* the schema should be. Alembic can autogenerate revisions, but each one becomes an imperative Python artifact you own, edit, and chain — the revision history is the source of truth. With DBWarden the models stay the source of truth, and the output is plain SQL.

Unlike tools that apply declarative diffs directly to the database, DBWarden still produces versioned, reviewable migration files with explicit rollbacks: declarative authoring without giving up auditable deploy artifacts.

## From zero to production

Typical adoption path in an existing project:

1. Point DBWarden at your existing SQLAlchemy models
2. Run initial `make-migrations` to generate a baseline schema
3. Commit generated migrations as your source of truth
4. Replace your current migration workflow with the DBWarden CLI
5. Optionally enable:
   - Migration impact analysis for safer deploys
   - Offline mode for CI pipelines without a database service

## Installation

```bash
uv add dbwarden
```

Requirements: Python 3.12+, SQLAlchemy 2.0+.

Optional dependency groups:

| Group        | Default | Provides                             |
|--------------|---------|--------------------------------------|
| `[postgres]` | Yes     | `psycopg2-binary`                    |
| `[mysql]`    |         | `pymysql`                            |
| `[clickhouse]` |       | `clickhouse-connect`, `aiohttp`      |
| `[dev]`      |         | `pytest`, `zensical`, `seoslug`, `httpx2` |

## Quickstart

### 1. Configure

Create a file named `dbwarden.py` in your project root:

```python
from dbwarden import database_config

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:pass@localhost:5432/myapp",
    database_url_async="postgresql+asyncpg://user:pass@localhost:5432/myapp",
)
```

### 2. Define your models

```python
from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base
from dbwarden.databases import TableMeta, IndexSpec

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    bio = Column(Text, nullable=True)

    class Meta(TableMeta):
        comment = "Core user accounts"


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False)

    class Meta(TableMeta):
        indexes = [
            IndexSpec(name="ix_posts_created_at", columns=["created_at"]),
        ]
```

### 3. Generate a migration

```bash
dbwarden init
dbwarden make-migrations "create initial tables"
```

Output: both upgrade and rollback in the same file.

```sql
-- upgrade
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    bio TEXT
);
COMMENT ON TABLE users IS 'Core user accounts';

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    body TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_posts_created_at ON posts (created_at);

-- rollback
DROP TABLE posts;
DROP TABLE users;
```

### 4. Apply

```bash
dbwarden migrate
```

### 5. Check status

```bash
dbwarden status
```

## Typical workflow

1. Define or update your SQLAlchemy models with `class Meta` annotations
2. Run `dbwarden make-migrations` to generate SQL
3. Review the generated `.sql` file and its rollback section
4. Run `dbwarden migrate` to apply
5. Verify with `dbwarden status`

---

## Migration engine

**Model-driven generation**: DBWarden reads your SQLAlchemy models directly. When you change a model, it diffs the new state against the last snapshot and generates the SQL to reconcile them.

**Plain SQL output**: Generated migrations are `.sql` files. No migration runtime, no generated Python. Review them, commit them, execute them directly against any environment.

**Rollback contract**: Generated migrations carry both upgrade and rollback sections. DBWarden emits executable rollback when it is safe, refuses placeholder rollback by default, and requires an explicit irreversible declaration when rollback cannot be produced.

**Schema snapshots**: After every migration, a checksummed JSON snapshot is written to `.dbwarden/schemas/`. Snapshots power rename detection, offline diffing, and column-level comparisons without querying the live database.

**Column-level diffing**: Type, nullability, default, and comment changes generate precise `ALTER COLUMN` statements.

**Typed `class Meta`**: The `_MetaValidator` metaclass validates every attribute on `class Meta` at import time. Typos that would have silently produced wrong DDL now raise `DBWardenConfigError` immediately.

```python
class Meta(MyTableMeta):
    my_engin = "InnoDB"  # DBWardenConfigError: unknown attr 'my_engin'
```

Supported index features:

- Partial indexes (`WHERE` clause)
- Covering indexes (`INCLUDE` columns)
- `USING` access methods
- `NULLS NOT DISTINCT` (PostgreSQL 15+)
- Per-column sort order
- Storage parameters (`WITH (fillfactor=...)`)
- ClickHouse skip indexes via `ChIndexSpec`

---

## Pre-deploy impact analysis

Before applying schema changes, DBWarden can scan your codebase to identify what will be affected. It uses AST analysis with a grep fallback, so results reflect actual code structure rather than text matches.

```bash
dbwarden check-impact 0042 --database primary
```

Output:

```
drop_column on users.username
  References: 2
    app/routes/users.py:34  attribute_access
      .username
    app/templates/profile.jinja2:12  grep
      user.username
```

Run this before any destructive deploy to surface breaking changes before they reach production.

---

## Offline migrations

Export model state once, then generate migrations on any machine without a database connection. Designed for CI pipelines and local development without a running database.

```bash
dbwarden export-models --database primary
git add .dbwarden/model_state.json
```

Then on any machine, with no database required:

```bash
dbwarden make-migrations "add bio column" --offline
```

The model state file is updated in place after each migration.

> **⚠️ WARNING: NEVER DELETE THE MODEL STATE FILE**
> This file (`.dbwarden/model_state.*.json`) is the source of truth for your database schema. You may **delete migration files** safely, but **NEVER delete the model state file**. If accidentally deleted, restore it from git immediately or re-generate with `dbwarden export-models`. Deleting it causes dbwarden to lose track of your schema state, which can lead to data loss or incorrect migrations.

---

## Reverse-engineer models

Generate SQLAlchemy models from a live database with round-trip support (PostgreSQL, MySQL, ClickHouse, SQLite):

```bash
dbwarden generate-models --database primary --tables users,posts
dbwarden generate-models --database primary --base app.database:Base
```

By default each generated file declares its own `Base = declarative_base()`. Use `--base` to import a custom Base class from your project instead (e.g. `--base app.database:Base` or `--base app.database:DeclarativeBase`). The generated output includes `class Meta` blocks with all detected backend-specific metadata.

---

## Supported databases

| Database   | Round-trip | Notes                                       |
|------------|------------|---------------------------------------------|
| PostgreSQL | Full       | Primary backend, full schema fidelity       |
| MySQL      | Full       | DDL parity focus                            |
| ClickHouse | Full       | Analytics backend, MergeTree engine family  |
| SQLite     | Dev only   | Local development and SQL translation       |
| MariaDB    | No         | Schema layer complete; snapshot gaps remain |

### PostgreSQL

First-class support with full round-trip schema fidelity. Supported features include identity and generated columns, partitioning, table inheritance, exclusion constraints, deferrable constraints, advanced indexes via `PgIndexSpec`, per-column storage and collation, enum type creation, and full type normalization (SERIAL, TIMESTAMPTZ, NUMERIC, JSONB, UUID, ARRAY, TSTZRANGE).

### MySQL

Full round-trip support with `MyTableMeta` / `MyColumnMeta` and `my.field()` spec objects. Engine-level options (`my_engine`, `my_charset`, `my_collate`, `my_row_format`), column-level options (`unsigned`, `charset`, `collate`, `on_update`), and model reverse-engineering via `generate-models`.

```bash
uv add "dbwarden[mysql]"
```

### ClickHouse

First-class analytics backend support. MergeTree engine family via `ChEngineSpec`, replicated engines, projections, dictionaries, materialized views, skip indexes via `ChIndexSpec`, column codecs, `LowCardinality` and `Nullable` type wrappers.

```bash
uv add "dbwarden[clickhouse]"
```

### MariaDB

Schema layer is complete with `MdbTableMeta` / `MdbColumnMeta` and `mdb.field()` spec objects including MariaDB-specific features (`page_compressed`, `invisible`, `without_overlaps`). Snapshot capture and reverse-engineering of MariaDB-specific features are not yet complete.

---

## Developer experience

**Dev mode**: Run SQLite locally against a PostgreSQL production schema with automatic SQL translation.

**Multi-database**: One project, multiple databases, full isolation between them. Use `model_tables` to assign table ownership per database when sharing model paths.

**Generate models**: Reverse-engineer a live database (PostgreSQL, MySQL, ClickHouse) into SQLAlchemy models with `dbwarden generate-models`.

**`dbwarden diff`**: Read-only comparison tool. Outputs as Rich table, JSON, or raw SQL. Supports `--offline` mode.

**Graceful disconnection**: Automatic retry logic and clear error messages when a database is unreachable.

---

## Plugins

DBWarden features a plugin system with three trust tiers (official, verified, community). Official plugins extend core with features that were previously built-in, now maintained independently:

| Plugin | PyPI | Purpose |
|---|---|---|
| `dbwarden-ch-rbac` | [`dbwarden-ch-rbac`](https://pypi.org/p/dbwarden-ch-rbac) | ClickHouse RBAC: roles, users, grants, row policies, quotas, settings profiles |
| `dbwarden-fastapi` | [`dbwarden-fastapi`](https://pypi.org/p/dbwarden-fastapi) | FastAPI session dependencies, health endpoints, migration routes |
| `dbwarden-pgsql-extensions` | [`dbwarden-pgsql-extensions`](https://pypi.org/p/dbwarden-pgsql-extensions) | PostgreSQL extensions, event triggers, functions, triggers, storage parameters |
| `dbwarden-pgsql-rbac` | [`dbwarden-pgsql-rbac`](https://pypi.org/p/dbwarden-pgsql-rbac) | PostgreSQL RBAC: roles, grants, default privileges, policies |
| `dbwarden-pgsql-types` | [`dbwarden-pgsql-types`](https://pypi.org/p/dbwarden-pgsql-types) | PostgreSQL custom types: ENUMs, domains, composite types, sequences |
| `dbwarden-sandbox` | [`dbwarden-sandbox`](https://pypi.org/p/dbwarden-sandbox) | Testcontainers sandbox providers for safe migration replay |
| `dbwarden-seeds` | [`dbwarden-seeds`](https://pypi.org/p/dbwarden-seeds) | Seed data management with code seeds and file-based SQL/Python seeds |

See the [plugin documentation](https://dbwarden.emiliano-go.com/plugins/) for installation, development guides, and the full Verified standard.

---

## Contributors

- [Emiliano Gandini Outeda](https://github.com/emiliano-gandini-outeda): creator and maintainer
- [Ahmet Cetin](https://github.com/ahmetcetin): optional-dependency guard (#47), logging overhaul (#50)

---

## License

MIT

---

DBWarden is built for teams that want declarative, reviewable, reproducible database changes, derived from the models they already maintain, not from migration scripts they have to write.
