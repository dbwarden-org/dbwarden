---
title: Changelog
description: Release notes for dbwarden, newest first. Tracks features, fixes, and breaking changes across all published versions.
---

# Changelog

All notable changes to dbwarden, newest first. Versions follow semantic versioning and are tagged in the repository.

## [Unreleased]

### Changed

- **Declarative configuration parity.** `DbwardenDatabase` now supports the complete `database_config(...)` field and default surface, inherited and direct plugin configuration, equivalent handles and validation, and aliased or indirect class discovery.
- **Generated files use atomic replacement.** Migration files, model state, generated models, rollback files, plugin state, and exported configuration now avoid leaving truncated files after an interrupted write.
- **Database settings mask URLs through SQLAlchemy URL parsing.** Settings and config output now handle encoded credentials and IPv6 hosts without exposing passwords.

### Fixed

- **Unsafe SQL identifiers are quoted or rejected.** Backend-generated schema, table, column, statistics, and drop-object SQL now protects reserved words and embedded identifier quotes.
- **Live command disconnects no longer report success.** Explicit `check` and `snapshot` operations propagate connection failures after retries.
- **Configuration and impact scans reject symlink escapes.** Workspace discovery no longer imports or scans symlinked files and directories outside the intended root.
- **Plugin provenance is HTTPS-only and size-bounded.** Plugin lock and consent TOML serialization escapes structural characters, and installer/provenance inputs are validated.
- **Migration SQL splitting respects quoted strings and comments.** Semicolons inside SQL literals and comments no longer split statements incorrectly.

## [0.17.0] - 2026-08-13

### Added

- **Global `--json` flag.** `dbwarden --json <command>` switches display commands (`status`, `history`, `database list`, `settings show`, `config`, `version`, `lock-status`) to structured JSON output and also routes the command's log output through JSON formatting (the same effect as `DBWARDEN_LOG_JSON=true`). Commands that already support JSON output (`check`, `check-db`, `check-impact`, `diff`, `plugin list`, `plugin info`) honor the global flag too.
- **TRACE log level.** A `trace` level (numeric `5`) is available via `--debug-level trace` and logs per-statement SQL during `migrate`, `rollback`, and `downgrade`.
- **`--perf` flag for migration commands.** `migrate`, `rollback`, `downgrade`, and `make-migrations` accept `--perf` to add per-SQL-statement timing breakdowns on top of the always-on phase timings (lock acquisition, snapshot write, model state write, and SQL statement durations).
- **Optional database availability handling.** Database entries support `skip_if_missing=True` for optional databases in multi-database operations. Use the global `--disable-skip` flag to force configured optional databases to fail normally.
- **Declarative database configuration.** Concrete subclasses of `DbwardenDatabase` are automatically registered and support inherited configuration, while the existing `database_config(...)` API remains supported.
- **Partial-success results.** Multi-database migration, status, and seed commands report skipped databases and use exit code `3` when the operation completes with optional databases unavailable.

### Fixed

- **ClickHouse DDL generation for fresh migrations.** `make-migrations` now emits ClickHouse DDL that applies cleanly to a fresh ClickHouse 24.x database: column comments are emitted before `CODEC(...)`, `Nullable` is nested inside `LowCardinality(...)` rather than the invalid reverse, and columns used in `ORDER BY` / `PRIMARY KEY` / `PARTITION BY` / `SAMPLE BY` are rendered as non-nullable.
- **Plugin list table rendering.** The `plugin list` table no longer wraps or truncates plugin distribution names in narrow terminals.
- **Rollback warning test stability.** The irreversible-rollback warning test now asserts against the log record instead of Rich console wrapping.

## [0.16.5] - 2026-08-05

### Added

- **Plugin-declared configuration keys.** Plugins can now register their own config keys and have them surface through the standard configuration path, so an installed plugin ships its settings surface instead of relying on loose user-side keys.

### Changed

- **`settings` output masks secret values.** The `settings` command now redacts values it recognises as credentials, so dumping configuration for a support ticket no longer prints a database password or API token.

### Fixed

- **MySQL diff for newly created tables.** A newly added table in a MySQL database no longer produces an empty or broken diff; the new-table path now emits the full create statement.
- **Publish and CI workflows hardened.** Release publishing and the CI pipeline were tightened to fail fast on the conditions that previously produced partial artifacts.

## [0.16.4] - 2026-08-05

### Added

- **`--debug` and `--debug-level` CLI flags.** Both flags enable per-file scan logging, so a slow or misbehaving `make-migrations` run can be traced down to the individual model file that caused it.

### Fixed

- **Config cache miss no longer rescans the whole workspace.** A config cache miss previously triggered a repeated full-workspace rescan; the cache now reloads in place.
- **Offline rollback no longer double-reverses.** The offline rollback path was reversing the same operation twice; the reversal is now applied exactly once, and agg-target key types resolve through the cascade chain.

## [0.16.3] - 2026-08-03

### Fixed

- **Materialized view drops use `DROP_VIEW` ordering.** Drops for cascading materialized views are emitted in `DROP_VIEW` order, matching how the views depend on one another.
- **`ch_raw` group-by column types resolve through the cascade chain.** A `ch_raw` view whose group-by keys come from an upstream view now resolves the types transitively instead of falling back to an unresolved state.

## [0.16.2] - 2026-07-29

### Fixed

- **Cascade materialized views resolve string forward references.** Cascading MVs that reference an upstream view by a forward string now resolve through the registry rather than failing at snapshot time.
- **Group-by key types fixed in cascading views.**
- **`ch_meta.ch_type` populated regardless of backend.** The `ch_type` metadata is now filled in even when the diff is running under a non-ClickHouse backend.
- **Config fallback now warns.** Falling back to a default backend or schema when a config value is missing now emits a warning instead of silently proceeding.

## [0.16.1] - 2026-07-28

### Added

- **Container test for cascade combinator correctness.** A live-ClickHouse test now verifies that cascading aggregate views emit the correct combinator.

### Changed

- **Cascading aggregate views use the `MergeState` combinator.** Cascades no longer emit `Sum` for upstream aggregate state; they use `MergeState` so the aggregate survives the cascade correctly.
- **`schemap` and `@auto_schema` moved to the `dbwarden-fastapi` plugin.** The schema-map integration is no longer part of core; install `dbwarden-fastapi` to keep using `@auto_schema`.

### Fixed

- **Three agg-target DDL bugs.** Aggregation target handling in the DDL layer emitted incorrect SQL in three edge cases.

## [0.16.0] - 2026-07-24

### Added

- **Plugin system with trust tiers, consent, and provenance verification.** dbwarden now discovers plugins through the `dbwarden.plugins` entry point group and classifies each distribution into official, verified, or community tiers. Plugin code from unverified sources is not imported until the operator explicitly consents to that exact version. See the [Plugins](plugins/) section for the trust model.
- **`recover-model-state` command.** Model state is now stored in the database as well as on disk, and `dbwarden recover-model-state` restores it when the on-disk file is lost or corrupt.
- **GPG-signed commit requirement.** Contribution guidelines now require GPG-signed commits.

### Changed

- **Remaining sandbox and five PostgreSQL extension handlers extracted to plugins.** Core no longer ships the sandbox provider or the PostgreSQL extensions, event triggers, functions, triggers, and storage-parameter handlers; they live in `dbwarden-sandbox` and `dbwarden-pgsql-extensions`.
- **Plugin-duplicated code stripped from core.** Every handler that now lives in a plugin was removed from core so there is a single implementation.
- **Normalized live snapshot diff.** Live-database snapshots now diff against the same normalized representation as stored snapshots.
- **PostgreSQL round-trip diff stabilized.**

### Fixed

- **Generated model metadata preserved.** Reverse-engineered models keep their detected backend metadata instead of dropping it during regeneration.

## [0.15.0] - 2026-07-21

### Added

- **Strict rollback contracts.** Every generated migration now has a declared rollback contract: executable rollback when it is safe, placeholder refusal by default, and an explicit irreversible declaration when rollback cannot be produced. See [Rollback Generation](correctness/rollback-generation.md).
- **Irreversible rollback annotations.** An operator can annotate a migration as irreversible, which dbwarden records and respects.
- **Rollback round-trip integration harness.** A test harness applies upgrade and rollback in sequence and verifies the schema lands back where it started. See [Round Trip Verification](correctness/round-trip-verification.md).
- **ObjectHandler protocol documented.**

### Changed

- **Rollback metadata preserved across diff pipelines.** Rollback information survives the snapshot, diff, and emission stages instead of being regenerated per stage.
- **Rollback state restored for ClickHouse and PostgreSQL.** RBAC, collection, profile, and policy rollback are restored for ClickHouse; PostgreSQL rollback state is restored as well.

## [0.14.3] - 2026-07-21

### Added

- **`recover-model-state` command.** Restores model state from the database when the on-disk state is missing or stale.

## [0.14.2] - 2026-07-21

### Fixed

- **`clickhouse_options` normalized to `ch_options`.** The snapshot pipeline now uses a single dict key, `ch_options`, so diffs no longer churn on naming alone.
- **TTL expression query conditional for ClickHouse before 24.4.** The `ttl_expression` query only runs against versions that expose the column, preventing failures on older servers.
- **Dead `ChRbacHandler` removed.**
- **Mobile sidebar drawer fixed.** The Zensical modern theme now uses a class-based drawer instead of `:has()`, and the viewport meta tag is present.

## [0.14.1] - 2026-07-20

### Changed

- **Docs aligned to the API.** Reference pages now match the exported surface, including the missing aggregation methods and RBAC class exports.

## [0.14.0] - 2026-07-20

### Changed

- **Core refactored to a single registry-driven protocol.** The twin registries were collapsed into one, the five backends were extracted into `dbwarden/databases/`, and every handler now speaks one protocol with `**kwargs` propagation and a `cluster_ctx` on the driver.
- **Config, commands, and FastAPI extracted into packages.** `config/`, `commands/`, and the FastAPI extension now live as packages rather than modules, which is the groundwork for the plugin system.
- **ClickHouse view API rewritten.** `ChView`, `MaterializedView`, and `AggregatingView` are now registry-backed non-SQLAlchemy base classes, with builder signatures (`to` instead of `to_table`), `_resolve_source`, and `AggregatingViewSpec` for correctness.
- **ClickHouse RBAC, named collections, and data operations.** New handlers and spec classes cover roles, users, grants, policies, quotas, settings profiles, named collections, partition operations, mutations, and `OPTIMIZE` via a `DataOp` type.
- **`render_expr()` and compiled-expression acceptance.** Expression sites now accept SQLAlchemy `ColumnElement` objects and render them through `render_expr()`.

### Added

- **`p0` and `integration` pytest markers.** `p0` marks the two-cycle convergence gate that must never regress; `integration` marks tests that need a live ClickHouse container.
- **Convergence-audit and ClickHouse integration CI jobs.**
- **ClickHouse documentation split into a multi-page reference** with dedicated pages for RBAC, views, dictionaries, and data operations.

## [0.13.0] - 2026-07-06

### Added

- **PyPI publishing workflow.** A `v*` tag now publishes the package to PyPI automatically.
- **SEO integration via seoslug 2.0.1.** The docs site generates canonical URLs, OG images, and schema-aware frontmatter through the inline Zensical extension.
- **Favicon, OG image, and robots.txt content-signal directives.**

### Fixed

- **Nine SQL generation bugs resolved** across the PostgreSQL expansion.
- **Schema package re-exports removed** so imports resolve against the single canonical path.

## [0.12.5] - 2026-07-01

### Changed

- **README version badge updated** and the publishing workflow prepared for the PyPI release.

## [0.12.4] - 2026-06-24

### Changed

- **`_write_model_state` skipped when no migrations applied.** Startup no longer burns CPU rewriting model state that did not change; this removes a multi-minute stall on every startup with nothing pending.

### Fixed

- **MySQL metadata preserved in generated models.**
- **Config and model caches refreshed** when the underlying files change.
- **Docs lint issues fixed.**

## [0.12.3] - 2026-06-23

### Fixed

- **`make-migrations` output deduplicated.**
- **MySQL DDL savepoint destruction handled** so a failed statement does not invalidate the transaction.
- **Primary key inferred for tables missing one**, and MySQL DDL translation improved.
- **Generated SQLite databases ignored** by version control.

## [0.12.1] - 2026-06-17

### Added

- **`--base` flag documented.** `dbwarden generate-models --base app.database:Base` imports a project base instead of declaring a new one.
- **Graceful database disconnection.** Connection failures now trigger retry logic and a clear error instead of a bare traceback.

### Changed

- **Schema backends moved to `dbwarden/databases/`.**
- **Safe SQL quoting and config cache fixes.** Duplicate model loads are avoided and default serialization is stable.

### Fixed

- **Live snapshot taken when no cached snapshot exists** for `make-migrations`.
- **Unique module name computed per file** in the sandbox loader, so two files with the same basename no longer collide.

## [0.12.0] - 2026-06-13

### Added

- **MySQL round-trip support.** MySQL schema classes, model discovery, a round-trip engine, and comprehensive test coverage, alongside the MySQL dependency group. See [MySQL](databases/mysql.md).
- **Seed export command.** `dbwarden export` writes code seeds to ROC SQL files.

### Changed

- **ClickHouse `ForeignKey` prohibited.** A foreign key on a ClickHouse table is now an error rather than silently unsupported SQL.
- **Snapshots moved to `.dbwarden/`**, and model state is synced online.
- **Auto-generated SQL for previously manual operations.** ClickHouse rename, nullable, `LowCardinality`, and projection changes, plus the PostgreSQL `USING` clause, are now emitted automatically.
- **Generated models emit `pg.field()` and `ch.field()` spec objects** instead of flat backend attributes.

## [0.11.2] - 2026-06-12

### Added

- **In-code seed engine.** `@seed_data`, `SeedRow`, and `DBWardenSeed` define seeds in Python; a tracking table records what has been applied.
- **`auto_apply_seeds` config** wired into `migrate`.
- **Checksum drift detection and seed pruning.**

### Fixed

- **Offline migration first run generates SQL for all tables**, not an empty set.
- **Seed types moved to the `dbwarden.seed` module** so the CLI and engine share one definition.

## [0.11.0] - 2026-06-11

### Added

- **Typed Meta system.** A `_MetaValidator` metaclass validates every attribute on `class Meta` at import time; a typo now raises `DBWardenConfigError` instead of silently producing wrong DDL. Per-backend field factories produce spec objects.
- **Offline v2 state format.** ClickHouse engine recreate and column rename flags are captured in the offline state.

### Changed

- **`requires-python` relaxed to `>=3.12`** and the upper bound removed, so Python 3.14 is supported.

## [0.10.2] - 2026-06-11

### Added

- **`model_tables` per-database filter** with overlap validation, so one model file set can be partitioned across databases with ownership checks.

## [0.10.1] - 2026-06-11

### Fixed

- **Config sandbox classification for in-package and `src/` layout projects.** Model files discovered inside the package directory or under `src/` are now classified correctly.

## [0.10.0] - 2026-06-09

### Added

- **PostgreSQL auto-increment lifecycle support.** Serial, identity, and sequence lifecycle are handled end to end, including rename and rollback.
- **`--type` flag for repeatable migrations.**

### Changed

- **Real `diff` implemented**, replacing the placeholder, and obsolete CLI commands removed.
- **Lock system overhauled.** Migration locking, connection safety, and sandbox fixes landed as a mass bug-fix pass (38+ fixes).

### Fixed

- **`alter_column_type` rollback and `drop_table` placeholder fixed**, with the full rollback cycle verified.
- **Rollback SQL for ClickHouse indexes and foreign key constraints fixed**, and the `make-rollback` regex corrected.
- **System tables excluded from diffs** and rollback counts corrected.
- **Offline migration engine fixed** for compatible operations and a wrong import, with 35 comprehensive tests; 26 edge-case tests added, and the crash on corrupted state resolved.

## [0.9.5] - 2026-06-09

### Added

- **`--type` flag for repeatable migrations.**

### Changed

- **Documentation updated** for the new flag and related CLI changes.

## [0.9.4] - 2026-06-09

### Added

- **Cookbook docs, examples, and an integration test suite.**

### Fixed

- **Six bugs fixed.**
- **Missing import in `rollback.py` fixed** (`get_database`).
- **Comments added to every example.**

## [0.9.0] - 2026-06-09

### Fixed

- **Offline migration engine fixed** for compatible operations and the wrong import path, with 35 comprehensive tests.

### Changed

- **Docs restructured.** Index and introduction merged, models/modeling split into separate references, squashing folded into the squash page, and the navigation rebuilt.

## [0.8.0] - 2026-04-26 through 2026-06-09

This window aggregated the 0.8 and 0.9 development lines into core; the version was not bumped per feature.

### Added

- **Schema snapshots for rename detection.** A checksummed JSON snapshot is written after every migration and powers rename detection without querying the live database. See [Deterministic Diff](correctness/deterministic-diff.md).
- **Column-level diff with `StatementOrder` and `--safe-type-change`.** Type, nullability, default, and comment changes produce precise `ALTER COLUMN` statements, and type changes require the flag.
- **Table and column rename detection** with an interactive prompt and a rename flag.
- **Richer index metadata.** Partial and covering indexes, `USING` access methods, `WITH` storage parameters, `WHERE`, `TABLESPACE`, `NULLS NOT DISTINCT`, per-column sort order, and ClickHouse skip indexes, plus a `--concurrent` flag.
- **`DatabaseHandle`** returned by `database_config()`, with `database_url_sync` and `database_url_async` split into separate settings and a unified async engine dispatcher.
- **Sandbox providers and `--dry-run`.** `dbwarden migrate --dry-run --sandbox` replays migrations against SQLite or a testcontainers provider.
- **ClickHouse materialized views, projections, and a safety analyzer**, wired into the new `check` command.
- **ClickHouse replicated engines and external dictionaries.**
- **Versioned seed management** with CLI commands and a configurable tracking table.
- **Prometheus metrics and JSON logging**, plus FastAPI Redis lock, status, and migrate endpoints and a `dbwarden_lifespan` context manager. See [Observability](observability.md).
- **Migration impact analysis** with the `check-impact` command, using AST analysis with a grep fallback.
- **Offline migrations.** `export-models` dumps model state; `make-migrations --offline` generates SQL with no database connection. See [Offline Integrity](correctness/offline-integrity.md).
- **In-code seed definitions** (`@seed_data`, `SeedRow`, `DBWardenSeed`).
- **Class Meta metadata foundation**, with `IndexSpec`, `CheckSpec`, `UniqueSpec` dataclasses and factory functions.
- **PostgreSQL first-class support with zero-diff round-trip**, including unlogged tables, `no_inherit`, deferred uniques, `tsvector`, `enum ADD VALUE`, partitioning, and the typed `@auto_schema` wrapper.
- **ClickHouse first-class Meta** with `ChEngineSpec`, `ChIndexSpec`, and spec serialization.

## [0.7] - 2026-04-26

### Added

- **Auto-generated migration names** when no description is provided, with descriptive names derived from the change.
- **Migration plan output** and configurable migration tracking tables.
- **Health router hardening.** Authentication via the `DBWARDEN_HEALTH_AUTH` environment variable, sanitized error responses, and validation of identifiers and paths against strict patterns.

### Changed

- **Migrations now use savepoints.** Any statement failure rolls back all statements in the migration.
- **Atomic writes with backups.** Config and snapshot writes are atomic and validated before replace, with the backup directory checked first.

### Fixed

- **No spurious backup on initial `init`.**
- **`RestrictedFileLoader` added** so sandboxed config loads cannot escape the project root.

## [0.6] - 2026-04-24

### Added

- **FastAPI integration.** Session dependencies, health router, and startup helpers.
- **`settings` command group and mutators.**
- **Rich colorized CLI output** with command accents.

### Changed

- **Python-based `database_config` runtime** replaced the TOML-only config surface.
- **Docs restructured** into Getting Started, Tutorial, Advanced, and Reference tiers.

## [0.5] - 2026-04-23

### Changed

- **Documentation updates** across the site.

## [0.4] - 2026-04-23

### Added

- **SQLite translation layer with strict mode support.** PostgreSQL-flavored models run against SQLite locally, with a `--strict` flag enforcing translation correctness.
- **Global `--dev` mode** and database URL and target uniqueness validation.

## [0.3.6] - 2026-04-14

### Changed

- **Checksum-based migration tracking.** Migrations are checked by checksum instead of version, so deployments are idempotent.
- **Migrations table keyed by filename** (auto-increment id removed) with a UNIQUE constraint on the version column for PostgreSQL `ON CONFLICT` support.
- **ClickHouse-specific SQL queries** for migration tracking.

## [0.3.2] - 2026-04-10

### Fixed

- **Migration filename uses the config section name** instead of the URL.
- **Logging and type mapping improved** across the command layer.

## [0.3.1] - 2026-04-10

### Fixed

- **Logging and type mapping improved** across the command layer.

## [0.3.0] - 2026-04-10

### Added

- **ClickHouse support.** URL conversion and the `clickhouse-connect` dependency.
- **Multi-database support.** An explicit `database_type` field, a multi-database config structure, `--database` and `--all` CLI options, and a `database` management command. See [Multi-Database](configuration/multi-database.md).

### Changed

- **Backend-specific internal migration SQL** for PostgreSQL, MySQL, and SQLite.

### Fixed

- **Duplicate migrations fixed.**

## [0.2.0] - 2026-03-04

### Changed

- **Icon and banner added**, and tests and docs updated for the rebrand.

## [0.1.5] - 2026-02-27

### Added

- **ALTER TABLE support.**

### Changed

- **`.env` alternatives and async mode removed** in favour of a single sync path.
- **Diffs compare against the database schema** instead of the migrations table.

### Fixed

- **Async mode detection and duplicate connection logs** in the connection layer.

## [0.1.3] - 2026-02-16

### Added

- **`warden.toml` documented.**

## [0.1.2] - 2026-02-10

### Added

- **Issue templates and CONTRIBUTING.md.**

### Changed

- **Config moved from `.env` to `warden.toml`.**

### Fixed

- **`CallableColumnDefault` handled** so SQLite no longer emits syntax errors for callable defaults.

## [0.1.1] - 2026-02-08

### Added

- **Baseline migrations** and automatic backup before migration.
- **Migration dependency support.**
- **Colored output with SQL syntax highlighting.**

### Changed

- **Checksum-based deduplication** for `RA__` and `ROC__` migrations.
- **Auto-discovery scans all subdirectories** for model files.

### Fixed

- **"Table already exists" errors fixed** by adding `IF NOT EXISTS` to `CREATE TABLE` statements.
- **Migration deduplication and pending-migration detection fixed.**
