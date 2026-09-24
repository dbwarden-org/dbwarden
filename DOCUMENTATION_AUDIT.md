# Documentation and API audit

Audit date: 2026-09-22. Existing refactor changes were preserved.

Declarative-data follow-up: documentation coverage now includes 160 documents, 582 Python snippets, 66 CLI commands, and 380 exports. Drift checks and the strict site build pass. [The data implementation report](DATA_IMPLEMENTATION_STATUS.md) records current code and backend evidence; the counts below describe the earlier schema-refactor audit.

## Test repair follow-up

The subsequent full suite passed: **2,846 passed, 92 skipped, 16 warnings** in 295.13 seconds (`.repair-complete-tests.log`). This supersedes the failing test baseline below. Eight native symlink cases need privileges unavailable on this Windows host; the same eight path-rejection cases pass with narrowly mocked links. Other skips remain environment-dependent.

Repairs cover Windows file handles and path escaping, SQLite backup flushing, default-database state naming, empty JSON diff output, malformed safety metadata, offline failure exits and exception recovery across migration artifacts. New tests exercise real Git merge collisions, dry runs, retry after injected write errors, exact state-file restoration, split application/resumption, repeatables, safe type changes and plugin categories.

The offline guide now states that `export-models` reads models without a database connection. Exported JSON has no embedded checksum; plans and schema snapshots have their own integrity checks. File rollback attempts every path and reports restoration failures; it is not a crash-recovery journal.

That documentation check covered 156 documents, 575 Python snippets, 43 CLI commands and 366 exports. The strict site build and import-boundary check passed. Repository-wide lint and typing findings from the earlier audit remain separate from the passing tests.

## Scope

The audit covers root Markdown, README, example READMEs, every documentation
page, site navigation, the curated `llms.txt` index, and generated
`llms-full.txt`. Public and contributor exports are checked against Python
imports; CLI coverage comes from the registered Typer command tree.

Added three reference pages:

- [Feature map](docs/reference/feature-map.md): workflows, documentation links,
  plugin ownership, and implementation limits.
- [Python API inventory](docs/reference/python-api.md): export names,
  signatures, declared fields, methods, and re-export relationships.
- [CLI option inventory](docs/reference/cli-options.md): every built-in command,
  argument, flag, type, and default.

## Findings and corrections

| Finding | Correction |
|---|---|
| 42 names listed in backend `__all__` did not resolve | Connected them to existing implementations; kept cycle-sensitive imports lazy |
| Kafka, S3, S3Queue, RabbitMQ, and NATS helpers discarded arguments | Render connection arguments, named-collection overrides, and settings; reject invalid inputs |
| Several engine helpers emitted unquoted string values | Quote literal values while preserving SQL expressions |
| Redis helper used the wrong positional argument order | Emit host, database index, password; validate the legacy `storage` argument |
| SummingMergeTree emitted separate column arguments | Emit a tuple for multiple summation columns |
| Distributed policy could occupy the sharding-key position | Require an explicit sharding key when a policy is supplied |
| Replicated engine paths were emitted without quotes | Quote path and replica arguments without duplicating serialized values |
| Confirmation commented only the first SQL line of a data operation | Comment every line and retain a regression test |
| Lock-holder termination referenced an unimported SQL builder | Import SQLAlchemy `text`; test PostgreSQL, MySQL, and ClickHouse branches with mocks |
| ClickHouse index annotations referenced an unimported `Literal` | Resolve annotations and test `get_type_hints` |
| Examples used nonexistent parameters, malformed Python, and unsupported CLI flags | Correct signatures, examples, and command syntax |
| DataOp examples implied execution and automatic deduplication | Document descriptor construction, explicit migration authoring, and history boundaries |
| RBAC and named-collection pages described unsupported fields and secret-store behavior | Document actual core descriptors and external plugin ownership |
| Architecture described retired generation paths and final-state advancement | Describe shared generation, pending plans, stage-specific state, categories, and safety scopes |
| Backend pages promised complete fidelity or unconditional atomicity | State operation, plugin, transaction, and server-version limits |
| ClickHouse coordination examples implied all profiles were built in | Distinguish core leases from externally operated coordination patterns |
| Navigation, links, Python version requirements, and site TOML drifted | Repair references and configuration; validate through a strict site build |

The prose pass removed marketing claims, redundant introductions, unsupported
timing claims, and awkward phrasing. It normalized headings and punctuation,
rewrote the entry pages and troubleshooting guide, and preserved technical
identifiers, SQL syntax, and historical version facts.

## Validation

| Check | Result |
|---|---|
| Documentation audit | 156 Markdown documents, 575 Python snippets, 43 CLI entries including groups, 366 export names; zero findings |
| Navigation | Every documentation page appears in site navigation |
| Strict Zensical build | Passed; no issues |
| Focused regression run | 482 passed, including ClickHouse, model discovery, metadata, offline projects, and plugin categories |
| Full suite | 2,776 passed, 38 failed, 84 skipped; failure IDs match the prior baseline |
| Final API checks after the lock and annotation fixes | 27 passed |
| Import-boundary check | Passed |
| New audit script and regression-test Ruff check | Passed |
| Repository Ruff | 1,288 findings, down from 1,317; no new diagnostic identities |
| Repository mypy | 198 errors in 42 files, down from 204; no new normalized diagnostic identities |
| Diff whitespace check | Passed |

The full-suite result preceded the final four lock/annotation regression cases;
those cases are included in the subsequent 27-test API run. At that point, 38 baseline failures remained, including Windows fixture path escaping, symlink privileges and connection cleanup. The test repair follow-up above supersedes that result.

Run evidence is in local `.docs-verified-tests.log`, `.docs-focused-tests.log`,
`.docs-last-api-tests.log`, `.docs-mypy.log`, and `.docs-strict-build.log`.
These logs are local test output, not shipped documentation.

## Evidence limits

The documentation checker verifies imports, signatures, snippet syntax, CLI
flags, inventory drift, links, anchors, and navigation. It does not execute all
documentation snippets. The API inventory includes descriptors and protocols;
importability alone does not prove every runtime behavior.

No live PostgreSQL, MySQL, MariaDB, ClickHouse, Redis, or message-broker test ran
for this audit. SQL generation, serialization, mocked lock-recovery branches,
SQLite behavior, and offline CLI workflows were tested. External plugin
implementations remain outside this repository's evidence boundary.

ClickHouse argument checks used its primary references for
[S3](https://clickhouse.com/docs/engines/table-engines/integrations/s3),
[Redis](https://clickhouse.com/docs/engines/table-engines/integrations/redis),
[Distributed](https://clickhouse.com/docs/engines/table-engines/special/distributed),
and [SummingMergeTree](https://clickhouse.com/docs/engines/table-engines/mergetree-family/summingmergetree).

## Repeat the checks

```bash
python scripts/check-docs.py
python scripts/check-import-boundary.py
python -m pytest tests/test_documented_apis.py
python scripts/generate_llms_full.py
zensical build --strict
```

After changing exports or command registration, refresh the corresponding
inventory using the commands in [codebase organization](docs/codebase.md).
