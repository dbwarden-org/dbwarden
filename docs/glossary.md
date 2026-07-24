---
description: Key terms and concepts used throughout the DBWarden documentation, including
  migration lifecycle, database backends, safety tooling, FastAPI integration, and
  configuration.
seo:
  title: Glossary - DBWarden Documentation
  canonical: https://dbwarden.emiliano-go.com/glossary
  robots: index,follow
  og:
    type: website
    title: Glossary - DBWarden Documentation
    description: Key terms and concepts used throughout the DBWarden documentation,
      including migration lifecycle, database backends, safety tooling, FastAPI integration,
      and configuration.
    url: https://dbwarden.emiliano-go.com/glossary
    image: https://dbwarden.emiliano-go.com/assets/images/og-image.png
    image:width: 1376
    image:height: 768
    image:alt: DBWarden documentation
    site_name: DBWarden Documentation
    locale: en_US
  twitter:
    card: summary_large_image
    title: Glossary - DBWarden Documentation
    description: Key terms and concepts used throughout the DBWarden documentation,
      including migration lifecycle, database backends, safety tooling, FastAPI integration,
      and configuration.
    image: https://dbwarden.emiliano-go.com/assets/images/og-image.png
    image:alt: DBWarden documentation
    site: '@emiliano_go_'
  description: Key terms and concepts used throughout the DBWarden documentation,
    including migration lifecycle, database backends, safety tooling, FastAPI integration,
    and configuration.
  schema_jsonld:
  - '@context': https://schema.org
    '@type': WebPage
    name: Glossary - DBWarden Documentation
    url: https://dbwarden.emiliano-go.com/glossary
    description: Key terms and concepts used throughout the DBWarden documentation,
      including migration lifecycle, database backends, safety tooling, FastAPI integration,
      and configuration.
    image: https://dbwarden.emiliano-go.com/assets/images/og-image.png
    publisher:
      '@type': Organization
      name: Emiliano Gandini Outeda
      logo: https://dbwarden.emiliano-go.com/assets/images/og-image.png
  - '@context': https://schema.org
    '@type': BreadcrumbList
    itemListElement:
    - '@type': ListItem
      position: 1
      name: Glossary
      item: https://dbwarden.emiliano-go.com/glossary
seo_html: "<title>Glossary - DBWarden Documentation</title>\n<meta name=\"description\"\
  \ content=\"Key terms and concepts used throughout the DBWarden documentation, including\
  \ migration lifecycle, database backends, safety tooling, FastAPI integration, and\
  \ configuration.\">\n<link rel=\"canonical\" href=\"https://dbwarden.emiliano-go.com/glossary\"\
  >\n<meta name=\"robots\" content=\"index,follow\">\n<meta property=\"og:type\" content=\"\
  website\">\n<meta property=\"og:title\" content=\"Glossary - DBWarden Documentation\"\
  >\n<meta property=\"og:description\" content=\"Key terms and concepts used throughout\
  \ the DBWarden documentation, including migration lifecycle, database backends,\
  \ safety tooling, FastAPI integration, and configuration.\">\n<meta property=\"\
  og:url\" content=\"https://dbwarden.emiliano-go.com/glossary\">\n<meta property=\"\
  og:image\" content=\"https://dbwarden.emiliano-go.com/assets/images/og-image.png\"\
  >\n<meta property=\"og:image:width\" content=\"1376\">\n<meta property=\"og:image:height\"\
  \ content=\"768\">\n<meta property=\"og:image:alt\" content=\"DBWarden documentation\"\
  >\n<meta property=\"og:site_name\" content=\"DBWarden Documentation\">\n<meta property=\"\
  og:locale\" content=\"en_US\">\n<meta name=\"twitter:card\" content=\"summary_large_image\"\
  >\n<meta name=\"twitter:title\" content=\"Glossary - DBWarden Documentation\">\n\
  <meta name=\"twitter:description\" content=\"Key terms and concepts used throughout\
  \ the DBWarden documentation, including migration lifecycle, database backends,\
  \ safety tooling, FastAPI integration, and configuration.\">\n<meta name=\"twitter:image\"\
  \ content=\"https://dbwarden.emiliano-go.com/assets/images/og-image.png\">\n<meta\
  \ name=\"twitter:image:alt\" content=\"DBWarden documentation\">\n<meta name=\"\
  twitter:site\" content=\"@emiliano_go_\">\n<script type=\"application/ld+json\"\
  >\n[\n  {\n    \"@context\": \"https://schema.org\",\n    \"@type\": \"WebPage\"\
  ,\n    \"name\": \"Glossary - DBWarden Documentation\",\n    \"url\": \"https://dbwarden.emiliano-go.com/glossary\"\
  ,\n    \"description\": \"Key terms and concepts used throughout the DBWarden documentation,\
  \ including migration lifecycle, database backends, safety tooling, FastAPI integration,\
  \ and configuration.\",\n    \"image\": \"https://dbwarden.emiliano-go.com/assets/images/og-image.png\"\
  ,\n    \"publisher\": {\n      \"@type\": \"Organization\",\n      \"name\": \"\
  Emiliano Gandini Outeda\",\n      \"logo\": \"https://dbwarden.emiliano-go.com/assets/images/og-image.png\"\
  \n    }\n  },\n  {\n    \"@context\": \"https://schema.org\",\n    \"@type\": \"\
  BreadcrumbList\",\n    \"itemListElement\": [\n      {\n        \"@type\": \"ListItem\"\
  ,\n        \"position\": 1,\n        \"name\": \"Glossary\",\n        \"item\":\
  \ \"https://dbwarden.emiliano-go.com/glossary\"\n      }\n    ]\n  }\n]\n</script>\n"
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
