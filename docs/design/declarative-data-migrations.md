# Declarative data migrations

Status: normative contract; implementation and evidence status recorded below

This document is the corrected normative design for DBWarden declarative data migrations. It replaces ambiguous or conflicting requirements in the 2026-08-19 proposal. `MUST`, `MUST NOT`, `SHOULD`, and `MAY` have their usual standards meaning.

## Product boundary

DBWarden compiles reviewed data declarations into immutable migration artifacts. Users declare desired managed rows, deterministic transformations, and one-shot historical transitions. They do not provide arbitrary migration callbacks, sessions, network calls, or Python execution hooks.

The current SQLAlchemy model remains the only current schema definition. A data declaration may refer to current columns, but it does not redefine table shape. Historical structure comes from one explicitly pinned schema snapshot.

The production contract includes:

- inline and static-file managed rows;
- deterministic transformations and backfills;
- one-source, one-target historical transitions;
- one-source, multi-target splits;
- deterministic many-source, one-target merges with explicit grouping and field rules;
- captured transformation preimages and declared archive destinations;
- keyset-batched data statements with explicit duration, statement, lock, and replication-lag limits;
- unified schema and data dependency planning;
- immutable SQL, plan, and frozen audit artifacts;
- source and target identity analysis;
- explicit ownership, coverage, overlap, conflict, completion, and rollback policy;
- operation journals, reconciliation, rollback epochs, and convergence checks;
- PostgreSQL production execution;
- MySQL and MariaDB production execution after their non-transactional journal gate passes;
- SQLite development, test, and sandbox parity;
- the limited ClickHouse subset defined below.

The following remain unsupported and MUST fail validation:

- arbitrary merge reducers, implicit winners, and merges without complete source provenance;
- independently committed or resumable batch chunks;
- generic source reconstruction and inverse-mapping rollback without a preserved or captured source;
- plain conflict `ignore`;
- cross-database transitions;
- execution-scoped expressions;
- unregistered user-defined functions;
- execution above explicitly declared row limits.

Compiler preview for an unsupported backend is allowed. It MUST be labelled preview, MUST NOT emit an applicable artifact set, and MUST NOT count as backend conformance.

## Conformance status

The checked-in implementation covers the original safety core: declarations,
canonical identity, exact snapshot lineage, manifest-bound artifacts, ownership,
transactional and checkpointed execution, reconciliation, rollback epochs, and
convergence. Deterministic merge, archive, capture, and keyset batching are part
of this contract and require the same artifact, ownership, recovery, rollback,
and convergence rules as the original primitives.

Release evidence is backend-specific. SQLite has real local execution evidence.
PostgreSQL has real local execution and concurrency evidence for the implemented
operation set. MySQL 8.4.11 and MariaDB 11.4.4 each passed six native lifecycle
flows covering managed revisions, archive, capture, preserved transitions,
batched merge, rollback, reapply, and retained-source tamper refusal.
Connection-loss, process-crash, and broader-version qualification remain open.
ClickHouse has native lowering and an instrumented append-version journal, but
no live-server conformance. These limits are release gates; generated SQL and
adapter tests do not satisfy them.

## Core declarations

### Managed rows

A model may declare a finite set of owned rows. A managed-row declaration contains:

- a stable `declaration_id`;
- target table;
- one or more non-null key columns;
- canonical inline rows or a frozen static source;
- owned columns;
- an explicit row scope predicate;
- `on_missing`, initially `keep` or `delete`;
- adoption and conflict policy;
- destructive acknowledgement when deletion or overwrite is possible;
- rollback policy.

If no key is declared, DBWarden MAY use the model primary key. An explicitly selected natural or composite key MUST be backed by a unique constraint or an equivalent backend proof.

`on_missing="delete"` applies only to rows already claimed by the same declaration and still inside its explicit scope. It never means "delete every unlisted row in the table." The runtime ownership ledger distinguishes a row created by DBWarden, adopted as equivalent, or overwritten with acknowledgement.

An existing target row is accepted only when all owned values are equivalent or the declaration explicitly acknowledges overwrite. Columns outside `owned_columns` are never changed.

### Static row sources

At generation, DBWarden resolves a static source inside the project root, rejects symlink escape, parses and type-checks it, canonicalizes its rows, and freezes the canonical content into the artifact set. The original relative path is provenance only. Apply never reads the original project file.

The source format contract specifies encoding, newline normalization, CSV dialect when applicable, null syntax, number parsing, duplicate-key rejection, deterministic row order, and maximum size. Static sources containing secrets are unsupported.

### Transformations

A transformation declares a target column or columns, a deterministic expression, a complete domain, unmatched-row behavior, maximum affected rows, ownership, and rollback policy.

Expression-language version 1 permits:

- column references and literals;
- bound parameters frozen at generation;
- versioned arithmetic and explicit casts;
- lower, upper, trim, and concatenation under pinned string semantics;
- `coalesce` and `case`;
- complete static mappings;
- scalar JSON extraction through `json_extract(value, literal_path)`;
- one-based deterministic string components through
  `split_part(value, literal_delimiter, literal_index)`;
- reviewed deterministic backend functions with pinned settings.

`json_extract` requires a literal path beginning with `$`. Strings are decoded,
numbers become text, booleans become `true` or `false`, and JSON null, a missing
path, an object, or an array becomes SQL null. PostgreSQL, MySQL, MariaDB, and
SQLite implement those semantics. ClickHouse rejects `json_extract` because
its available lowering cannot preserve the missing-versus-empty distinction.

`split_part` requires a nonempty literal delimiter and an integer index from 1
through 64. SQL null remains null and an absent component returns an empty
string. All five backends lower it. Both functions are additive version-1 AST
nodes; existing version-1 nodes retain their meaning.

It rejects filesystem and environment reads, network calls, random values, sequence reads, current time, arbitrary Python callables, mutable globals, unordered windows, and unknown functions.

A partial expression requires an explicit predicate and unmatched policy. A generated reconciliation statement MUST be idempotent, but idempotency does not authorize rerunning a successful migration.

The canonical expression is a backend-independent AST with inferred types, referenced columns, functions, bound parameters, determinism class, and semantic versions. Backend SQL appears only after lowering.

Version 1 preserves AST operand order. It does not algebraically reassociate operations. A later semantic version may add a rewrite only with an explicit proof covering nulls, types, collation, overflow, timezone, and backend coercion.

### Historical and current-model transitions

A transition has one source and one or more current targets. A
`historical_table(...)` source is excluded from final desired-model discovery
and may be preserved, archived, or dropped. A mapped current-model source
remains in desired schema and requires `source_snapshot=<id>`,
`on_complete="keep"`, and `on_unmatched="error"`. Its snapshot freezes the
source columns used by the one-shot mapping, while completion leaves the live
source table in place. Any other use of `keep`, or any attempt to retire a
mapped desired source, fails validation.

PostgreSQL and SQLite read the kept source under their normal transactional
lock boundary. MySQL and MariaDB atomically rename it to a reserved frozen name,
complete and verify target writes, then rename it back. This is a temporary
write barrier, not source retirement. Rollback removes verified owned target
effects and leaves the current source in place. ClickHouse rejects transitions.

A transition declares:

- stable declaration identity;
- exact source snapshot lineage;
- source identity columns;
- one or more targets with target identity, mapping, predicate, and conflict policy;
- relationship cardinality;
- coverage and overlap policy;
- source completion policy;
- rollback policy;
- execution limits.

Single-source cardinalities are `one_to_one` and `one_to_many_split`.
`many_to_one` is admitted only through the deterministic `merge_sources()`
contract below. Unstructured many-to-one mappings remain rejected.

Source identity is backed by a schema uniqueness constraint or an apply-time duplicate proof in the same consistency boundary as the write. Target identity is non-null and stable. It MUST have a database uniqueness constraint unless the backend proves an equivalent serialized conditional write.

One source identity may fan out to several declared target types. It may not create the same target identity twice inside one target.

### Deterministic merges

A merge combines two or more explicitly pinned historical sources into exactly
one current target. Every input declares its own source identity, projection,
and distinct priority. The merge declares non-null grouping keys and one rule
for every non-key output field.

The supported field rules are:

| Rule | Contract |
|---|---|
| `sum` | Sum non-null numeric contributions using the lowered backend numeric type. |
| `avg` | Average non-null numeric contributions using the lowered backend numeric type. |
| `min` / `max` | Select the ordered minimum or maximum under the plan's pinned backend semantics. |
| `count` | Count non-null expression results. |
| `require_equal` | Require every contribution, including null placement, to be equal. String equality uses UTF-8 bytes rather than the database's default collation. |
| `winner` | Select the lowest-priority source, then order by its source identity encoded as UTF-8 bytes. |

Arbitrary reducers, implicit input order, and implicit last-write wins are
invalid. Every input must project the same named fields with compatible types.
Grouping keys must exist in every projection. Source identities and grouping
keys are non-null integer or string values, and source identities are unique at
apply. Other types require an explicit cast into one of those stable families.
String grouping uses UTF-8 byte equality. The plan records
`priority_then_identity_utf8_hex_v1` as its ordering rule.

The combined input must contain at least one row by default. A declaration may
set `allow_empty=True` to acknowledge that an empty result is valid and that
source completion may still proceed. The setting is part of merge identity.

The plan retains source-to-group and group-to-target provenance. Runtime edge
records authenticate each contribution without logging raw identities. A merge
uses the same target conflict and ownership rules as a transition. A revision
that changes grouping, sources, or field rules requires a new declaration with
explicitly pinned retained sources.

MySQL and MariaDB MUST freeze every merge source under one metadata-lock
boundary before reading any source. Freezing sources one at a time is invalid
because writes between renames would create an inconsistent merge cut.

### Capture and archive

`capture` transformation rollback records the previous owned value before the
write. The capture store distinguishes a missing row, null, and a typed value,
and binds the record to the migration, epoch, declaration, target identity,
owned column, preimage, and generated postimage. Capture and mutation share the
same consistency boundary. Rollback restores a capture only while the current
owned value still matches the recorded postimage.

Archive is an explicit move to a named archive table. It is valid for missing
managed rows, unmatched transition rows, or a completed historical source only
when the declaration specifies the archive destination and acknowledgement.
The archive plan records source and archive identities, mapped columns,
ownership, conflict behavior, and rollback behavior. It writes or verifies the
archive record before removing the source record. A backend that cannot make
that sequence atomic MUST use durable intent, verification, and reconciliation.

Archive never means unscoped deletion. An archive destination is reserved by
the declaration and must not overlap a current desired model or another
declaration with incompatible ownership.

### Keyset batching

`batch(...)` may split a generated data statement into ordered keyset chunks.
Its key is a non-null unique identity and may not be changed by the statement.
The compiler proves that key against the source identity or target primary or
unique key. `OFFSET` batching is invalid.

Supported controls are chunk size, maximum declaration-attempt duration,
statement timeout, lock timeout, and maximum observed replication lag.
`max_duration` is one cooperative budget shared by every step and chunk in the
declaration attempt; a retry starts a new budget. The executor checks it before
and after SQL and between chunks. `statement_timeout` provides interruption on
backends that support it. All four limit values are finite positive seconds.
Unsupported controls fail during backend negotiation.

All chunks for one generated statement remain in the statement's existing
transaction and ownership receipt. They do not commit independently and cannot
be resumed independently. A failure rolls back the complete statement on a
backend admitted for batching. The normal operation checkpoint records the
statement effect. Global coverage, postconditions, edge recording, and source
retirement occur only after all chunks succeed.

## Coverage and conflicts

Initial coverage modes are:

| Mode | Meaning |
|---|---|
| `all` | Every source identity is assigned to at least one declared target edge. |
| `subset` | Unmatched source identities are allowed and remain untouched. |
| `exactly_once_match` | Every source identity matches exactly one raw target predicate. |
| `all_assigned_once` | Every source identity is assigned once after explicit target priority rewrites overlapping predicates. |

`on_unmatched="ignore"` or `on_unmatched="archive"` is valid only with `subset`. `all` and both exactly-once modes use `error`.

Overlap policies are:

- `error`, which rejects any raw overlap;
- `fan_out`, which permits only the explicitly listed cross-target edges;
- `priority`, which requires unique explicit priorities and is valid only with `all_assigned_once`.

The plan records raw predicates and effective predicates after priority rewriting.

Initial conflict policies are:

| Policy | Meaning |
|---|---|
| `error` | Abort before mutation when a target identity already exists, except when a verified same-migration edge proves recovery state. |
| `ignore_if_equivalent` | Accept an existing identity only when every owned value equals the declared result. |
| `overwrite` | Replace owned values after explicit CRITICAL acknowledgement and ownership validation. |

Plain `ignore` is invalid. Retry machinery MUST NOT implement `error` as ignore.

## Ownership

Every mutating declaration contains:

- owner declaration ID and revision;
- target table;
- key columns and canonical key domain;
- row scope predicate;
- owned columns;
- adoption policy;
- acknowledgement state;
- runtime ownership or identity-edge ledger reference.

A transformation owns its target columns only inside its domain. A transition owns only the target row identities and columns recorded by its verified identity edges. No declaration owns a table merely because it writes to it.

The planner compares live declarations for overlapping row and column ownership. A possible overlap fails unless a deterministic ownership order is explicit in semantics. Runtime conflicts outside declared ownership fail closed.

## Canonical IR

The compiler has these phases:

```text
discovery
validation
resolution
canonicalization
identity analysis
coverage and conflict analysis
dependency planning
safety classification
backend capability negotiation
backend lowering
artifact generation
execution
convergence
```

Each phase has a typed input and output. A phase does not reinterpret pre-canonical Python objects. Backend SQL does not exist before lowering. Execution consumes verified artifacts and recorded state; it does not rediscover live declarations.

Canonical values use NFC strings, lexicographically sorted object keys, explicit nulls, and type-preserving encodings for dates, timestamps, decimals, and finite floats. Semantic arrays define their own order. Environment-specific absolute paths are forbidden.

`canonical_bytes(value)` returns the UTF-8 strict canonical JSON encoding. `digest(value, domain)` hashes a typed array containing `dbwarden`, the domain, semantic format version, and value. Every identity domain is distinct.

### Identity derivation

Derived fields are excluded from their own preimages. Derivation order is:

1. Canonicalize declaration semantics without derived IDs, checksums, probe evidence, timestamps, or runtime state.
2. Compute the declaration semantic checksum.
3. Compute transition ID from database identity, declaration ID, and declaration semantic checksum.
4. Compute each data operation ID from its kind and canonical declaration payload. Dependency IDs are excluded. Derive execution-step IDs from the parent operation ID, direction, and local step index. The journal scopes those IDs by migration and epoch.
5. Resolve dependency references to operation IDs.
6. Compute the complete plan and artifact manifest checksums.

String concatenation is not a hash encoding. All tuples and arrays use canonical typed encoding.

The identity model distinguishes:

- declaration ID;
- declaration semantic checksum;
- transition ID;
- migration ID;
- operation ID;
- static edge definition ID;
- runtime edge instance hash;
- run ID and application epoch.

An edge definition ID hashes transition ID, source key definitions, target reference, target key definitions, mapping, and predicate. It contains no row value.

An edge instance hash uses HMAC-SHA-256 over versioned typed source-key and target-key tuples. The checkpoint stores its `key_id` and encoding version. Reconciliation keys remain available for the journal retention lifetime.

## Snapshot lineage

Generation resolves a historical source to one exact snapshot. The frozen lineage contains:

- database identity;
- globally unique snapshot ID within that database;
- schema checksum;
- optional data-declaration checksum;
- creating migration ID;
- canonical parent snapshot IDs;
- snapshot format version.

Branch label, creation time, storage URI, and import provenance are registry metadata. They do not affect transition semantic identity.

Authoring may omit `snapshot=` to request the latest compatible snapshot on one
unambiguous registry lineage. Compilation walks the single lineage head,
selects the newest snapshot containing the source table with the requested
schema, and freezes that concrete snapshot ID and lineage. Multiple heads,
multiple newest compatible candidates, a missing source, or malformed lineage
fails and requires an explicit snapshot ID. `latest_previous` is not a magic
snapshot name. Execution never searches again or substitutes another snapshot.
A missing or corrupt bundle member is an integrity failure.

The registry rejects duplicate IDs, unknown or cyclic parents, invalid checksums, database or backend mismatch, and ambiguous matches. An operating-system file lock serializes registry updates before atomic publication; migration-driven registration also runs under the database migration lock. Offline imports need no database connection. Read-only resolution does not create directories or files.

The implementation API is:

```text
resolve_snapshot(
    snapshot_id,
    database,
    backend,
    registry_path=None,
    schema_dir=None,
)
```

It returns the frozen lineage plus `schema_state` after verifying the existing core schema-snapshot checksum.

## Planning and ordering

Schema and data operations form one dependency DAG. It includes target creation, intermediate column creation, transfers, transformations, validation, constraint enforcement, source preservation, and destructive work.

For a non-null derived column on an existing table, the planner either emits:

1. a safe nullable or equivalent intermediate column;
2. backfill;
3. validation;
4. constraint enforcement;

or fails and requests an explicit staged release.

A destructive operation depends on all reads from the object, all transfers, coverage validation, convergence checks required by policy, and any preservation operation. Data transfer never follows dependent drop or rename work.

The planner topologically sorts the DAG before lowering. `operation_id` is the deterministic tie-breaker. Lowering and execution preserve this order across transaction boundaries. They MUST NOT regroup all transactional statements before all autocommit statements.

A cycle error includes the concrete operation path. It may recommend a multi-migration release only when a valid intermediate state can break the cycle. If one transition spans several files, a release record links their migration IDs and states.

## Review evidence and safety

The plan has separate `semantics` and `review_evidence` sections.

Semantic identity and SQL depend only on `semantics`. Live probes may populate evidence, thresholds, severity, and guards, but they never change mappings, expressions, policies, identity, or SQL shape.

Evidence records probe ID, canonical query scope, database identity, isolation level, observation time, values, accepted thresholds, and checksum. The final artifact manifest covers the evidence reviewed by the operator.

Generation-time evidence is advisory at apply. Apply reruns required probes. Transactional plans run probes and writes in one documented isolation boundary, with constraints or locks where needed. Non-transactional plans use conditional writes, durable intent, per-operation verification, and final convergence.

Destructive behavior requires both declaration-specific acknowledgement and the normal operator acknowledgement such as `--force`. A stale acknowledgement whose semantic checksum no longer matches is invalid.

## Backend capability negotiation

Lowering evaluates the complete plan for:

- atomic DML and metadata commit;
- transactional DDL;
- ordered mixed transactional and autocommit execution;
- conditional write and equivalence support;
- durable operation checkpoints;
- synchronous effect visibility;
- source preservation support;
- convergence-query support.

One autocommit, external, or asynchronous operation makes the affected execution segment non-transactional. Backend name alone never proves atomicity.

### Initial capability matrix

| Capability | PostgreSQL | MySQL and MariaDB | SQLite | ClickHouse |
|---|---|---|---|---|
| Support level | Production | Backend-observed lifecycle subset; crash and version gates open | Development, CI, sandbox | Implemented limited subset; live conformance pending |
| Managed rows | Full initial policies | Full initial policies with journal | Full initial policies | Insert and synchronous update mutations; irreversible rollback only; scoped deletion rejected |
| Transformations | Full initial expressions | Full initial expressions with journal as required | Supported expression subset | Synchronous `ALTER ... UPDATE` mutations; irreversible rollback only |
| One-to-one transition | Full | Full with edge checkpoints | Full | Rejected; source-write barrier unavailable |
| Split | Full | Full with per-target checkpoints | Full | Rejected; source-write barrier unavailable |
| Deterministic merge | Full after source locks | Full only with atomic multi-source freeze and journal | Full | Rejected; source-write barrier unavailable |
| Captured transformation rollback | Full | Full with transactional capture/write receipt | Full | Rejected |
| Archive | Full when archive/write/delete share the transaction | Journaled archive receipt before source removal | Full | Rejected |
| Keyset batching | Transactional chunks, PostgreSQL limits | Transactional DML chunks; backend-specific limits | Transactional chunks; no replication-lag control | Rejected |
| `ignore_if_equivalent` | Yes | Yes | Yes | Historical transitions unsupported |
| `overwrite` | Acknowledged and owned values only | Same, journaled | Same | No |
| Source drop | After same-boundary validation or verified journal state | After source freeze and verified journal state | Yes | Historical transitions unsupported |
| Retry after uncertainty | Reconcile first | Reconcile first | Reconcile first | Reconcile first, never blind retry |

MySQL and MariaDB require separate real-backend evidence. MySQL 8.4.11 and
MariaDB 11.4.4 each passed the six lifecycle flows described above; neither
backend inherits the other's result. Their crash and broader-version gates
remain open. ClickHouse uses `mutations_sync = 2`, probes `system.mutations`
before a checkpoint becomes `DONE`, and stores append-only run and checkpoint
versions in `ReplacingMergeTree` tables. Unit tests cover generated native SQL
and an instrumented ClickHouse connection; no live ClickHouse conformance run
has been completed. Asynchronous mutations, historical transitions,
deterministic merges, archive, capture rollback, batching, overwrite, and
scoped managed-row deletion remain outside the ClickHouse subset.

MySQL and MariaDB rename a transition source into the reserved migration namespace before capturing rows. This waits for existing table users and prevents later writes through the original name. Rollback restores the source name only after owned target cleanup. ClickHouse's Atomic engine performs rename without waiting for existing queries, so rename alone cannot establish that source boundary. Historical transitions fail planning until a supported write barrier exists. See the backend documentation for [MySQL rename and metadata locking](https://dev.mysql.com/doc/refman/8.4/en/rename-table.html) and [ClickHouse Atomic rename](https://clickhouse.com/docs/engines/database-engines/atomic#rename-table).

## Artifact set

Every data migration has:

```text
<migration>.sql
<migration>.plan.json
<migration>.data.py
```

Inline CSV and JSON inputs are normalized into the frozen `DATA_SPEC`; they do not add copied source files beside the bundle. Any separately retained source artifact is an ordered manifest member with its media type, byte length, and checksum.

The SQL file is executable output. The plan is the machine-readable semantic and execution contract. The frozen Python file is human-readable audit data and is never an execution input.

The artifact manifest contains format version, migration ID, semantic checksum, and ordered members. Every member has normalized relative name, media type, byte length, and SHA-256. The manifest checksum is the domain-separated digest of the canonical manifest without its checksum field. The plan's embedded `data_bundle` field is excluded from the plan component preimage to avoid self-reference.

Generation writes members to staging and publishes the valid manifest last. Discovery ignores incomplete sets. Verification occurs before plan, baseline, apply, retry, reconcile, audit, rollback, and convergence. Applied history stores the manifest checksum.

The implementation APIs are:

```python
build_manifest(sql_content, plan, frozen_content)
verify_bundle(sql_path, plan=None)
```

Verification requires all three core members, checks every component, statically reads the frozen artifact, verifies its guard migration ID, and compares canonical `DATA_SPEC` with `plan["data_spec"]`.

## Frozen audit artifacts

The generated audit file imports and immediately calls `guard_frozen_data_artifact(migration_id)`, then assigns one literal `DATA_SPEC`. It contains no callbacks or executable migration logic.

Normal import raises `FrozenDataArtifactImportError`. Intentional Python audit tooling may use the context-local `allow_frozen_data_imports()` context manager. The process-wide environment override is disabled unless its value is exactly `1`. Every allowed import emits an audit warning.

Normal audit uses `read_frozen(path)`, which parses the AST, requires the guard, permits only the generated import, guard call, and literal assignment, and never imports the file.

## Public implementation API

The supported authoring surface is exported from `dbwarden.data`:

```text
rows(*, key=None, rows=None, source=None, owned_columns=None,
     on_missing="keep", scope=None, acknowledge_delete=False,
     archive_to=None, acknowledge_archive=False,
     rollback="irreversible", category=None)

derive(target, expression, *, rollback, when=None,
       on_unmatched="error", max_rows=None, category=None,
       settings=None, execution=None)

historical_table(table, *, snapshot=None, schema=None)
archive_table(table, *, schema=None)
into(model, *, map, key=None, where=None,
     on_conflict="error", acknowledge_overwrite=False, priority=None)
validate(expression, *, message="Data validation failed")

from_source(source, *, identity, map, priority)
aggregate(policy, expression)
winner(expression)
merge_sources(*inputs, key, values, allow_empty=False)

batch(*, size, key, max_duration=None, statement_timeout=None,
      lock_timeout=None, max_replication_lag=None)
```

`DataMeta` owns `managed_rows`, `transformations`, and `validations`.
`DataTransition` owns the source, source identity, targets, coverage, unmatched,
overlap, completion, rollback, maximum-row, category, and execution policies.
A merged source is assigned to `DataTransition.source`; its grouping key becomes
the default transition source identity.

The stable artifact and execution helpers are:

```text
resolve_snapshot(snapshot_id, *, database, backend,
                 registry_path=None, schema_dir=None)
register_snapshot(snapshot_id, schema_state=None, *, database=None,
                  backend=None, parent_snapshot_ids=(), registry_path=None,
                  data_checksum=None, created_by_migration=None)
build_manifest(sql_content, plan, frozen_content)
verify_bundle(sql_path, plan=None)
render_frozen(data_spec, migration_id)
read_frozen(path)

execute_data_plan(connection, plan, *, migration_id,
                  direction="upgrade", record_success=None,
                  baseline=False, baseline_reason=None, reapply=False,
                  before_statement=None, after_statement=None)
reconcile_data_plan(connection, migration_id, *, apply=False,
                    decision=None, plan=None)
```

`execute_data_plan` assumes the caller holds the shared migration lock. Normal
application uses `execute_migration_bundle`, which verifies the frozen bundle,
connects schema history recording, and forwards fencing and progress callbacks.
Direct callers must preserve those responsibilities.

## End-to-end lifecycle

1. Discovery imports trusted live project declarations from `model_paths` and
   `data_paths`. Frozen migration files are excluded.
2. Validation rejects ambiguous identities, unsupported policies, overlapping
   ownership, incomplete expressions, unsafe archive/capture rules, and invalid
   batch or merge declarations.
3. Resolution loads each exact snapshot and static row source. Static rows are
   normalized and embedded. Snapshot lineage is frozen.
4. Canonicalization creates the versioned data specification and content-based
   declaration checksum. Identity analysis derives declaration, transition,
   operation, step, and edge-definition identities without self-reference.
5. Planning combines schema and data operations in one dependency DAG. Backend
   negotiation rejects any operation whose transaction, lock, receipt,
   preservation, or convergence requirements cannot be met.
6. Lowering emits ordered upgrade and rollback steps, guards, lock tables,
   ownership proofs, edge metadata, batch descriptors, and recovery behavior.
7. Generation stages SQL and frozen audit data, computes the manifest, and
   publishes the plan last. A dry run stops before publication and mutation.
8. Apply acquires the normal migration lock, verifies the bundle and backend
   settings, creates or reads journal state, and reruns required guards.
9. Transactional backends execute guards, writes, edge recording, and schema
   history in one boundary. Nontransactional segments persist intent before a
   possible effect and verify each durable effect before proceeding.
10. Success commits schema history and `APPLIED_SUCCESS` together where the
    backend permits it. A checksum-matched repeat performs no migration writes.
11. A known rolled-back failure becomes retryable. A possible durable effect
    becomes `UNKNOWN_REQUIRES_RECONCILIATION` and blocks apply.
12. Reconciliation verifies checkpoints, ownership receipts, authenticated
    edges, and current postconditions. Only explicit `verified_retry` or
    `abandon` decisions allowed by the current state can change it.
13. Rollback verifies current ownership and postimages before reversing effects.
    A successful rollback records `ROLLED_BACK`; later application requires an
    explicit linked reapply epoch.
14. `check --data` and `diff --data` compare current desired declarations with
    actual owned values, retained sources, archives, captures, merge provenance,
    and applied frozen history.

## Execution journal

Execution state uses three logical stores:

1. An immutable artifact record keyed by migration ID and manifest checksum.
2. An append-only event journal identifying each run and application epoch, with a current-state projection for inspection.
3. Durable operation checkpoints and authenticated identity-edge instance records. SQL backends update checkpoint state in place; ClickHouse appends checkpoint versions.

A current-state projection may exist for status queries. It does not replace history.

Migration states are:

```text
NOT_STARTED
APPLYING
FAILED_RETRYABLE
FAILED_FINAL
UNKNOWN_REQUIRES_RECONCILIATION
APPLIED_SUCCESS
ABANDONED
ROLLING_BACK
ROLLED_BACK
```

Normal transitions are:

```text
NOT_STARTED -> APPLYING
APPLYING -> APPLIED_SUCCESS
APPLYING -> FAILED_RETRYABLE
APPLYING -> FAILED_FINAL
APPLYING -> UNKNOWN_REQUIRES_RECONCILIATION
UNKNOWN_REQUIRES_RECONCILIATION -> FAILED_RETRYABLE
UNKNOWN_REQUIRES_RECONCILIATION -> FAILED_FINAL
FAILED_RETRYABLE -> APPLYING
APPLIED_SUCCESS -> ROLLING_BACK -> ROLLED_BACK
```

Baseline records an `APPLIED_SUCCESS` run with mode `baseline`. It first proves convergence or stores explicit operator acknowledgement, reason, and skipped or failed checks.

`APPLIED_SUCCESS`, `ROLLED_BACK`, `FAILED_FINAL`, and `ABANDONED` are terminal inside one application epoch. Reapply after rollback creates a new explicit epoch and references the prior epoch. Normal discovery never reruns a migration merely because it was rolled back.

Every run records migration ID, manifest checksum, epoch, run ID, attempt number, state transitions, timestamps, current operation, completed operations, failure, reconciliation evidence, and operator decisions.

### Non-transactional protocol

Before a non-transactional statement, DBWarden commits a `PENDING` checkpoint. It applies the effect, verifies it, and commits `DONE`. Each frozen execution step has a distinct identity within its operation and direction, so statement indexes cannot collide across transition targets. A crash after the possible effect and before verification produces unknown state.

MySQL and MariaDB ownership inserts require InnoDB tables and disabled autocommit. The affected-row count must match the frozen plan's expected count. The inserted rows and a `WRITE_VERIFIED` checkpoint commit in the same DML transaction before later value checks mark the statement `DONE`. Count mismatches are terminal failures. A `PENDING` ownership insert cannot be recovered from equivalent target values alone, because those values may belong to an application writer.

Generated rollback steps containing only DML first persist their intent checkpoints, then lock the target rows with `SELECT ... FOR UPDATE`. Identity verification, guards, writes, afterguards, and `DONE` checkpoint updates share that transaction. DDL occupies separate steps. Preserved-source restoration runs only after target cleanup succeeds.

ClickHouse does not claim insertion ownership. Managed-row scoped deletion is unsupported without an atomic insertion-ownership receipt, and historical transitions are rejected without a supported source-write barrier.

Minimum checkpoint fields are migration ID, manifest checksum, epoch, run ID, operation ID, transition ID where applicable, edge definition ID, source and target edge instance hashes where applicable, state, record checksum, key ID, and encoding version.

Runtime journal rows cannot be covered by a generation-time migration checksum. The manifest covers journal schema and protocol versions. Each runtime record has its own checksum or authenticated hash and artifact-manifest reference.

Automatic retry is allowed only when the whole attempted segment rolled back atomically or reconciliation proves every completed effect and the next action. A stale `APPLYING` record whose lock is gone becomes unknown.

## Rollback

Rollback is a state-changing run with its own journal. It is conditional on current ownership and state.

For managed rows:

- delete only a row created by the migration and unchanged from its recorded postimage;
- restore a value only from a previous DBWarden desired state or an explicitly captured preimage;
- do not delete an adopted row;
- stop for reconciliation when current owned values no longer match the recorded postimage.

For transformations, `clear` is valid only for an owned nullable target and only while the current value matches the generated postimage. `recompute` restores the previous frozen desired expression from retained inputs after checking the current expression's postcondition. It requires a previous declaration with compatible ownership and domain; it does not reconstruct arbitrary pre-migration values. `capture` restores the authenticated typed preimage recorded in the same consistency boundary as the original write, again only while the current value matches the recorded postimage. Generic inverse reconstruction remains unsupported.

For transitions, `restore_preserved_source` restores the recorded fully qualified preserved source name and reverses only ledger-owned target effects. It does not generically drop target tables. A target table may be dropped only when the same migration created it and verification proves that it has no unowned rows or dependent objects.

Preserved names include a collision-resistant transition or migration suffix and are recorded in the plan. Registered preserved internal tables are excluded from desired-schema diff but remain visible to audit and convergence.

Archive rollback first verifies the archive receipt and the absence of a
conflicting live row. It restores only the row and columns owned by the archived
declaration, then removes only its own unchanged archive record. An archive
record changed or adopted by application code stops rollback for reconciliation.

An irreversible migration causes rollback to fail clearly. A comment or empty SQL section is not successful reversal.

## Convergence

Convergence compares current canonical desired state with actual state. It does not regenerate or replay old migration code.

Checks cover:

- managed-row keys and owned values inside scope;
- transformation violations inside domain;
- expected transition target identities;
- coverage of retained sources;
- duplicate target identities;
- conflict-equivalence claims;
- merge group values, deterministic winner evidence, and contribution edges;
- archive receipts, archive values, and source absence required by policy;
- capture preimage/postimage records needed for reversible history;
- foreign keys and constraints;
- preserved-source and ownership-ledger consistency.

`check --data` reports each declaration separately and redacts row values by default.

A captured transformation is a frozen one-shot write. Convergence verifies its
authenticated ledger and recorded postimage against the current target. It
does not reevaluate the expression or domain against post-write rows, because
self-references and target-dependent domains can change meaning after the
write.

Sandbox execution requires an explicit fixture, sanitized clone, or separately approved data snapshot for historical source rows. With only a schema snapshot, sandbox proves SQL syntax and empty-state schema convergence. It does not prove production coverage, conflicts, row limits, or performance.

## CLI contract

The initial safety-critical commands are:

```text
dbwarden data-transition validate
dbwarden data-transition plan
dbwarden data-transition dry-run
dbwarden data-transition render
dbwarden data-transition audit
dbwarden make-data-migration
dbwarden make-migrations
dbwarden migrate --dry-run
dbwarden migrate
dbwarden data-transition reconcile
dbwarden diff --data
dbwarden check --data
```

Dry run resolves and reports one exact snapshot, validates the canonical IR, runs allowed read-only probes, negotiates backend capabilities, renders the operation DAG, and mutates no file, metadata, or database row.

Non-interactive execution never prompts. Ambiguous mappings, snapshots, policies, and acknowledgements fail. JSON output uses the same versioned plan contract as human output.

Interactive scaffolding, model descriptions, and generated Markdown are useful product features but are not prerequisites for execution safety. Generated documentation is never a discovery source.

## Security model

Live declarations are trusted project Python code. Import-time validation occurs after Python import side effects and is not a sandbox. The trust boundary includes code review, the process, credentials, and database permissions.

The compiler pipeline is a correctness and mutation-safety boundary. It rejects unsupported expressions, ambiguous ownership, and unsafe operations. Frozen artifacts use static parsing by default. Plans and logs redact raw row values unless local debugging explicitly requests them.

## Required verification

The production contract is incomplete until it passes:

- canonical hash golden vectors and self-preimage exclusion tests;
- identity domain, typed-key, HMAC key-rotation, and collision cases;
- branch, squash, import, corrupt, missing, and ambiguous snapshot cases;
- ownership overlap, adoption, scoped deletion, and later-write rollback cases;
- coverage and overlap truth tables;
- merge grouping, aggregate null/type behavior, winner ordering, and ambiguous-contribution cases;
- archive write-before-delete, adoption, conflict, rollback, and interrupted-move cases;
- capture missing/null/value, postimage conflict, tamper, and rollback cases;
- keyset uniqueness, null, non-advancing key, timeout, lag, mid-chunk failure, and full-statement rollback cases;
- concurrent conflict and probe-to-write race tests;
- unified DAG and mixed transaction/autocommit order tests;
- artifact member tamper and publication-crash tests;
- failure injection before effect, after effect, before verification, and before success metadata;
- reconciliation tests that keep `error` conflict semantics;
- apply, rollback, ordinary migrate, and explicit reapply epoch tests;
- real PostgreSQL, MySQL, MariaDB, SQLite, and limited ClickHouse conformance runs;
- convergence after applying history to an empty database with suitable source fixtures;
- compatibility tests proving existing schema migrations and seed behavior are unchanged.

Tests assert that arbitrary merge reducers, implicit merge winners, independently committed batch chunks, cross-database operations, execution-scoped expressions, and plain-ignore declarations fail with stable unsupported-capability diagnostics. Backend-specific unsupported combinations, including all four advanced operations on ClickHouse, fail before an applicable artifact is emitted.

Repeated apply after success executes zero mutation statements. Journal and integrity inspection reads remain necessary. It is not tested by allowing idempotent mutation SQL to run again.

## Completion criteria

The production contract is complete only when:

1. Managed rows, transformations, validations, transitions, deterministic merges, archive, capture, and batching compile through the same typed IR and unified DAG.
2. Identity hashes are acyclic, domain-separated, and stable across processes.
3. Historical sources resolve to exact, checksummed lineage with no apply-time fallback.
4. Every mutation has enforceable ownership and scope.
5. Every artifact set has a verified manifest covering SQL, plan, frozen audit data, lineage, and frozen static sources.
6. Successful execution occurs once per epoch and every attempt remains auditable.
7. Non-transactional backends cannot retry without durable checkpoints or reconciliation.
8. Rollback preserves prior success history and touches only proven owned effects.
9. Schema and data operations retain dependency order through execution.
10. Unsupported backend or operation combinations fail closed.
11. `check --data` proves or reports current managed-data convergence.
12. Existing migrations and seeds retain their documented semantics.
13. Merge contributions and winners are deterministic and retain authenticated provenance.
14. Archive and capture rollback refuse changed or unowned state.
15. Batched statements preserve the unbatched result and roll back as one statement effect.

Shipping compiler preview, generated SQL, or green unit tests without the journal, ownership, manifest, rollback, and real-backend evidence does not satisfy these criteria.
