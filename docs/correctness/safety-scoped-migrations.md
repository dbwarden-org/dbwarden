# Safety-scoped migrations

Generate every intended change, then choose which files may run during a deployment.
Severity controls scheduling. It does not prove that SQL is safe for production data,
replace backups, or remove acknowledgement requirements.

## Generate and deploy

```bash
dbwarden make-migrations --database primary --split-at-severity WARN
dbwarden migrate --database primary --max-severity INFO
# Later, after reviewing deferred SQL:
dbwarden migrate --database primary --max-severity CRITICAL --force
```

The first command produces consecutive versioned files when both groups contain
operations:

```text
primary__0007_update_users.sql
primary__0008_update_users__deferred.sql
```

The base contains operations below the split threshold. The deferred file contains
operations at or above it and every operation that depends on them. Each file has
its own upgrade, rollback, and plan. If either group is empty, only the other file
is written. A threshold of SAFE therefore puts every operation into one deferred
file. Repeatables cannot be split; combining a split threshold with `--type ra`
or `--type roc` is a usage error.

The partition preserves statement ordering, checks explicit operation dependencies,
and adds dependencies for table/column changes, indexes, constraints, foreign keys,
schemas, and view references. Cycles, unknown dependency IDs, unsupported emitters,
and dependencies incompatible with statement ordering fail before writing files.
Plugins must declare dependencies that cannot be inferred from these attributes.

## One severity model

Generation and static classification call
`dbwarden.engine.safety.classifiers.classify_operation`. Execution and status read
the resulting plan; they do not classify SQL at deployment time.

| Level | Examples |
|---|---|
| SAFE | Read-only SELECT; PostgreSQL type changes classified SAFE by its type table |
| INFO | Create table; nullable column without volatile default; concurrent PostgreSQL index |
| WARN | Rename; SET NOT NULL; non-concurrent PostgreSQL index; unbounded DML |
| CRITICAL | Drop table/column; TRUNCATE; lossy ClickHouse rebuild; safe-type contract |
| UNKNOWN | Missing, malformed, stale, unsupported, or inconsistent plan |

PostgreSQL narrowing and cross-family conversions use its existing type classifier.
ClickHouse column/option changes use its backend tables. Bounded DML classification
recognizes a column equality or range predicate structurally. It cannot prove that
the column is indexed, unique, or selective.

Existing `operations[].severity` values remain INFO, WARNING, and ERROR for legacy
readers. The new `severity` block uses SAFE, INFO, WARN, CRITICAL, and UNKNOWN.
Unsupported operation kinds never default to INFO.

## Plans and effective state

Plans retain legacy fields and add:

```json
{
  "schema_version": "1.1",
  "backend": "postgresql",
  "content_hash": "sha256:...",
  "base_checksum": "...",
  "target_checksum": "...",
  "upgrade_ops": [],
  "severity": {
    "file": "WARN",
    "provenance": "generated",
    "split": {"threshold": "WARN", "role": "deferred", "paired_with": "0007"},
    "ops": [{"id": "op:1", "kind": "alter_column_nullable", "severity": "WARN"}]
  },
  "required_flags": ["--force"]
}
```

The example abbreviates typed operations. Generated operations contain full emitter
attributes, rollback attributes where required, dependencies, and checked state
changes. IDs remain stable across both files. The content hash covers the complete
SQL file with line endings normalized to LF. Editing headers, upgrade, or rollback
invalidates trust. State checksums exclude timestamps and generation bookkeeping.

Generation starts from the latest applied snapshot, then composes pending typed
plans in version order. It excludes applied and superseded files. Config-defined
plugin objects participate through their registered handlers. A reconciliation
plan participates once as the replacement for the superseded branch files.
The legacy SQL preview helper retains a warned fallback for old planless files;
normal generation never uses SQL replay to compose pending state.

A pending deferred change is not emitted again. Deleting a pending deferred file
and its plan lets generation emit the missing change again. Deleting its prerequisite
while keeping the deferred plan causes a stale-base error. A model edit that
contradicts pending deferred state requires applying or superseding that file first.

Planless pending files warn that effective state is incomplete. `--strict-pending`
refuses them. A trusted plan whose base state no longer matches fails closed in
both modes. Generated model state stores the generation baseline separately from
the desired target; generation does not claim an unapplied target is an applied
database snapshot.

## Execution and exit codes

Versioned migrations run as a strict prefix. The first file above the ceiling
stops execution before its SQL. Later versions and repeatables do not run.
Applied prefix files retain their history entries. The existing lock remains held
through the partial run and releases through normal cleanup.

| Exit | Meaning |
|---|---|
| 0 | Completed, no pending work, or successful dry run |
| 3 | Successful versioned prefix followed by severity deferral |
| 4 | `check --write-plan` left unresolved files |
| Other existing nonzero codes | SQL, lock, checksum, configuration, or policy failures |

The existing optional-database skip result also uses exit 3. With `--all`, JSON
distinguishes skipped databases and severity deferrals. Other databases continue
after a deferral; real failures take precedence.

`--count` and `--to-version` bound the candidate set before the ceiling.
`--force` acknowledges operation risks but never raises the ceiling.
`--dry-run` reports the stop without DDL or a deferred exit code.
`--baseline` records history only, without executing SQL or applying a ceiling.
Backup and sandbox options retain their normal execution paths.

Repeatables above the ceiling warn on every skipped run. Eligible repeatables
run normally if no versioned file caused a stop. Changed-only repeatables that
have not changed remain outside the candidate set.

UNKNOWN blocks under SAFE, INFO, or WARN. CRITICAL is the uncapped default and
admits UNKNOWN, subject to existing preflight policies. Applied historical files
are not reclassified. There is no per-file severity override.

## Adopt existing SQL

```bash
dbwarden check --write-plan --all --database primary
dbwarden check --write-plan 0008 --database primary
```

Classification is static and whole-file. Every upgrade statement must map before
a trusted static plan is written. Generated plans are never overwritten. Matching
trusted plans are unchanged; stale static plans are reclassified. Review the
CRITICAL list and resolve the UNKNOWN list before enabling a capped deployment.

PostgreSQL syntax is validated by pglast/libpg_query (PostgreSQL 18 grammar).
SQLGlot supplies dialect ASTs for statement mapping and for MySQL, MariaDB,
ClickHouse, and SQLite. Both dependencies are pinned to reviewed version ranges.
They replace regex-based risk guesses; neither executes SQL or connects to a database.

| Engine | Grammar coverage | Classification boundary |
|---|---|---|
| PostgreSQL | Native full statement grammar | Conservative mapped subset; unmapped ASTs remain UNKNOWN |
| MySQL / MariaDB | Core DDL/DML, grade B target | Exotic ALTER options and executable comments remain UNKNOWN |
| ClickHouse | DDL/DML dialect, grade B target | Unsupported engine/ALTER forms remain UNKNOWN |
| SQLite | Minimal ALTER surface, grade C target | Unsupported rebuild scripts and statements remain UNKNOWN |

Parser grammar coverage does not claim complete classification coverage. DO blocks,
dynamic SQL, function/procedure bodies, opaque calls, and nested data modification
remain unresolved. Unsupported syntax and SQLGlot fallback Command nodes fail closed.
Static type alterations without previous type metadata also remain UNKNOWN.

## Safe type changes

`--safe-type-change --split-at-severity WARN` expands with a nullable temporary
column in the base and defers the contract. The base includes a backfill instruction.
Backfill and application dual writes remain operator work.

The contract locks the table, verifies populated temporary values and lossless
round trips, then drops/renames and restores defaults, nullability, and comments.
Its rollback recreates and repopulates the old column. Columns with keys, indexes,
constraints, identity/generated metadata, or unsupported backend semantics are
refused before writing; those require an explicit migration preserving dependencies.
This workflow does not itself guarantee zero downtime.

## Merge and reconciliation

`merge --dry-run` computes the same artifacts without writing or superseding files.
Merge checks hand edits before writing, preserves colliding branch filenames for
audit, and routes reconciliation through the shared typed writer and splitter.
Records include the merge-base commit, version and checksum, superseded filenames
and versions, all reconciliation versions, and per-environment status.

Dirty persistent environments use `reconcile`, which generates real SQL from live
state to the merged target. It uses the migration runner, verifies convergence,
records the normal reconciliation versions as satisfied, and updates the environment
record while the existing runner lock is held. `rebase --dry-run` inspects disposable
recovery; non-interactive mutation requires `--yes`.

## Status and observability

Plugins can register named migration groups alongside base/deferred and assign
operations or emitted statements to them. Group order controls file allocation;
dependencies and severity thresholds can move operations later. Custom groups do
not add severity levels or change execution ceilings. Plans record category
metadata; `status` exposes the category in table and JSON output. See
[plugin API](../plugins/developing/object-plugins.md#safety-and-migration-categories).

`status` displays plan severity, deferred files, and later blocked files. JSON
includes the blocking version, trust reason, and deferred age in seconds. Age is
based on SQL file mtime; copying files or checking out a repository can reset it.

Structured logs emit `migration_deferred` with database, ceiling, stopping file,
file severity, and remaining pending count. Repeatable skips emit
`repeatable_skipped_severity`. Optional Prometheus metrics record deferral counts
and per-version deferred age. A severity stop does not invoke failure hooks.

## Configuration

```python
database_config(
    database_name="primary",
    database_type="postgresql",
    database_url_sync="postgresql://localhost/app",
    split_at_severity=None,
    max_severity="CRITICAL",
    strict_pending=False,
)
```

These exact defaults keep splitting off and execution uncapped. CLI values override
database settings; `--no-strict-pending` disables a configured strict-pending policy.
UNKNOWN is not a selectable threshold or ceiling.
