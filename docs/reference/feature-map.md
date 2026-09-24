# Feature map

Use this page to choose a workflow. The [CLI option inventory](cli-options.md)
lists every built-in command and flag. The [Python API inventory](python-api.md)
lists declared exports, signatures, fields, and methods, including contributor
interfaces. An exported descriptor does not imply that core manages its server
object; plugin boundaries are listed below.

## Configuration and models

| Capability | Guide | Boundary |
|---|---|---|
| Declarative `DbwardenDatabase`, function `database_config`, project `DbwardenConfig` | [Configuration API](configuration-api.md) | One default database; plugin keys require registration |
| URLs, credentials, optional databases, environments, config discovery | [Configuration](../configuration/index.md) | Imported project code can execute; redacted output does not encrypt stored artifacts |
| Multiple databases and model ownership | [Multiple databases](../configuration/multi-database.md) | Separate targets and migration directories; overlap requires explicit configuration |
| SQLAlchemy models, table/column Meta, backend specs | [Models](../models.md) | Accepted fields and emitted operations vary by backend |
| Model discovery and selective tables | [Discovery](../configuration/model-discovery.md) | Importable mapped classes; avoid application startup side effects |
| Declarative managed rows, derivations, validations, historical transitions, frozen artifacts, and data journal | [Declarative data migrations](../declarative-data-migrations.md) | Versioned bundles use transactional or checkpointed execution; backend conformance evidence is separate from SQL generation |
| Reverse engineering with `generate-models` | [Schema inspection](../cookbook/05-schema-inspection.md) | Review inferred types, defaults, and unsupported objects |
| Pydantic schema generation with `auto_schema` | [Auto schemas](../cookbook/10-auto-schemas.md) | Runtime validation schemas are separate from migration state |

## Migration authoring and state

| Capability | Guide | Boundary |
|---|---|---|
| Model-based SQL generation, rename detection, explicit `new` migrations | [First migration](../getting-started/first-migration.md) | Generated SQL requires review; name similarity does not prove a rename |
| Versioned SQL, upgrade/rollback sections, RA/ROC repeatables | [Migration files](../migration-files.md) | Repeatables use execution/checksum rules; versioned migrations run in order |
| Offline export, diff, generation, and state recovery | [Offline workflow](../cookbook/04-offline-ci.md) | Saved state must correspond to migration history |
| Expand-contract type changes and severity splits | [Safety-scoped migrations](../correctness/safety-scoped-migrations.md) | SAFE/INFO/WARN/CRITICAL are safety levels; categories are named groups |
| Pending-plan composition, base/deferred state, strict pending checks | [Safety-scoped migrations](../correctness/safety-scoped-migrations.md) | Checksums and recorded before/after states must agree |
| Manual SQL classification with `check --write-plan` | [CLI guide](../cli-reference.md) | Static analysis can return UNKNOWN; it cannot establish live data safety |
| Merge detection, SQL/plan renumbering, reconcile, and rebase | [Merge handling](../advanced/merge-handling.md) | Persistent environments need reconciliation; plans and JSON state remain paired |
| Snapshots, checksums, history, status, recover-model-state | [Checksum integrity](../advanced/checksum-integrity.md) | Checksums detect changes; they do not prove the SQL is correct |

## Execution and inspection

| Capability | Guide | Boundary |
|---|---|---|
| Apply, rollback, downgrade, and make-rollback | [Apply and inspect](../cookbook/03-apply-and-inspect.md) | An inverse schema change cannot recover deleted data |
| Execution severity ceilings and repeatables | [Safety-scoped migrations](../correctness/safety-scoped-migrations.md) | A ceiling stops at a version prefix; `--force` does not raise it |
| Read-only diff, check-db, check-impact | [Safety and impact](../cookbook/06-safety-impact.md) | Reports use available schema/code evidence, not application correctness proofs |
| Data probes, convergence, journal reconciliation, and explicit reapply | [Declarative data migrations](../declarative-data-migrations.md) | `--data` adds live data checks; rolled-back data needs explicit reapply; uncertain durable effects require proof before retry |
| Lock-status, unlock, native locks, ClickHouse leases | [Locking](../advanced/migration-locking.md) | ClickHouse lease checks have weaker guarantees than native mutual exclusion |
| Sandboxed migration replay | [Workflows](../getting-started/workflows.md) | Core includes SQLite; external database providers require plugins |
| SQLite dev mode and SQL translation | [SQL translation](../sql-translation.md) | SQLite execution does not verify another server's behavior |
| JSON output, debugging, performance tracing, metrics, and logs | [Observability](../observability.md) | Dependencies and enabled hooks determine available telemetry |
| Skipped databases, partial success, and exit codes | [CLI guide](../cli-reference.md) | Exit code 3 needs result inspection; unresolved static plans use exit code 4 |

## Extensions and backend-specific operations

| Capability | Guide | Boundary |
|---|---|---|
| Plugin install, consent, trust, provenance, discovery, and removal | [Using plugins](../plugins/index.md) | Plugins execute in-process; trust labels are not a sandbox |
| Plugin configuration, value hooks, object handlers, ordering anchors | [Developing plugins](../plugins/developing/overview.md) | Follow the core/plugin API version contract |
| Base/deferred statements and custom named migration groups | [Plugin CLI](../plugins/reference/plugin-cli.md) | Registered group names do not change safety levels |
| Plugin CLI commands and category introspection | [Hook catalog](../plugins/reference/hook-catalog.md) | Installed plugins determine extra commands and groups |
| Seed descriptors, SQL/Python seed files, export/apply/rollback | [Seeds](../seeds.md) | Seed extensions may require `dbwarden-seeds`; inspect installed support |
| FastAPI sessions, lifespan, health, and management routes | [FastAPI](../cookbook/09-fastapi-integration.md) | External `dbwarden-fastapi` package; not implemented in this repository |
| PostgreSQL types, functions, extensions, RBAC, and object metadata | [PostgreSQL](../databases/postgresql/index.md) | Several config-owned objects require their corresponding plugins |
| MySQL metadata and MariaDB-specific fields | [MySQL and MariaDB](../databases/mysql/index.md) | MariaDB-specific emission is partial; review MODIFY preservation limits |
| SQLite rebuilds, generated columns, constraints, and table options | [SQLite](../databases/sqlite/index.md) | Rebuilds copy rows and can fail on changed constraints |
| ClickHouse engines, views, aggregation, dictionaries, projections, and indexes | [ClickHouse](../databases/clickhouse/index.md) | Server acceptance and convergence depend on version and schema |
| ClickHouse DataOp descriptors and populate helpers | [Data operations](../databases/clickhouse/data-operations.md) | Descriptors do not execute or automatically register SQL |
| ClickHouse RBAC and named collections | [RBAC](../databases/clickhouse/rbac.md) | Core exports specs; external plugin owns DDL and policy |

## Maintaining coverage

Run `python scripts/check-docs.py` after API, command, or documentation changes.
It checks declared exports, generated inventories, snippet syntax and supported
call signatures, CLI flags, links, anchors, and navigation. It does not execute
documentation snippets against external services. Use the relevant runtime
tests and a target-server integration run for behavior claims.
