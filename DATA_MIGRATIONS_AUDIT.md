# Declarative data migrations audit

## Scope

This audit compares the preserved original proposal at
`G:\Downloads\DBWarden Declarative Data Migrations Specification.original.md`
with the current implementation and the corrected normative contract in
[docs/design/declarative-data-migrations.md](docs/design/declarative-data-migrations.md).
The original proposal was last updated on 2026-08-19. Line references below
refer to the preserved `.original.md` file. The non-suffixed file in Downloads
now contains the corrected canonical contract.

The original proposal is a useful product brief, but it is not a safe execution
contract by itself. It contains circular identities, conflicting snapshot and
coverage rules, an incomplete ownership model, and backend guarantees that do
not account for partial effects. The corrected design resolves those issues.
Implementation status is tracked separately in
[DATA_IMPLEMENTATION_STATUS.md](DATA_IMPLEMENTATION_STATUS.md).

This audit uses three evidence levels:

- **Implemented** means the checked-in source enforces the contract and focused
  tests exercise it.
- **Backend-observed** means the behavior was exercised against that database,
  rather than through generated SQL or an instrumented adapter.
- **Release-complete** means every required backend and failure mode has the
  evidence named by the completion criteria.

A unit test or generated SQL is not backend-observed evidence. A successful
apply is not proof of crash recovery, concurrency safety, rollback, or
convergence.

## Normative corrections to the original proposal

### Identity and canonicalization

Original lines 215-350 mix required derived IDs with the objects used to derive
those IDs. They also leave dependency IDs inside possible operation preimages.
That creates self-reference and graph-order dependence.

The corrected contract derives identities in this order:

1. Canonicalize declaration semantics without IDs, checksums, observations,
   timestamps, or runtime state.
2. Hash the declaration semantics with an explicit domain and format version.
3. Derive transition identity from database identity, declaration identity, and
   the declaration semantic hash.
4. Derive operation identity from local operation semantics without dependency
   IDs.
5. Derive each execution-step identity from operation identity, direction, and
   local step index.
6. Resolve dependencies and checksum the complete plan and artifact manifest.

Canonical values use NFC strings, sorted object keys, explicit typed values,
and strict JSON. Version fields require integers and reject booleans. Backend
SQL is produced only during lowering. Runtime identity and value tuples use
HMAC-SHA-256 with a retained per-database key, key ID, encoding version, and
separate domains. Raw row identities are not journaled.

### Semantic plan, observations, and artifact integrity

Original lines 121-132 say live data must not affect artifact semantics, while
lines 385-389 and 1718-1720 put probe results into generated plans. The corrected
plan separates immutable semantics from review evidence. Probes may block apply
and establish accepted thresholds, but they do not change mappings, policies,
identities, or SQL shape.

The three core members are SQL, plan JSON, and a guarded frozen data artifact.
The manifest binds ordered member names, media types, byte lengths, and hashes.
The plan's embedded manifest is excluded from its own component preimage. The
plan is published last. Apply, baseline, reconciliation, rollback, audit, and
convergence verify the bundle before using it.

### Snapshot pinning

Original lines 353-370 require an exact source snapshot, but lines 2056-2060
permit apply-time fallback. Fallback changes provenance even when schema bytes
match. Generation may resolve a candidate; the resulting artifact must pin one
snapshot ID, checksum, database, backend, and canonical parent lineage.
Execution never substitutes another snapshot.

Registry updates use an operating-system lock and atomic replacement. Reads do
not create directories. Duplicate IDs, missing or cyclic parents, ambiguous
files, invalid checksums, and database/backend mismatch fail.

### Static source freezing

Original lines 545-557 and 1071-1076 leave it unclear whether apply rereads a
CSV or JSON file. It does not. Generation resolves the path inside the project,
rejects symlink escape, parses and type-checks it, normalizes the rows, and
stores the canonical rows in the frozen specification. The original path is
provenance only.

### Ownership and scoped deletion

Original lines 563-577 do not define evidence sufficient to delete or overwrite
an application row. The corrected contract binds every mutation to a declaration,
table, identity, scope, owned columns, adoption policy, acknowledgement, and
runtime ownership or identity-edge record.

`on_missing="delete"` applies only to rows already claimed by that declaration,
still inside its explicit scope, and unchanged in the owned postimage. A
pre-existing equivalent row may be adopted for convergence, but it does not
become migration-created. Rollback cannot delete it. Overwrite requires an
explicit acknowledgement and remains limited to owned columns.

### Coverage, overlap, and conflict behavior

Original lines 898-947 use `exactly_once` both for raw match cardinality and for
priority assignment. The corrected modes are distinct:

- `all`: every source identity is assigned at least once;
- `subset`: unmatched source identities may remain untouched;
- `exactly_once_match`: exactly one raw target predicate matches;
- `all_assigned_once`: priority rewrites overlaps into one effective assignment.

Plain conflict `ignore` remains invalid. `ignore_if_equivalent` compares every
owned value. Recovery does not weaken `error` into ignore. Source identities
must be unique in the consistency boundary of the write, and target identities
must be constrained or serialized by an equivalent backend mechanism.

### Probe-to-write races

A read-only precheck is not authorization to write later. PostgreSQL locks
referenced tables inside the data transaction. SQLite uses `BEGIN IMMEDIATE`.
MySQL and MariaDB require InnoDB, disabled autocommit for ownership DML,
affected-row receipts, and target row locks for generated rollback DML. Their
historical source is renamed into a reserved frozen name before capture, which
prevents later writes through the original name. ClickHouse historical
transitions remain rejected because its rename semantics do not provide the
required source-write barrier.

### Journal, retry, and reconciliation

Original lines 1772-1843 define at-most-once success but do not specify enough
durable state for implicit-commit DDL. The corrected execution model has:

- a current run projection;
- append-only state and decision events;
- operation and statement checkpoints;
- authenticated source, target, and mapped-value edges;
- explicit application epochs.

A nontransactional statement records `PENDING` before a possible effect. An
ownership insert commits its rows and `WRITE_VERIFIED` receipt together. `DONE`
follows postcondition verification. A lost connection after a possible effect
is `UNKNOWN_REQUIRES_RECONCILIATION`; it is not retried automatically. A
successful rollback ends its epoch. Ordinary migration does not reapply it;
explicit `--reapply-data` starts a linked epoch.

### Rollback

Original lines 1946-1998 allow several readings of "restore." The corrected
rules are conditional on current ownership and postimage:

- delete only migration-created, unchanged rows;
- do not delete adopted equivalent rows;
- restore a prior DBWarden desired value or an explicitly captured preimage;
- restore a preserved source only after owned target cleanup;
- never drop an existing target table or a table containing unowned rows;
- fail clearly when the selected policy is irreversible.

Deterministic preserved names include migration or transition identity and are
excluded from desired-schema drift while remaining visible to audit.

### Baseline and event history

Original lines 1876-1879 call baseline appropriate when the target state already
exists but do not require proof or acknowledgement. Baseline is now an explicit
operator decision. Its event records `acknowledged=true`, a reason, and the IDs
of checks skipped or not proven. It does not claim convergence.

### Backend claims

Original lines 2117-2163 list emitters without defining the capability boundary
that makes them safe. Every lowered plan now negotiates transaction boundaries,
conditional writes, checkpoints, synchronous effect visibility, preservation,
and convergence support. Unsupported combinations fail before an applicable
bundle is emitted.

## Requirement disposition

The table maps the original acceptance criteria at lines 2536-2565 to current
status. It does not treat deferred live-backend evidence as a passing result.

| # | Requirement | Disposition |
|---:|---|---|
| 1 | Managed rows beside schema models | Implemented for inline and frozen CSV/JSON rows, with owned columns and scoped missing-row policy. |
| 2 | Deterministic transformations | Implemented through the typed expression AST and backend lowering, including scalar JSON extraction and bounded one-based string splitting. Arbitrary callables and raw SQL fail. |
| 3 | Versioned canonical IR and typed phase boundaries | Structural IR validation now checks required records, versions, declaration sets, checksums, table references, and policies. The compiler remains a dictionary pipeline rather than a family of phase classes; focused boundary and golden coverage remains a verification obligation. |
| 4 | Distinct deterministic identities | Implemented for declaration, transition, migration, operation, execution step, edge definition, runtime edge, run, and epoch identities. |
| 5 | Historical transition to one or more targets | Implemented for historical and pinned current-model sources with one or multiple targets. Current-model sources use `on_complete="keep"`; deterministic many-source merge into one target uses the bounded contract below. |
| 6 | Deleted model resolved from pinned snapshot | Implemented. Apply uses frozen artifacts and does not rediscover the model. |
| 7 | Branch and squash ambiguity fails | Implemented with parent-lineage validation and ambiguity tests. |
| 8 | Manual and interactive scaffolding | Implemented CLI surface; generated incomplete declarations remain invalid until completed. |
| 9 | Dry-run, render, describe, and Markdown documentation | Implemented through the shared compiler/frozen-view paths. Probe evidence remains observation-specific. |
| 10 | SQL, plan, and frozen artifact | Implemented as one verified manifest-bound set. |
| 11 | Frozen import guard and audit override | Implemented. Normal audit uses static AST parsing without importing the file. |
| 12 | Identity, cardinality, coverage, and overlap in plan | Implemented. |
| 13 | No accidental duplicate target identity | Statically and dynamically guarded; normalized source-identity collisions fail closed. |
| 14 | Generic merge disabled until formal semantics | Arbitrary merge remains rejected. The promoted deterministic subset requires pinned sources, stable identities and grouping keys, an explicit rule for every non-key field, authenticated provenance, and one target. |
| 15 | Conflict policies | `error`, `ignore_if_equivalent`, and acknowledged `overwrite` are implemented. Plain `ignore` remains rejected. |
| 16 | Expression/backend versions and settings | Implemented, including frozen parameters and PostgreSQL runtime setting checks. Backward compatibility across future semantic versions still requires retained old readers. |
| 17 | At-most-once success | Implemented per application epoch. Repeated success performs inspection reads but no migration mutation statements. |
| 18 | Execution states | Implemented, including final failure, rollback, and explicit reapply epochs. The append-only event schema must continue to retain every operator decision and reconciliation result as new modes are added. |
| 19 | Nontransactional checkpoints before retry | Implemented in source. MySQL 8.4.11 and MariaDB 11.4.4 passed native lifecycle flows; their connection-loss and process-crash matrix remains open. ClickHouse has instrumented evidence only. |
| 20 | Retry-safe generated operations | Implemented for the admitted operation set with fail-closed unknown states. Batch chunks share one statement checkpoint and transaction rather than claiming independent durable resume. |
| 21 | Transfer before destructive schema work | Implemented through the unified schema/data ordering path and source-retirement steps. Multi-file release state remains an open contract. |
| 22 | Explicit destructive acknowledgement | Implemented for deletion, overwrite, source drop, irreversible work, archive, and operator force gates. Capture is a declared rollback policy rather than a destructive acknowledgement. |
| 23 | Honest rollback | Implemented for admitted policies, including authenticated transformation and transition capture, archive restoration, preserved-source restoration, and irreversible refusal. |
| 24 | Composite artifact integrity | Implemented for the three frozen members and embedded static rows. Publication rollback is tested; operating-system crash durability still needs a dedicated fault test. |
| 25 | `check --data` | Implemented for managed rows, transformations, validations, transitions, merges, archive receipts, and capture receipts. Receipt checks use the matching successful frozen plan and journal epoch. |
| 26 | Sandbox convergence | Implemented with schema and frozen-data replay, project-state preservation, and a regression that injects data drift into the sandbox. Production source rows and load remain separate evidence. |

## Promoted advanced requirements

The original Phase 6 list at lines 2526-2534 described batching, capture,
archive, large-table controls, and generic merges as later work. The user has
explicitly promoted the following into the required release.

### Deterministic many-source merge

The merge contract must include:

- two or more explicitly pinned source relations;
- a source identity for each relation;
- one target identity and explicit grouping expressions;
- an explicit reducer or winner rule for every target field with more than one
  possible contribution;
- fixed null, type, collation, overflow, and tie behavior;
- source-to-group and group-to-target authenticated provenance;
- conflict, ownership, completion, capture, and rollback policies;
- duplicate, unmatched, and ambiguous-winner guards.

The implemented bounded subset admits integer or string source identities and
grouping keys. String comparison and winner ordering use UTF-8 byte encodings
rather than a database's default collation. `require_equal` uses the same
stable comparison. A caller must cast other types into an admitted stable
representation. `merge_sources(..., allow_empty=False)` rejects an empty
combined input by default; `allow_empty=True` records the explicit decision.
One input relation cannot appear twice in the same merge, and qualified and
bare snapshot table names cannot resolve ambiguously.

Arbitrary reduction callbacks, implicit input order, and implicit last-write
wins are invalid. A scalar field without one deterministic contributor,
`require_equal`, an ordered winner, or a reviewed aggregate must fail
compilation.

### Durable batching

Batching must preserve declarative outcome. It may split one statement into
keyset ranges, but it does not change identity, coverage, conflict, ownership,
or rollback semantics. The initial implementation requires a unique, non-null,
immutable key and canonical ascending ranges. It does not use `OFFSET`.

All chunks of one generated statement stay inside that statement's existing
transaction and ownership receipt. They are not independently committed or
resumable. A crash rolls back the statement where the backend provides the
required DML transaction; the normal statement checkpoint governs recovery on
nontransactional plans. This bounds statement size without claiming a smaller
transaction or durable per-batch progress.

The cooperative `max_duration` budget spans the full declaration attempt and
resets on retry. Statement, lock, and replication-lag limits may also abort and
roll back the current statement. They must not silently reduce the declared
result set. Source retirement and global coverage validation occur only after
the complete statement succeeds.

This replaces the original proposal's undefined "maximum estimated duration"
with an observed monotonic attempt budget. The compiler does not present a
cost estimate as an execution guarantee.

### Capture and archive

Capture must use a reserved, manifest-bound store keyed by migration, epoch,
declaration, target identity, owned column, and typed preimage. It must capture
inside the same consistency boundary as the write and distinguish missing,
null, and value. Rollback may restore only when the current value still matches
the recorded postimage.

Archive is a data move, not a synonym for delete. The declaration must identify
the archive target, archive identity, mapped columns, ownership, conflict
policy, retention expectation, acknowledgement, and rollback policy. Archive
write and source deletion need one atomic boundary or a durable two-step
protocol with reconciliation.

## Remaining required work

The following work prevents a claim of complete conformance even when all
current tests pass:

1. Extend the separate native MySQL 8.4.11 and MariaDB 11.4.4 lifecycle results
   with connection-loss, process-crash, and broader version/isolation tests.
2. Run live ClickHouse conformance for the supported subset, including mutation
   completion, append-version journal reads, tamper detection, and connection
   loss.
3. Keep broader PostgreSQL version and configuration qualification distinct
   from the observed local PostgreSQL run.
4. Define a multi-file release record if a future migration can publish related
   transitions as separate bundles. Current generation uses one verified
   bundle per version and database.
5. Add operating-system crash tests around staged artifact publication and
   directory durability, not only exception rollback.
6. Preserve old semantic readers when format version 2 is introduced; current
   version-1 tests cannot prove future backward compatibility.

## Genuine non-goals

These exclusions preserve the product boundary and do not weaken a promised
feature:

- arbitrary Python migration callbacks or reducers;
- raw user SQL inside declarations;
- network, filesystem, environment, random, clock, or mutable-global reads
  during expression execution;
- cross-database transitions without a separate distributed protocol;
- execution-scoped expressions whose value is not frozen at generation;
- silent conflict `ignore`;
- unbounded ETL, streaming, or external job orchestration;
- automatic semantic inference where mapping, identity, aggregation, winner,
  ownership, or rollback is ambiguous.

## Backend evidence matrix

| Backend | Implemented semantics | Evidence boundary | Release decision |
|---|---|---|---|
| SQLite | Managed rows, transformations, validations, transitions, deterministic merges, capture, archive, batching, ownership, rollback, sandbox, reconciliation, and convergence | Real local execution; no production-server concurrency claim | Development, CI, and sandbox reference backend |
| PostgreSQL | Transactional data plan, table locks, setting pins, ownership, transitions, deterministic merge, capture, archive, batching, rollback, and convergence | Real local PostgreSQL 18.1 coverage exists; broad version/configuration qualification remains | Current production backend for the implemented operation set |
| MySQL | Native lowering, atomic source freeze, implicit-commit checkpoints, insert receipts, rollback row locks, advanced operations, and reconciliation | Six lifecycle flows observed on MySQL 8.4.11; crash and broader-version matrix remains | Backend-observed subset; full release gate open |
| MariaDB | Separate lowering and journal protocol | Same six lifecycle flows observed independently on MariaDB 11.4.4; crash and broader-version matrix remains | Backend-observed subset; full release gate open |
| ClickHouse | Managed inserts, synchronous update mutations, validations, irreversible rollback policy, append-version journal | Generated SQL and instrumented connection; no live server conformance | Limited subset, not release-complete |

ClickHouse historical transitions, overwrite, and scoped managed-row deletion
remain unsupported because the required source barrier or atomic ownership
receipt is absent. Unsupported operations must fail planning and must not emit
an applicable artifact.

## Recorded verification

| Check | Result | Evidence boundary |
|---|---|---|
| Final broad core suite | **3,123 passed, 96 skipped, 0 failed** in 150.73 seconds | `.data-phase6-final-core-tests.log`; 16 warnings; isolated PostgreSQL enabled |
| Final data suite | **274 passed, 4 skipped, 0 failed** in 23.07 seconds | `.data-phase6-final-data-tests.log`; enabled native backends included |
| Focused native set | **36 passed, 0 failed** in 6.47 seconds | Kept current-model sources, MySQL/MariaDB lifecycles, four-backend scalar expressions, and sandbox cases |
| Final external harness | **151 passed, 289 skipped, 0 failed** in 139.04 seconds | `.data-harness-final-tests.log`; environment, provider, scale, and benchmark gates remain skipped |
| Documentation drift and build | 160 documents, 584 Python snippets, 66 commands, and 387 exports with zero issues; strict build passed | `scripts/check-docs.py`; `zensical build --strict` |

Passing tests establish the behavior exercised by those runs. Skipped native,
crash, version, and configuration gates remain open as listed above.

## Audit conclusion

The corrected implementation now covers the original initial safety core: typed
declarations, canonical identity, exact snapshot lineage, frozen artifacts,
ownership, guarded execution, durable recovery, rollback epochs, and
convergence. The original proposal must not be used as an implementation spec
without the corrections above.

The expanded operation set is present in source and focused regression tests.
Full production conformance still depends on MySQL/MariaDB crash testing, live
ClickHouse evidence, broader server-version qualification, and publication
crash durability. Those are evidence and durability gates, not undocumented
implementation claims.
