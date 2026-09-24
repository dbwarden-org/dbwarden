# Safety-scoped migrations refactor report

Implemented in working tree. No commit or push made. The schema-refactor verification passed **2,846 tests, with 92 skipped and 16 warnings**. Subsequent declarative-data changes and current verification are recorded in [the data implementation report](DATA_IMPLEMENTATION_STATUS.md). Repository-wide lint and typing diagnostics from the earlier audit remain; the test results below describe their original runs. See [documentation and API audit](DOCUMENTATION_AUDIT.md) for the earlier follow-up evidence.

## Follow-up: merge, scope and offline error handling

Generation and merge now restore SQL, plans, superseded markers, merge records and both state aliases after write exceptions. Rollback preserves exact bytes, including existing CRLF content. If restoration itself fails, other files are still attempted and the error identifies unrecovered paths. This does not provide process-crash or power-loss recovery.

Offline generation rejects missing or malformed baselines with a nonzero exit. Malformed split metadata becomes UNKNOWN and stops capped migration selection. Tests reject duplicate operation IDs, missing dependencies, dependency cycles, forward dependencies and orphan plan collisions. Repeatable output remains unsplit and failed replacement restores its prior bytes.

Default database application resolves the configured database name before writing snapshots and state. Generation and migration now agree on `model_state.primary.json`; both database-specific and legacy aliases remain consistent. Repeatable execution cannot advance model state past a deferred versioned migration. Empty `diff --out json` output is a valid empty array.

Real temporary Git projects test branch collisions, merge dry runs, injected marker/record/state failures and successful retry. Offline projects verify safe widening, risky narrowing, SQL placement, trusted plan composition, repeatables and plugin-defined categories without connecting to a server.

## Follow-up: temporary projects and plugin categories

Implemented the requested named migration groups without adding safety levels.
Plugins register groups through `PluginRegistrar.register_migration_category(name,
order=...)` and set `category`/`safety` on `Op` or `MigrationStatement`. Base is order
0; deferred is 100. Custom orders are unique positive integers other than 100.
Dependencies promote operations to later groups, and severity splitting imposes
deferred as a minimum for operations above its threshold. An atomic operation
cannot span categories; plugins return separate operations for separate stages.

Each generated group has its own version, SQL/rollback, trusted plan, category
metadata, and chained state checksums. Repeatables remain unsplit. Unknown safety,
unknown categories, invalid names/orders, conflicts, and inconsistent category
metadata fail validation. Existing generated plans can be consumed without loading
the plugin that supplied their recorded classification.

CLI additions: `plugin categories --format json`, `plugin list --load`, and
`plugin info <distribution> --load`. Plugin reports include `migration_categories`;
status displays category. Default list/info inspection still avoids plugin imports.

Real temporary-project tests exposed and now cover these fixes:

- Safe type generation accepts SQLAlchemy's non-key `autoincrement="auto"`.
- PostgreSQL offline diffs do not emit ClickHouse `MODIFY COLUMN` from cached type
  metadata. Typed state retains the updated descriptive metadata.
- Type-change `--plan` output remains parseable JSON.
- Merge writes matching database-specific and legacy state JSON.
- Windows migration filenames use basenames; RA/ROC also retain existing legacy
  history keys, avoiding repeated unchanged ROC execution or duplicate records.

Six CLI project workflows use actual config/model files and fresh processes:
PostgreSQL safe expansion/contract, integer widening, varchar narrowing, SQLite
RA/ROC execution, entry-point plugin discovery/consent with three generated groups,
and a real two-branch Git merge with colliding versions. Checks include dry-run
immutability, native PostgreSQL parsing of upgrade/rollback SQL, exact typed-state
composition, both JSON paths, unchanged regeneration, severity skip/retry, changed
ROC checksums, and legacy Windows history preservation.

| Follow-up validation | Result |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q --tb=short` | **2753 passed, 38 failed, 84 skipped, 16 warnings**, 206.63 seconds; `.plugin-full-tests.log`. All 38 failing IDs exactly match `.release-tests.log`. Includes all six temporary CLI project workflows. |
| Focused safety/plugin/generation/merge/reconcile suite | **215 passed**, 19.12 seconds; `.plugin-focused-tests.log`. |
| Import-boundary check | Passed. |
| Main/plugin/category/list/info/generation/merge/migrate help | All eight invocations passed. |
| Ruff, whole package | **1317 existing findings**, no new normalized file/code/message diagnostics compared with previous verified output. New core generation/safety modules and new test files pass focused Ruff. |
| mypy, whole package | **204 existing errors**, no new normalized file/message diagnostics. |
| `git diff --check` | Passed. |
| Documentation generator | Passed; 145 pages, 1097 KB. |

PostgreSQL evidence is offline generation, plan/state validation, and native SQL
parsing. No live PostgreSQL server execution was performed. SQLite repeatables and
migration lifecycle checks execute SQL against temporary database files. Repository
files remain uncommitted; Git commits occur only inside the disposable test repo.

Additional paths changed in this follow-up (beyond the manifest below):
`dbwarden/plugin.py`, `dbwarden/commands/plugin_cmd.py`,
`dbwarden/commands/downgrade.py`, `dbwarden/commands/rollback.py`,
`dbwarden/engine/core/protocol.py`, `dbwarden/engine/core/statement_order.py`,
`docs/plugins/developing/object-plugins.md`, `docs/plugins/reference/plugin-cli.md`,
`tests/test_plugin_api_surface.py`, `tests/test_plugin_categories.py`, and
`tests/test_offline_project_safety.py`.

## Pipeline

Online generation, offline generation, merge, and persistent-environment reconciliation use `diff_states` and `generate_files` in `dbwarden/commands/make_migrations/generation.py`.

Generation composes an applied snapshot or recorded baseline with pending, hash-bound typed plans. It excludes applied and superseded versions, rejects stale base/target checksums, and checks deferred-state contradictions. Typed operations carry state changes and dependencies. The shared classifier supplies recorded severity and required acknowledgements; the writer partitions by downstream closure and emits independently reversible SQL/plan pairs atomically per file.

The execution ceiling selects a strict version prefix before preflight/execution. Existing migration execution and lock cleanup remain responsible for SQL, history, failure handling, and release. Severity deferral exits 3; static classification with unresolved files exits 4. `--force` does not bypass ceilings.

Removed the unused second CLI registry. Legacy Python helpers remain where existing callers require them; SQL replay is restricted to the legacy preview fallback.

## Specification accounting

| Requirement | Result |
|---|---|
| Workstream A: merge/rebase/reconcile | Shared typed writer, collision-preserving supersession, rename metadata, complete merge records, environment probes, dry runs, applied history, and convergence checks implemented. Temporary-Git merge and real SQLite reconciliation tests pass. Live server validation remains open. |
| Workstream B: common effective state | Implemented online/offline composition, strict pending policy, superseded/applied filtering, stale-plan rejection, contradictions, and deletion/regeneration behavior. |
| Spec §§1–5: severity and architecture | One canonical severity model; UNKNOWN fails closed under capped execution. Recorded plans remain scheduling metadata, not database authorization. |
| §6: generation and splitting | Implemented dependency closure, cycle/order refusal, empty-side behavior, stable versions, deferred suffix, per-file rollback, typed state, hashes, config overrides, and repeatable rejection. Indexes emitted with new tables are separate typed operations. |
| §6.5: existing flags | Rename prompting and noninteractive drop/add fallback preserved. Non-concurrent PostgreSQL indexes classify WARN. Safe type changes produce expansion plus guarded contract; unsupported dependency-bearing columns fail before writes. ClickHouse recreate retains its risk classification. |
| §7: execution | Strict prefix, exit 3, repeatable skip/retry, UNKNOWN handling, force orthogonality, count/version bounds, multi-database aggregation, dry run, and metadata-only baseline implemented. Existing backup/sandbox/lock paths retained. |
| §7.8: static classification | Whole-file, static, hash-bound, idempotent adoption; generated plans protected; review report and exit 4 implemented. Unknown statements, opaque calls, nested DML, executable comments, and type changes requiring unavailable prior type metadata remain UNKNOWN. |
| §8: visibility | Status severity/deferred/blocked fields, check-to-file annotations, structured events, optional deferral counts, and age gauge implemented. |
| §9: deployment workflow | Documented application compatibility, acknowledgement, ceiling, and deferred deployment obligations. Application compatibility remains operator responsibility. |
| §10: failure matrix | Focused tests cover split conservation, cycles/dependencies, pending state, edits/deletions, contradictions, failure recovery, rollback, bounds, repeatables, aggregation, and force. PostgreSQL/MySQL/ClickHouse live-engine evidence remains incomplete. |
| §§11–13: configuration/compatibility | Database config and CLI precedence, schema 1.1 metadata, legacy fields, normalized full-file hashes, filename compatibility, and trust boundaries implemented. Existing optional-database skip exit 3 retained and distinguished in structured output. |
| §14: open choices | Adopted WARN for non-concurrent PostgreSQL indexes. Two-way splitting only; expiry enforcement, per-operation execution ceilings, and registration-time plugin severity requirements remain outside v1. Unknown plugin operations are refused. |
| Definition of done: all-green gates | **Unfinished.** Full test/lint/type gates remain red; details below. Docker unavailability blocked live server validation. |

## Initial implementation verification

Environment: Windows, CPython 3.12.13, project `.venv`. Repository guidance specifies pytest, `ruff check dbwarden/`, and `mypy dbwarden/`; CI also specifies the P0 gate.

| Command | Result |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q --tb=short` | **2,732 passed, 38 failed, 84 skipped**, 17 warnings; 55.73 s. |
| `.venv/Scripts/python.exe -m pytest -q -m p0 --tb=short` | **9 passed, 4 skipped**. |
| Focused command below | **66 passed**. |
| `.venv/Scripts/python.exe scripts/check-import-boundary.py` | Passed. |
| `.venv/Scripts/python.exe -m ruff check dbwarden --output-format json` | **1,317 findings**; baseline HEAD: 1,486. No new file/code/message diagnostics. |
| `.venv/Scripts/python.exe -m mypy dbwarden --no-error-summary` | **204 errors**; baseline HEAD: 291. No new file/message diagnostics. |
| Ruff on the 10 new/reworked core modules below | Passed. |
| Typer `CliRunner`, `--help` for every registered top-level command | **28 commands passed**. |
| `.venv/Scripts/python.exe scripts/generate_llms_full.py` | Regenerated 145 documentation pages. |
| `git -c core.safecrlf=false diff --check` | Passed. |

Focused tests:

```powershell
.venv/Scripts/python.exe -m pytest -q --tb=short tests/test_generation_scope.py tests/test_generation_lifecycle.py tests/test_severity_plans.py tests/test_severity_scope.py tests/test_severity_lifecycle.py tests/test_static_severity.py tests/test_merge_generation.py tests/test_reconcile_lifecycle.py
```

Focused lint:

```powershell
.venv/Scripts/python.exe -m ruff check dbwarden/commands/make_migrations/generation.py dbwarden/commands/make_migrations/__init__.py dbwarden/engine/generation_state.py dbwarden/engine/safety/classifiers.py dbwarden/engine/safety/partition.py dbwarden/engine/safety/plans.py dbwarden/engine/safety/scope.py dbwarden/engine/safety/static.py dbwarden/commands/merge.py dbwarden/commands/reconcile.py
```

The initial test baseline had 124 failures, 2,580 passes, and 84 skips. It included missing timezone data; installing test-only `tzdata` resolved that environment issue. Do not attribute the entire passing-count increase to code changes. All 38 final failing test IDs were present in the baseline; no new failing IDs appeared. Remaining failures include Windows path literals in generated test configs, unavailable symlink privilege, and temporary-file/current-directory cleanup.

Raw final test evidence is in `.release-tests.log`, focused results in `.release-focused.log`, and type-check output in `.verified-mypy.log`. These local logs are ignored by Git. Baseline static checks used an untouched HEAD archive and the same installed tools.

## Boundaries and residual work

- Full-green CI acceptance remains open. Existing test portability and repository-wide lint/type debt were not silently skipped or represented as passing.
- Docker reported that `dockerDesktopLinuxEngine` was unavailable. PostgreSQL, MySQL, and ClickHouse live execution was not validated. SQLite execution and native PostgreSQL syntax parsing were validated; neither proves other engines' runtime behavior.
- Safe type-change automation refuses keys, dependent indexes/constraints, identity/generated metadata, and unsupported backends. Those need an explicit migration preserving dependencies. The generated base includes a backfill instruction; operators provide backfill and application dual writes.
- PostgreSQL uses native grammar validation; classification is a conservative mapped subset. Other dialects also retain UNKNOWN for unsupported constructs. No exhaustive parser-coverage certification is claimed.
- Plugin relationships require accurate typed dependencies. Unknown operation kinds and unrepresented state changes fail closed.
- Static analysis cannot determine old column types without state. Such handwritten ALTER TYPE files require an explicit typed plan instead of an inferred low-risk label.

SQLGlot and pglast were added because stdlib and existing regex replay cannot provide statement ASTs plus native PostgreSQL grammar validation. Version ranges are pinned. Neither parser executes SQL.

## Changed files

- `dbwarden/cli/app.py` (deleted)
- `dbwarden/cli/commands/__init__.py` (deleted)
- `dbwarden/cli/commands/check_cmd.py` (deleted)
- `dbwarden/cli/commands/generation_cmd.py` (deleted)
- `dbwarden/cli/commands/init_cmd.py` (deleted)
- `dbwarden/cli/commands/migration_cmd.py` (deleted)
- `dbwarden/cli/commands/seed_cmd.py` (deleted)
- `dbwarden/cli/commands/utils_cmd.py` (deleted)
- `dbwarden/cli/main.py` (modified)
- `dbwarden/commands/__init__.py` (modified)
- `dbwarden/commands/check.py` (modified)
- `dbwarden/commands/make_migrations/__init__.py` (modified)
- `dbwarden/commands/make_migrations/migrate_plan.py` (modified)
- `dbwarden/commands/make_migrations/pipeline.py` (modified)
- `dbwarden/commands/make_migrations/snapshot_merge.py` (modified)
- `dbwarden/commands/merge.py` (modified)
- `dbwarden/commands/migrate/__init__.py` (modified)
- `dbwarden/commands/migrate/state.py` (modified)
- `dbwarden/commands/rebase.py` (modified)
- `dbwarden/commands/reconcile.py` (modified)
- `dbwarden/commands/status.py` (modified)
- `dbwarden/config/build.py` (modified)
- `dbwarden/config/state.py` (modified)
- `dbwarden/config_registry.py` (modified)
- `dbwarden/config_schema.py` (modified)
- `dbwarden/connection/queries/store.py` (modified)
- `dbwarden/engine/backends/clickhouse/safety.py` (modified)
- `dbwarden/engine/backends/postgresql/handlers/column_handler.py` (modified)
- `dbwarden/engine/backends/postgresql/handlers/pg_table_handler.py` (modified)
- `dbwarden/engine/backends/postgresql/safety.py` (modified)
- `dbwarden/engine/core/snapshot_io.py` (modified)
- `dbwarden/engine/file_parser.py` (modified)
- `dbwarden/engine/model_discovery/sql_generation.py` (modified)
- `dbwarden/engine/offline/constraints.py` (modified)
- `dbwarden/engine/offline/diff.py` (modified)
- `dbwarden/engine/preflight.py` (modified)
- `dbwarden/engine/safety/analyzer.py` (modified)
- `dbwarden/engine/safety/classifiers.py` (modified)
- `dbwarden/engine/snapshot/diff.py` (modified)
- `dbwarden/engine/snapshot/sql_gen.py` (modified)
- `dbwarden/engine/version.py` (modified)
- `dbwarden/merge/detection.py` (modified)
- `dbwarden/merge/marker.py` (modified)
- `dbwarden/merge/reconciliation.py` (modified)
- `dbwarden/merge/rename_capture.py` (modified)
- `dbwarden/metrics.py` (modified)
- `docs/changelog.md` (modified)
- `docs/cli-reference.md` (modified)
- `docs/commands/check.md` (modified)
- `docs/commands/make-migrations.md` (modified)
- `docs/commands/merge-commands.md` (modified)
- `docs/commands/migrate.md` (modified)
- `docs/commands/status.md` (modified)
- `docs/correctness/safety-classifier.md` (modified)
- `docs/databases/postgresql/migration-safety.md` (modified)
- `docs/llms-full.txt` (modified)
- `docs/llms.txt` (modified)
- `docs/migration-files.md` (modified)
- `docs/reference/configuration-api.md` (modified)
- `pyproject.toml` (modified)
- `scripts/generate_llms_full.py` (modified)
- `tests/engine/snapshot/test_diff.py` (modified)
- `tests/engine/test_unique_constraint_emission.py` (modified)
- `tests/test_cli_debug.py` (modified)
- `tests/test_offline_migrations.py` (modified)
- `tests/test_schema_meta.py` (modified)
- `tests/test_status.py` (modified)
- `uv.lock` (modified)
- `zensical.toml` (modified)
- `dbwarden/commands/make_migrations/generation.py` (added)
- `dbwarden/engine/generation_state.py` (added)
- `dbwarden/engine/safety/partition.py` (added)
- `dbwarden/engine/safety/plans.py` (added)
- `dbwarden/engine/safety/scope.py` (added)
- `dbwarden/engine/safety/static.py` (added)
- `docs/correctness/safety-scoped-migrations.md` (added)
- `tests/test_generation_lifecycle.py` (added)
- `tests/test_generation_scope.py` (added)
- `tests/test_merge_generation.py` (added)
- `tests/test_reconcile_lifecycle.py` (added)
- `tests/test_severity_lifecycle.py` (added)
- `tests/test_severity_plans.py` (added)
- `tests/test_severity_scope.py` (added)
- `tests/test_static_severity.py` (added)
- `REFACTOR_REPORT.md` (added; this report)
