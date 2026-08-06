---
description: Key terms and concepts used throughout the DBWarden documentation, including
  migration lifecycle, database backends, safety tooling, FastAPI integration, and configuration.
---

# Glossary

## A

**Auto Schema**
: A feature that generates Pydantic schemas from SQLAlchemy model annotations using `@auto_schema` (from the `dbwarden-fastapi` plugin), eliminating duplication between ORM and API layers in FastAPI applications.

## B

**Backend**
: A supported database type (PostgreSQL, MySQL, MariaDB, SQLite, ClickHouse). Each backend has specific DDL syntax, feature support, and round-trip capability.

**Baseline**
: A migration version used as a starting point, marking the point up to which existing migrations are considered already applied without executing the upgrade SQL.

## C

**Checksum**
: A SHA-256 hash stored when a migration file is applied. On subsequent runs, the checksum is recalculated to detect file tampering or accidental edits.

**Check (command)**
: Analyzes schema differences between SQLAlchemy models and a live database, classifying every operation by danger level.

**Code Seed**
: A seed defined as a Python class extending `Seed`, with `run()` and optional `rollback()` methods. The recommended way to manage seed data.

**Configuration (dbwarden.py)**
: DBWarden uses a Python file (`dbwarden.py`) with `database_config()` calls, providing type safety, runtime flexibility, and IDE support for configuring databases.

## D

**Database Config**
: A single `database_config()` call that defines one database. Each config includes the database name, type, connection URL, model paths, and optional dev mode settings.

**Dev Mode**
: Using a different database type (typically SQLite) for local development while targeting a production database (e.g., PostgreSQL) in deployment, enabled via the `dev_database_type` and `dev_database_url` config options.

**Diff**
: A read-only command showing structural differences between SQLAlchemy models and a live database, with table, json, and sql output formats.

**Downgrade**
: Revert applied migrations to reach a specific target version by reading `-- rollback` sections and applying them in reverse order.

## F

**FastAPI Integration**
: DBWarden's built-in support for FastAPI including async sessions, health endpoints, migration endpoints, Prometheus metrics, and distributed locking. Configured once via `database_config()`.

## G

**generate-models**
: A command that reverse-engineers SQLAlchemy model code from a live database, supporting all backends.

## H

**Health Endpoints**
: Production-ready HTTP endpoints for database connectivity checks, migration state monitoring, and Kubernetes liveness/readiness probes.

## I

**Impact Analysis**
: The ability to find affected Python code references (function calls, class references, variable names) before deploying a migration, via the `check-impact` command.

**Index Spec**
: A class (`IndexSpec`) used in model `Meta` to define database indexes, including columns, uniqueness, and types.

## L

**Lock (Migration Lock)**
: A database-level lock that prevents concurrent schema mutations across multiple application instances or CLI invocations.

## M

**make-migrations**
: The command that generates SQL migration files by comparing current SQLAlchemy model definitions against either a live database or stored schema snapshots.

**Manual Migration**
: A migration file created by hand (via `dbwarden new`) rather than auto-generated. Useful for data migrations, stored procedures, or any DDL outside model diffs.

**Meta (class Meta)**
: An inner class on SQLAlchemy models that provides DBWarden with backend-specific metadata like table comments, indexes, partitioning, engine options, and more.

**Migration File**
: A plain SQL file with `-- upgrade` and `-- rollback` sections. Each file represents one atomic schema change.

**Multi-Database**
: Managing multiple database configurations (e.g., primary + analytics, or microservice per database) from a single `dbwarden.py` config file.

## O

**Observability**
: DBWarden's monitoring capabilities including Prometheus metrics (migration counters, schema version gauges, connection pool health) and structured JSON logging.

**Offline Mode**
: Generating migrations using stored JSON schema snapshots instead of connecting to a live database, enabling CI/CD pipelines without database access.

## P

**Pydantic Schema (auto-generated)**
: Request/response schemas automatically generated from SQLAlchemy model annotations using `@auto_schema` (from the `dbwarden-fastapi` plugin), keeping API contracts in sync with database models.

## R

**Rename Detection**
: DBWarden can detect column and table renames by comparing schema snapshots, generating `ALTER TABLE ... RENAME` instead of `DROP` + `ADD`.

**Rollback**
: The `-- rollback` section of a migration file containing SQL to undo the upgrade. DBWarden enforces that every migration has a corresponding rollback.

**Round-Trip**
: A backend that supports both reading schema (via `generate-models`) and writing schema (via `make-migrations`/`migrate`). Verified when reverse-engineering a database and re-generating produces zero diff.

**RA (Runs-Always) Migration**
: A migration type that runs every time `migrate` is executed, regardless of previous application. Useful for idempotent operations like views or functions.

**ROC (Runs-on-Change) Migration**
: A migration type that runs only when its content has changed (detected via checksum). Useful for stored procedures that evolve over time.

## S

**Safety Check**
: A feature that classifies every migration operation into danger levels (safe, caution, danger, manual) so teams can review high-risk changes before production.

**Sandbox**
: An isolated test environment using Testcontainers to validate migrations against a real database before applying them to production or staging.

**Schema Snapshot**
: A JSON file recording the full DDL state of a database at the point a migration was applied. Enables offline migration generation, rename detection, and CI workflows.

**Seed**
: Data population mechanism using either Python code seeds (recommended) or file-based SQL/Python seeds. Seeds are tracked and versioned like migrations.

**SQL-First**
: A design philosophy where all schema changes are expressed as explicit SQL files that can be reviewed, tested, and rolled back, rather than being abstracted away by an ORM.

**SQL Translation**
: The ability to generate SQL for one dialect (e.g., PostgreSQL) while operating against a different development database (e.g., SQLite), enabling local development without full infrastructure.

## T

**TableMeta**
: The base class for model `Meta` inner classes, providing type-safe configuration of table-level metadata like comments, indexes, partitioning, and engine options.

## U

**Unlock**
: The `dbwarden unlock` command to recover from a stale migration lock when no migration is actually running and the lock was not released properly.
