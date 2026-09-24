# Declarative data migrations: implementation and verification

The [audit](DATA_MIGRATIONS_AUDIT.md) records corrections to the original
proposal. The [normative design](docs/design/declarative-data-migrations.md)
defines the contract, and the [user guide](docs/declarative-data-migrations.md)
documents the public API and operating flow.

## Implemented behavior

| Area | Implementation | Regression evidence |
|---|---|---|
| Declarations, IR, and expressions | `DataMeta`, managed rows, deterministic derivations, validations, historical and kept current-model transitions, multi-target splits, many-source merges, capture, archive, and batching compile into canonical data specifications. The expression AST includes scalar JSON extraction and bounded string splitting. Structural validation checks required records, integer versions, declaration IDs and content checksums, table references, and policies. | `test_compiler.py`, `test_compiler_edges.py`, `test_expressions.py`, `test_expression_edges.py`, `test_ir_contract.py`, `test_validation.py`, `test_coverage_modes.py` |
| Advanced operations | Many-source merges require pinned sources, distinct priorities, explicit aggregate or winner rules, one target, and authenticated contribution and retained-source evidence. Capture rollback authenticates preimages and postimages. Archive requires a named reserved destination and acknowledgement. Keyset batching keeps all chunks in the statement transaction and never uses `OFFSET`. | `test_advanced_merges.py`, `test_phase6_data.py`, `test_phase6_convergence.py`, `test_batches.py` |
| Artifact integrity | SQL, plan, and guarded frozen Python form one checksummed bundle. The manifest binds ordered member names, media types, byte lengths, hashes, and semantic identity. Apply uses frozen content after live models or source files are removed. Renamed or altered members fail verification. | `test_artifacts.py`, `test_acceptance_integrity.py`, `test_frozen_views.py` |
| Snapshot lineage | Explicit IDs resolve by database, backend, and checksum. Registry updates serialize through an operating-system lock before atomic replacement. Imports support canonical branch, merge, and squash parents. Missing parents, cycles, duplicate IDs, and mismatched file identities fail. Resolution is read-only. | `test_snapshot_lineage.py`, `test_artifacts.py` |
| Unified generation | Schema creation, data backfill, validation, constraint contraction, and source retirement share generation and safety scoping. Base, deferred, and custom groups retain separate state projections. Data declarations require versioned migrations; existing schema repeatables remain supported. | `test_ordering.py`, `test_qualified_generation.py`, `tests/test_generation_scope.py`, `tests/test_scope_errors.py` |
| Branch merge and reconciliation | Branch reconciliation preserves existing frozen SQL and records hash-bound supersession sidecars. Persistent-environment reconciliation derives state from applied branch history and writes a new bundle. Failed publication restores prior files and state JSON. | `test_merge.py`, `test_reconcile.py`, `tests/test_merge_generation.py`, `tests/test_reconcile_lifecycle.py` |
| Journal lifecycle | Successful apply runs once per epoch. Baseline records explicit acknowledgement and skipped checks. Rollback retains prior history; another apply requires `--reapply-data`. Abandoned and final-failure states cannot silently restart. | `test_execution.py`, `test_execution_modes.py`, `test_reapply_cli.py` |
| Ownership and recovery | Retained HMAC identity and value edges support verification and key rotation. MySQL/MariaDB ownership inserts require matching affected-row counts and atomic `WRITE_VERIFIED` receipts. Generated rollback DML holds row locks through guards, writes, verification, and checkpoint commit. Each execution step has its own checkpoint ID. | `test_identity_collisions.py`, `test_insert_proofs.py`, `test_generated_nontransactional.py`, `test_nontransactional.py` |
| Backend safety | PostgreSQL locks referenced tables in the data transaction; SQLite uses an immediate write transaction. MySQL/MariaDB freeze historical sources before capture and restore source names after rollback cleanup. Many-source freeze uses one multi-table rename. ClickHouse uses synchronous mutations and an append-only versioned journal for its supported subset. | `test_backends.py`, `test_source_retirement.py`, `test_conditional_writes.py`, `test_advanced_merges.py` |
| Schema rollback ownership | A created target table is dropped only after owned-row cleanup and an empty-table check. PostgreSQL/SQLite retain the transaction boundary; MySQL/MariaDB rename before checking. SQLite rejects remaining views, foreign keys, and triggers that depend on the target. | `test_rollback_targets.py`, `test_audit_regressions.py` |
| CLI, sandbox, and convergence | Render, describe, generated Markdown, validation, planning, read-only dry runs, parameter binding, transition authoring, audit, reconciliation, `check --data`, and `diff --data` use compiled or frozen specifications. Live `check` also evaluates unapplied versioned, runs-always, and changed runs-on-change SQL from a trusted plan or the read-only classifier; unknown SQL and data drift cannot be forced. Sandbox replays schema and frozen data without changing project state and fails on data drift. Convergence checks authenticate merge, archive, and capture evidence when their applied plans are available. | `test_cli.py`, `test_frozen_views.py`, `test_audit_regressions.py`, `test_data_sandbox.py`, `test_phase6_convergence.py` |

The shared SQLite connection cache resolves relative file URLs against each
project directory. Temporary offline projects cannot reuse another project's
`sqlite:///app.db` connection. `tests/test_database_connection.py` covers
ordinary, driver-qualified, and URI-style URLs.

## Backend evidence and limits

| Backend | Evidence obtained | Remaining release gate |
|---|---|---|
| SQLite | Real generated SQL and local generation, apply, rollback, merge, archive, capture, batching, reconciliation, interrupted execution, ownership, sandbox, and convergence tests. | No claim about production server concurrency. |
| PostgreSQL | Real PostgreSQL 18.1 in an isolated local cluster, including managed rows, rollback, qualified historical sources, splits, competing-writer locks, many-source merge, batching, and rollback. | Broader production version and configuration qualification. |
| MySQL | MySQL 8.4.11 passed six native lifecycle flows: managed revision and reapply, two-revision archive rollback, captured derivation rollback, preserved transition rollback, batched many-source merge round trip, and retained-source tamper refusal. Native scalar-expression coverage is separate. | Connection-loss and process-crash injection, plus broader version and isolation qualification. |
| MariaDB | MariaDB 11.4.4 independently passed the same six native lifecycle flows. MySQL results are not used as a substitute. | Connection-loss and process-crash injection, plus broader version and isolation qualification. |
| ClickHouse | Native SQL lowering and instrumented append-only journal, synchronous mutation, failure, and reconciliation checks. Advanced merge, archive, capture, and batching fail planning. | Live ClickHouse conformance for the supported subset. |

Docker's Linux engine pipe was unavailable during the recorded broad run.
MySQL and MariaDB evidence came from native servers, not Docker. No live
ClickHouse conformance result is claimed. Environment-gated tests remain
visible rather than being replaced with mocks.

ClickHouse historical transitions fail planning because nonblocking rename
cannot establish the required source-write barrier. Scoped managed-row deletion
is rejected without an atomic insertion-ownership receipt. Its supported
mutation subset requires irreversible rollback. Cross-database transitions,
arbitrary Python functions, raw declaration SQL, and execution-scoped
expressions also remain unsupported.

## Open conformance work

- Exercise MySQL and MariaDB with connection-loss and process-crash injection.
- Exercise the supported ClickHouse subset against a live server, including
  connection loss and mutation completion.
- Qualify more PostgreSQL versions and relevant server configurations.
- Add operating-system crash tests around staged artifact publication and
  directory durability.
- Preserve old semantic readers when a future format version is introduced.
- Define a multi-file release record if a future migration can publish related
  transitions as separate bundles. Current generation uses one manifest-bound
  bundle per version and database.

## Recorded validation

| Check | Result | Evidence |
|---|---|---|
| Final broad core suite | **3,123 passed, 96 skipped, 0 failed**; 150.73 seconds | `.data-phase6-final-core-tests.log`; isolated PostgreSQL enabled |
| Current data suite | **274 passed, 4 skipped, 0 failed**; 23.07 seconds | `.data-phase6-final-data-tests.log`; includes enabled native backends |
| Native MySQL/MariaDB lifecycles | **12 passed, 0 failed** | Six flows on MySQL 8.4.11 and six on MariaDB 11.4.4; included in the data suite |
| Native scalar-expression set | **16 passed, 0 failed** | PostgreSQL, SQLite, MariaDB, and MySQL; included in the focused native set |
| Current focused native set | **36 passed, 0 failed**; 6.47 seconds | `.data-phase6-native-final-focused.log`; 3 kept-current-source, 12 MySQL/MariaDB lifecycle, 16 scalar-expression, and 5 sandbox cases |
| Current advanced integration set | **15 passed, 1 skipped, 0 failed** | `.data-phase6-integration.log`; environment-gated backend case skipped |
| Current sandbox set | **11 passed, 0 failed** | `.data-phase6-sandbox.log` |
| Documentation drift | **160 documents, 584 Python snippets, 66 commands, 387 exports; zero issues** | `scripts/check-docs.py` |
| Strict documentation build | Passed | `zensical build --strict` |

The broad run reported 16 warnings from existing SQLAlchemy reflection,
duplicate declarative classes, and deprecated fixture or API use. No
repository-wide lint or type-check result is claimed. The isolated PostgreSQL
cluster was stopped after that run.

The separate final harness run was **151 passed, 289 skipped, 0 failed** in
139.04 seconds (`.data-harness-final-tests.log`). The skips are explicit
environment, provider, scale, and benchmark gates; they are not counted as
passing conformance evidence.
