# Cookbook & Examples

Practical, runnable examples that walk through the entire DBWarden workflow: from project setup through advanced observability patterns.

## How to Use

Each cookbook section links to code under the [`examples/`](https://github.com/emiliano-gandini-outeda/DBWarden/tree/main/examples) directory. The **core examples** (sections 1-7) use SQLite and require only `uv add dbwarden`. Advanced examples may need Docker for PostgreSQL, ClickHouse, or Prometheus.

```
examples/
├── core/                 # Sections 1-7: progressive SQL workflow
├── multi-database/       # Section 8
├── fastapi-app/          # Section 9
├── fastapi-app/          # Sections 9-10 (FastAPI + auto-schema examples)
└── observability/        # Section 11
```

## Sections

| # | Section | What You'll Learn | Example Dir |
|---|---------|-------------------|-------------|
| 1 | [Project Setup](01-project-setup.md) | `init`, `config`, understanding `database_config()` | `examples/core/` |
| 2 | [Models & Migrations](02-models-and-migrations.md) | Model definitions, `make-migrations`, `new`, `make-rollback` | `examples/core/` |
| 3 | [Apply & Inspect](03-apply-and-inspect.md) | `migrate`, `rollback`, `downgrade`, `history`, `status`, `check`, `check-db` | `examples/core/` |
| 4 | [Offline & CI](04-offline-ci.md) | `export-models`, `make-migrations --offline` | `examples/core/` |
| 5 | [Schema Inspection](05-schema-inspection.md) | `diff`, `snapshot`, `generate-models` | `examples/core/` |
| 6 | [Safety & Impact](06-safety-impact.md) | `check`, `check-impact`, destructive change detection | `examples/core/` |
| 7 | [Seeds](07-seeds.md) | `seed create/apply/rollback/list`, SQL seeds, `@seed_data` | `examples/core/` |
| 8 | [Multi-Database](08-multi-database.md) | Multiple `database_config()`, PG + ClickHouse, `--all` flag | `examples/multi-database/` |
| 9 | [FastAPI Integration](09-fastapi-integration.md) | Lifespan hooks, health endpoints, session DI, migration endpoints | `examples/fastapi-app/` |
| 10 | [Auto Schemas](10-auto-schemas.md) | `@auto_schema`, `CreateSchema`, `UpdateSchema`, `PublicSchema` (requires `dbwarden-fastapi` plugin) | `examples/fastapi-app/` |
| 11 | [Observability](11-observability.md) | Prometheus metrics, structured logging, query tracing | `examples/observability/` |

## Quick Start (Core)

```bash
cd examples/core
uv add dbwarden sqlalchemy
bash scripts/01-setup.sh
bash scripts/02-models-migrations.sh
bash scripts/03-apply-inspect.sh
```

Each section in the cookbook explains what these commands do, what SQL they produce, and why it matters.

## Database-Specific Examples

The core examples use SQLite for zero-dependency setup. For production, DBWarden fully supports PostgreSQL, MySQL, and ClickHouse; each with its own deep-dive guide and dedicated example patterns.

### PostgreSQL

PostgreSQL is a first-class backend with full round-trip support (read and write schema). The FastAPI integration example in [Section 9](09-fastapi-integration.md) uses PostgreSQL, and [Section 8](08-multi-database.md) shows PostgreSQL + ClickHouse together.

For the complete reference on PostgreSQL-specific metadata (identity columns, collation, compression, generated columns, tablespace, inheritance, exclusion constraints, deferrable FKs, advanced index options), see the [PostgreSQL Deep Dive](../databases/postgresql/index.md).

```python
from dbwarden import database_config

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:password@localhost:5432/myapp",
    database_url_async="postgresql+asyncpg://user:password@localhost:5432/myapp",
    model_paths=["app.models"],
)
```

### MySQL / MariaDB

MySQL and MariaDB are first-class backends with full round-trip support. All MySQL-specific metadata (engine, charset, collation, row format, auto_increment, unsigned columns, ON UPDATE, column comments) is captured by the snapshot, diffed correctly, and emitted as valid DDL.

See the [MySQL Deep Dive](../databases/mysql.md) for the complete reference, including MySQL-specific model metadata via `class Meta(MyTableMeta)`.

```python
from dbwarden import database_config

legacy = database_config(
    database_name="legacy",
    database_type="mysql",
    database_url_sync="mysql+pymysql://user:password@localhost:3306/legacy",
    model_paths=["app.legacy_models"],
)
```

### ClickHouse

ClickHouse is supported with partial round-trip (read schema and auto-generate most DDL). DBWarden uses the ClickHouse HTTP client directly for DDL execution and supports full engine metadata via `class Meta(CHTableMeta)` with `ChEngineSpec`, `ProjectionSpec`, and `CHColumnMeta`.

See the [ClickHouse Deep Dive](../databases/clickhouse/index.md) for full details on materialized views, projections, dictionaries, replicated engines, and ClickHouse-specific metadata.

ClickHouse is typically configured alongside a transactional database (see [Section 8](08-multi-database.md) for a PostgreSQL + ClickHouse example).
