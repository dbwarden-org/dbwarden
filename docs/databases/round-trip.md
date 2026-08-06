---
description: A round-trip backend is one where DBWarden can both read schema (via generate-models)
  and write schema (via make-migrations/migrate). Covers PostgreSQL, MySQL, ClickHouse, SQLite,
  and MariaDB.
---

# Round Trip Support

A **round-trip** backend is one where DBWarden can both read schema (via `generate-models`) and write schema (via `make-migrations` / `migrate`).

## Supported Backends

| Backend | `database_type` | Round-Trip |
|---------|------------------|------------|
| PostgreSQL | `postgresql` | Yes |
| MySQL | `mysql` | Yes |
| ClickHouse | `clickhouse` | Yes |
| SQLite | `sqlite` | Dev only |
| MariaDB | `mariadb` | No |

## How Round-Trip Verification Works

"First-class" means the round-trip is verified: reverse-engineer a live database with `generate-models`, feed the output back into `make-migrations`, and get **zero diff**.

## Per-Backend Details

### PostgreSQL

PostgreSQL is a **first-class backend** with full round-trip support. All metadata (identity columns, collation, storage, compression, generated columns, fillfactor, tablespace, inheritance, exclude constraints, deferrable foreign keys, and advanced index options) is captured by the snapshot, diffed correctly, and emitted as valid DDL.

See [PostgreSQL Deep Dive](postgresql/index.md) for the complete list of supported features.

### MySQL

MySQL is a **first-class backend** with full round-trip support. All metadata (engine, charset, collation, row format, auto_increment, unsigned columns, `ON UPDATE`, and column comments) is captured by the snapshot, diffed correctly, and emitted as valid DDL.

See [MySQL Deep Dive](mysql.md) for the complete list of supported features.

### ClickHouse

ClickHouse has full round-trip support: `generate-models` reads schema from a live ClickHouse server, and `make-migrations` / `migrate` auto-generates DDL for table operations.

See [ClickHouse Deep Dive](clickhouse/index.md) for the complete list of supported features.

### SQLite

SQLite is supported for **development workflows only** (`--dev` flag). It uses the same snapshot format as PostgreSQL but with SQLite-compatible DDL. SQLite is ideal for local iteration before running migrations against production.

### MariaDB

MariaDB is supported as a separate `database_type` (`mariadb`), but it does **not** have round-trip support. You can use MariaDB as a target database for migrations, but `generate-models` and full schema introspection are not available. Use `make-migrations` to write migrations manually.
