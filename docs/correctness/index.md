# Correctness

dbwarden generates SQL automatically. This section explains the mechanical checks that make that safe: how dbwarden proves the generated SQL matches the declared model state, how it prevents noisy diffs, how it warns before risky changes, and how rollback SQL is produced under a strict contract.

The claim is not "trust the tool because it is convenient". The claim is stronger: dbwarden exposes a chain of independent checks. Each check catches a different class of failure. Together they make generated migrations reviewable, reproducible, and suitable for CI enforcement.

Start with the [convergence gate](convergence-gate.md). It is the single most powerful correctness check because it tests the full migration history against an empty database and verifies that the resulting schema matches the models.

## Chain of Trust

```text
Models
  |
  v
Canonical model state
  |
  v
Deterministic diff
  |
  v
Typed operations in a migration plan
  |
  +--> Safety classifier
  |
  v
Backend-native SQL emitters
  |
  v
Plain SQL migration file
  |
  +--> Strict rollback contract
  |
  v
Apply to database
  |
  v
Extract schema snapshot
  |
  v
Convergence and round-trip verification
```

Each stage has a different responsibility. Canonicalization removes representation noise. Diffing decides what changed. SQL emitters turn typed operations into backend-native SQL. Safety classification highlights risky operations before execution. Rollback generation checks whether the reverse path is executable or explicitly irreversible. Convergence checks prove that the final database state matches the model state.

## Defense in Depth

### Convergence Gate

The convergence gate applies the complete migration history to an empty database, extracts the resulting schema, and compares it with the current model specification. The gate passes only when there is zero schema drift.

This catches errors that unit tests can miss, such as a migration file that forgot an index, a model rename that was emitted as a drop and add, or a rollback edit that accidentally changed the upgrade section. See [Convergence Gate](convergence-gate.md).

### Round-Trip Verification

Round-trip verification checks extract and emit consistency. dbwarden reads a real database with `generate-models`, emits models, generates migrations from those models, and verifies that extraction after apply produces the same schema shape.

This is especially important for backend-specific features such as PostgreSQL identity columns, partitioning, exclusion constraints, ClickHouse engines, projections, skip indexes, and RBAC objects. See [Round-Trip Verification](round-trip-verification.md).

### Deterministic Diff

dbwarden canonicalizes model specs and database snapshots before comparing them. The same inputs produce the same diff every time.

Canonicalization prevents representation noise from turning into fake migrations. For example, PostgreSQL may report `character varying(255)` while a SQLAlchemy model says `String(255)`. ClickHouse may report engine metadata in an order that differs from the declaration. dbwarden normalizes those forms before diffing. See [Deterministic Diff](deterministic-diff.md).

### Safety Classifier

The safety classifier scans migration plans and identifies risky changes before execution. It distinguishes safe changes from warnings and blocking changes, then requires explicit acknowledgement for risky operations.

The goal is simple: dbwarden should never silently drop data. See [Safety Classifier](safety-classifier.md).

### Offline Integrity

Offline mode lets CI generate migrations without a live database by using a checked-in model state file. Schema snapshots and model state files make migration generation deterministic and protect the pipeline from accidental drift in a developer database.

Offline integrity is not a replacement for a live convergence gate. It is the deterministic first half of the workflow. See [Offline Integrity](offline-integrity.md).

### SQL Generation

dbwarden does not hide runtime logic inside migration files. It emits plain SQL. The diff becomes typed operations, operations are ordered, and backend-specific handlers render native SQL for PostgreSQL, MySQL, SQLite, MariaDB, and ClickHouse.

The output is designed for review. See [SQL Generation](sql-generation.md).

### Rollback Generation

Rollback SQL is generated with the upgrade SQL under a strict contract. Executable rollback SQL is accepted. Placeholder rollback is refused by default. Operations that cannot be rolled back automatically must be explicitly declared irreversible.

This means rollback correctness is not an afterthought and not a manually maintained parallel file. See [Rollback Generation](rollback-generation.md) and [Rollback Coverage Matrix](rollback-coverage-matrix.md).

## Recommended Reading Order

1. [Convergence Gate](convergence-gate.md)
2. [Deterministic Diff](deterministic-diff.md)
3. [SQL Generation](sql-generation.md)
4. [Rollback Generation](rollback-generation.md)
5. [Safety Classifier](safety-classifier.md)
6. [Offline Integrity](offline-integrity.md)
7. [Round-Trip Verification](round-trip-verification.md)

The pages are independent, but the trust model is cumulative. The convergence gate proves the final database shape. Deterministic diffing explains why generated changes are stable. SQL generation explains why the emitted file is backend-native and reviewable. Rollback generation explains the reverse path. Safety classification and offline integrity make the workflow suitable for CI.
