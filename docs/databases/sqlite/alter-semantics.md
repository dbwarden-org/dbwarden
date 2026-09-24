# ALTER semantics

SQLite ALTERs behave differently from every other database dbwarden supports. The native grammar is deliberately minimal, the schema is stored as plain text that ALTER rewrites, and DDL is fully transactional. Understanding these properties explains why dbwarden emits a native `ALTER TABLE` for some changes and a full table rebuild for everything else.

## Why SQLite ALTERs are special

Three properties set SQLite apart:

1. **A minimal native grammar.** Each `ALTER TABLE` statement performs exactly one action, and only a handful of actions exist. There is no `ALTER COLUMN TYPE`, no multi-action comma form, and (before 3.53.0) no way to add or drop a constraint after creation. Anything outside the native forms requires rebuilding the table, and dbwarden generates that rebuild for you.

2. **The schema is stored as text.** SQLite keeps the original `CREATE` statements in `sqlite_schema` (`sqlite_master`). Every `ALTER TABLE` modifies that text and reparses the entire schema; the statement only succeeds if the schema is still valid afterwards. Renames therefore rewrite the stored SQL of *other* objects (triggers, views, foreign key clauses), not just the table's own row.

3. **DDL is transactional, but there is only one writer.** SQLite transactions are atomic — "all changes within a single transaction in SQLite either occur completely or not at all" ([sqlite.org](https://sqlite.org/transactional.html)) — so a failed migration rolls back cleanly, unlike MySQL or ClickHouse. The cost is concurrency: one writer at a time, and a rebuild holds the write lock while it copies every row.

## Native ALTER support

Feature-introduction timeline from the [SQLite changelog](https://sqlite.org/changes.html):

| Form | Since |
|------|-------|
| `ALTER TABLE ... RENAME TO` | 3.1.2 (2005) |
| `ALTER TABLE ... ADD COLUMN` | 3.2.0 (2005) |
| `ALTER TABLE ... RENAME COLUMN` | 3.25.0 (2018) |
| `ALTER TABLE ... DROP COLUMN` | 3.35.0 (2021) |
| `ALTER COLUMN ... SET/DROP NOT NULL`, `ADD/DROP CONSTRAINT ... CHECK` | 3.53.0 (2026) |

Two consequences:

- **No batching syntax.** One statement, one action. dbwarden never combines SQLite ALTERs — instead its collapse pass folds every change that needs a rebuild into a *single* rebuild per table (see [below](#how-dbwarden-decides-native-alter-vs-rebuild)).
- **dbwarden does not version-sniff.** It emits the same SQL against every SQLite version: the four classic forms natively, everything else as a rebuild. The 3.53.0 `SET/DROP NOT NULL` syntax is not used, so behavior is identical across versions; a native `DROP COLUMN` does require SQLite ≥ 3.35 at apply time (see [version gotchas](#version-specific-gotchas)).

## ADD COLUMN restrictions

From the [SQLite ALTER TABLE documentation](https://sqlite.org/lang_altertable.html), the new column may take any form permissible in `CREATE TABLE`, with these restrictions:

- The column may not have a `PRIMARY KEY` or `UNIQUE` constraint.
- The column may not have a default of `CURRENT_TIME`, `CURRENT_DATE`, `CURRENT_TIMESTAMP`, or an expression in parentheses.
- If `NOT NULL` is specified, the column must have a default other than `NULL`.
- With foreign key enforcement on, a column with a `REFERENCES` clause must default to `NULL`.
- The column may not be `GENERATED ALWAYS ... STORED` (`VIRTUAL` is allowed).

Two runtime subtleties:

- Since **3.37.0**, adding a column with a `CHECK` constraint — or a `NOT NULL` constraint on a generated column — validates the new constraint against **all preexisting rows** and fails the `ADD COLUMN` if any row violates it. Before 3.37.0 the violation went undetected at ALTER time.
- That validation makes the ALTER take time proportional to table size. Constraint-free additions only rewrite schema text and run in constant time.

### How dbwarden handles it

`add_column_requires_rebuild` in `sql_build.py` mirrors the SQLite rules: a primary key, `UNIQUE`, `STORED` generated, `NOT NULL` without a default, or non-constant-default column is emitted as a rebuild; anything else is a native `ALTER TABLE ... ADD COLUMN`.

## DROP COLUMN

`DROP COLUMN` was added in **3.35.0** and works only when the column is not referenced elsewhere in the schema. Per the [documentation](https://sqlite.org/lang_altertable.html), it fails when the column:

- is a `PRIMARY KEY` or part of one,
- has a `UNIQUE` constraint,
- is indexed, or named in a partial index's `WHERE` clause,
- is named in a `CHECK` constraint not associated with the column itself,
- is used in a foreign key constraint,
- is used in a generated column expression,
- appears in a trigger or view.

Unlike `ADD COLUMN`, dropping a column rewrites every row, so it takes time proportional to table size even when it is natively supported.

!!! warning "DROP COLUMN corrupted databases before 3.35.5"

    The `DROP COLUMN` implementation in 3.35.0–3.35.4 "could corrupt the database file" when the table was rewritten ([changelog](https://sqlite.org/changes.html), fixed in 3.35.5). Do not rely on native `DROP COLUMN` against SQLite older than **3.35.5** — and never against < 3.35.0, where the statement does not exist.

### How dbwarden handles it

`drop_column_requires_rebuild` in `sql_build.py` checks the same reference list (primary key, index, constraint, generated expression) and falls back to a rebuild when any apply. A rebuild that drops a column discards its data; the rollback restores the column but not its contents, and the migration is reported as a conditional rollback for that reason.

## RENAME semantics and stored schema text

Renames are the subtlest SQLite ALTERs because they rewrite stored SQL text:

- Since **3.25.0**, `RENAME TO` and `RENAME COLUMN` also rewrite references inside triggers and views that mention the renamed object.
- Foreign key `REFERENCES` clauses in *other* tables are rewritten too — only when `PRAGMA foreign_keys=ON` before 3.26.0, unconditionally since 3.26.0:

| `foreign_keys` | `legacy_alter_table` | FK references rewritten | Version |
|---|---|---|---|
| Off | Off | No | < 3.26.0 |
| Off | Off | Yes | ≥ 3.26.0 |
| On | Off | Yes | all |
| any | On | No | all |

- `PRAGMA legacy_alter_table=ON` (3.25.2+) restores the pre-3.25 behavior where only the object's own `CREATE` statement is edited and references inside trigger/view bodies are left pointing at the old name. It is a per-connection, non-persistent setting intended as a workaround for programs that depend on the old behavior. dbwarden never sets it.

Because the rewrite is textual, an `ALTER TABLE` also fails — changing nothing — if *any* row in `sqlite_schema` does not parse (that strictness can be disabled with `PRAGMA writable_schema=ON` since 3.38.0, which dbwarden does not use).

### The ordering trap

The [official 12-step procedure](https://sqlite.org/lang_altertable.html) for arbitrary schema changes is explicit about ordering:

> The safe procedure constructs the revised table definition using a new temporary name, then renames the table into its final name... Renaming the original table away first "might corrupt references to that table in triggers, views, and foreign key constraints."

```sql
-- Correct (what dbwarden generates)
CREATE TABLE users__dbw_new (...);
INSERT INTO users__dbw_new SELECT ... FROM users;
DROP TABLE users;
ALTER TABLE users__dbw_new RENAME TO users;

-- Incorrect: renaming the original first can corrupt
-- trigger, view, and FK references to it (3.25.0+)
ALTER TABLE users RENAME TO users_old;
CREATE TABLE users (...);
```

dbwarden's rebuild always uses the safe create → copy → drop → rename order, with the staging table named `<table>__dbw_new`. See [Table rebuilds](index.md#table-rebuilds) for the full generated sequence.

## PRAGMA foreign_keys is a no-op inside a transaction

From the [PRAGMA documentation](https://sqlite.org/pragma.html):

> This pragma is a no-op within a transaction; foreign key constraint enforcement may only be enabled or disabled when there is no pending BEGIN or SAVEPOINT.

This is why the official procedure disables foreign keys *before* starting the transaction, and it constrains migration tooling: dbwarden runs each migration inside a single transaction (see [locking](#locking-and-busy_timeout) below), so a `PRAGMA foreign_keys` emitted mid-migration would silently do nothing. dbwarden therefore never emits it and relies on the SQLite default (`OFF`). If your deployment enables foreign keys on the migration connection, turn them off before running the migration — with enforcement on, the `DROP TABLE` step of a rebuild can be blocked or cascade into referencing tables.

A related pragma, `PRAGMA defer_foreign_keys=ON`, delays FK enforcement until commit for the current transaction — useful in hand-written data migrations, and automatically reset at each `COMMIT` or `ROLLBACK`.

## Locking and busy_timeout

SQLite allows any number of readers but exactly one writer:

- In rollback-journal mode, a writer must escalate to an `EXCLUSIVE` lock to commit; active readers block that escalation and the writer fails with `SQLITE_BUSY` once the busy timeout expires ([locking architecture](https://sqlite.org/lockingv3.html)).
- In [WAL mode](https://sqlite.org/wal.html), readers never block the writer and vice versa, but there is still only one writer — a concurrent migration still gets `SQLITE_BUSY`.

dbwarden's SQLite lock strategy (`dbwarden/lock/sqlite.py`) runs the **entire migration in one `BEGIN IMMEDIATE` transaction** on the lock connection. That acquires the write lock up front, makes the whole migration atomic, and means a crash mid-migration releases everything with no schema change applied. The busy timeout is controlled by the `sqlite_busy_timeout` config key and defaults to `0` (fail fast with `SQLITE_BUSY` rather than queue behind another writer).

The practical consequence for rebuilds: a rebuild copies every row while holding the database-wide write lock. On a large table that is a maintenance-window operation, which is why `check-impact` flags rebuild-triggering option changes as warnings.

## How dbwarden decides: native ALTER vs rebuild

The SQLite backend runs a **collapse pass** (`collapse_sqlite_ops` in `collapse.py`) over the diff before any SQL is emitted:

1. Ops that SQLite can express natively pass through unchanged: `rename_table`, `rename_column`, `add_index`/`drop_index`, and the `add_column`/`drop_column` variants described above.
2. Every remaining op on a table — type, nullability, or default changes, constraint adds/drops, `WITHOUT ROWID`/`STRICT` changes, generated-column or collation changes — is folded into **one** `recreate_sq_table` op per table, regardless of how many individual changes were requested.
3. Index ops on a table being rebuilt are dropped from the op list, because the rebuild recreates the table's indexes itself.
4. If the before/after table shapes needed to build the rebuild are unavailable, the original ops are kept so the failure is visible: emit falls back to comment-only statements such as `-- SQLite: ADD CONSTRAINT ... (not supported)`.

The full trigger matrix is documented in [What triggers a rebuild](index.md#what-triggers-a-rebuild).

### dbwarden's batching behavior

| Handler | Emits natively | Batching |
|---------|----------------|----------|
| SqTableHandler | Rebuild script only | All table/column-meta changes collapse into one rebuild per table |
| ColumnHandler | `RENAME COLUMN`, `ADD COLUMN`, `DROP COLUMN` | One statement per op |
| RenameTableHandler | `ALTER TABLE ... RENAME TO` | One statement per op |
| IndexHandler | `CREATE [UNIQUE] INDEX` / `DROP INDEX` | One statement per op; folded into the rebuild when the table rebuilds |
| ConstraintHandler | — (rebuild via collapse) | Comment-only fallback when the rebuild can't be constructed |

A rebuild is a single `MigrationStatement` whose SQL is a multi-statement script (create, copy, drop, rename, recreate indexes); the statements execute individually, in order, inside the migration transaction.

## Operation catalog

Complete reference of the ALTER shapes dbwarden generates for SQLite, with their classification. Two systems apply: the **`--force` gate** (blocks migration generation/apply on dangerous changes; shown as the Safety column below, `—` = not gated) and the **impact-plan severity** reported by `dbwarden check-impact`, which additionally rates `drop_column` as ERROR and `drop_index` / `drop_foreign_key` / `recreate_sq_table` as WARNING. Legacy `WARNING` / `ERROR` display labels map to `WARN` / `CRITICAL`. Use the generated plan for operation-specific decisions and the [safety scope contract](../../correctness/safety-scoped-migrations.md) for gates.

### Native ALTER operations

| Operation | Handler | Emitted as | Safety | Notes |
|-----------|---------|-----------|--------|-------|
| Rename table | RenameTableHandler | `ALTER TABLE old RENAME TO new;` | — | Rewrites trigger/view/FK text (3.25.0+) |
| Rename column | ColumnHandler | `ALTER TABLE t RENAME COLUMN a TO b` | — | 3.25.0+ |
| Add column | ColumnHandler | `ALTER TABLE t ADD COLUMN c <def>` | INFO | Restrictions above; else rebuild |
| Drop column | ColumnHandler | `ALTER TABLE t DROP COLUMN c` | WARNING, `--force` | 3.35.0+ (≥3.35.5 advised); rewrites all rows |
| Add index | IndexHandler | `CREATE [UNIQUE] INDEX ix ON t (...)` | INFO | Partial/expression indexes preserved |
| Drop index | IndexHandler | `DROP INDEX ix` | — | Not gated; impact plan rates WARNING |

### Rebuild operations (one rebuild per table, any mix of triggers)

| Trigger | Safety | Notes |
|---------|--------|-------|
| Column type change | WARNING, `--force` | Generic `change_column_type` classification |
| `sq_without_rowid` / `sq_strict` change | WARNING, `--force` | Flagged with "(rebuilds the table and copies every row)" |
| `sq` generated column / collation change | WARNING, `--force` | Same message |
| Column nullability / default change | — | Not separately flagged; rebuild is visible in the migration |
| Add/drop `UNIQUE`, `CHECK`, `FOREIGN KEY` | — | Constraints can only enter via the CREATE statement |
| `ADD COLUMN` hitting a native restriction | INFO (as add column) | PK, UNIQUE, NOT NULL w/o default, non-constant default, STORED generated |
| `DROP COLUMN` hitting a native restriction | WARNING, `--force` | PK, indexed, constraint-referenced, generated-referenced |

The rebuild's rollback is the rebuild in the other direction (`rollback_kind="real"`), except when columns were dropped — then the rollback restores the columns but not their data and is reported as conditional.

## Version-specific gotchas

| Version | Issue |
|---------|-------|
| 3.25.0 | Rename sanity-check false positives roll back valid ALTERs; fixed in 3.25.1 |
| 3.25.0–3.25.x | FK references rewritten on rename only with `foreign_keys=ON`; unconditional from 3.26.0 |
| 3.35.0–3.35.4 | `DROP COLUMN` can corrupt the database file; fixed in 3.35.5 |
| < 3.37.0 | `ADD COLUMN` does not validate new `CHECK`/`NOT NULL` constraints against existing rows |
| 3.7.0–3.51.2 | WAL-mode reset bug can corrupt the database when a writer and a checkpoint race; fixed in 3.51.3 (backports 3.44.6, 3.50.7) |

Recommended floor: **3.26.0** for predictable rename behavior, **3.35.5** if you rely on native `DROP COLUMN`, **3.37.0** for `STRICT` tables and `ADD COLUMN` validation, **3.51.3** (or a backport) for WAL deployments.

## Related

- [SQLite overview](index.md): table rebuilds, `WITHOUT ROWID`/`STRICT`, generated columns, type fidelity
- [Migration locking](../../advanced/migration-locking.md): how the `BEGIN IMMEDIATE` lock coordinates concurrent runs
- [Rollback generation](../../correctness/rollback-generation.md): the rollback contract rebuilds satisfy
- [Common SQL databases](../sql-databases.md): cross-backend DDL behavior comparison
