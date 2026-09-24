# ALTER semantics

MySQL and MariaDB ALTERs behave differently from PostgreSQL or SQLite. DDL is non-transactional, `MODIFY COLUMN` silently drops any attribute you do not restate, and every statement goes through an algorithm/locking machinery that can turn a simple-looking change into a full table copy. Understanding these properties prevents the classic MySQL migration failures: half-applied migrations, lost column attributes, and metadata-lock pileups.

## Why MySQL ALTERs are special

1. **Non-transactional DDL.** Every DDL statement implicitly commits the current transaction — before *and* after it runs — and cannot be rolled back. A migration file that fails on statement 4 leaves statements 1–3 applied.

2. **`MODIFY COLUMN` requires the full column definition.** Attributes present in the original definition but not restated are dropped. A bare `MODIFY COLUMN c BIGINT` on an `INT UNSIGNED AUTO_INCREMENT COMMENT '...'` column silently removes `UNSIGNED`, `AUTO_INCREMENT`, and the comment.

3. **The algorithm/lock machinery decides the cost.** Every `ALTER TABLE` runs as `INSTANT` (metadata only), `INPLACE` (may rebuild in place), or `COPY` (full table copy), with a lock level from `NONE` to `EXCLUSIVE`. The same statement is free on one version and a multi-hour rewrite on another — and even "instant" operations must wait for a metadata lock.

## Non-transactional DDL

From [Statements That Cause an Implicit Commit](https://dev.mysql.com/doc/refman/8.0/en/implicit-commit.html):

> The statements listed in this section implicitly end any transaction active in the current session, as if you had done a `COMMIT` before executing the statement. Most of these statements also cause an implicit commit after executing.

The list covers all DDL: `ALTER TABLE`, `CREATE`/`DROP` of tables, indexes, views, triggers, `RENAME TABLE`, `TRUNCATE TABLE`, and more.

### What "atomic DDL" is not

MySQL 8.0 introduced *atomic DDL*, which is frequently misread as transactional DDL. [The manual is explicit](https://dev.mysql.com/doc/refman/8.0/en/atomic-ddl.html):

> **Atomic DDL is not transactional DDL.** DDL statements, atomic or otherwise, implicitly end any transaction that is active in the current session... DDL statements cannot be performed within another transaction, within transaction control statements such as `START TRANSACTION ... COMMIT`, or combined with other statements within the same transaction.

Atomic DDL means crash safety: data dictionary updates, storage engine operations, and binlog writes are committed or rolled back as one unit (via the hidden `mysql.innodb_ddl_log` table), even if the server halts mid-operation. It says nothing about rolling back a *successful* statement. Only InnoDB supports it. MariaDB reached the equivalent in **10.6.1** ("most \[DDL operations\] atomic, and the rest crash-safe", via `ddl_recovery.log`), with the same implicit-commit semantics.

### How dbwarden handles it

In `run_migration` (`migrations_repo.py`), the MySQL/MariaDB branch disables savepoints and executes statements individually, because DDL would implicitly commit and destroy them anyway:

> MySQL/MariaDB DDL statements implicitly commit the current transaction and destroy any active savepoints. For MySQL/MariaDB, savepoints are disabled and DDL statements are executed individually (they auto-commit).

Consequences:

- **A failed migration leaves earlier statements applied.** dbwarden records only completed migration files; reconciling a file that died halfway is a manual step — inspect the database, then either fix forward or run the file's `-- rollback` section by hand.
- **Rollback SQL is still generated for every operation** (inverse `MODIFY COLUMN` with the snapshot's full definition, inverse table-option ALTER, `DROP INDEX`, re-`ADD CONSTRAINT`), and `dbwarden rollback` / `downgrade` execute it through the same runner.
- Where an inverse cannot be produced, dbwarden writes a placeholder instead of guessing: `-- Cannot unset engine for t`, `-- MANUAL ACTION REQUIRED: ...`, or `/* REQUIRES MANUAL COLUMN DEFINITION - type info unavailable */`. With the strict rollback contract enabled, a placeholder fails generation unless the migration is annotated irreversible.

## MODIFY COLUMN drops unspecified attributes

From the [ALTER TABLE documentation](https://dev.mysql.com/doc/refman/8.0/en/alter-table.html):

> For column definition changes using `CHANGE` or `MODIFY`, the definition must include the data type and **all attributes** that should apply to the new column, other than index attributes such as `PRIMARY KEY` or `UNIQUE`. **Attributes present in the original definition but not specified for the new definition are not carried forward.**

The manual's own example: after `MODIFY col1 BIGINT` on a column defined `INT UNSIGNED DEFAULT 1 COMMENT 'my column'`, "it also drops the `UNSIGNED`, `DEFAULT`, and `COMMENT` attributes."

### How dbwarden handles it

The comment, MySQL-meta, and default change paths rebuild the complete definition with `mysql_column_definition_for_meta` (`dbwarden/engine/backends/mysql/extract.py`), always in the same clause order:

```sql
-- type -> UNSIGNED -> AUTO_INCREMENT -> NULL/NOT NULL -> DEFAULT -> COMMENT -> CHARACTER SET -> COLLATE -> ON UPDATE
ALTER TABLE users MODIFY COLUMN id INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Primary key';
```

Every type used in a `MODIFY COLUMN` passes `assert_complete_mysql_type`, so a bare `VARCHAR` in a model fails fast at generation time instead of producing a syntax error mid-migration.

!!! warning "Limitation: type, nullability, and autoincrement changes emit a partial definition"

    The type-change, nullability-change, and autoincrement-toggle paths emit `MODIFY COLUMN c <type> [NULL|NOT NULL] [AUTO_INCREMENT]` without re-emitting `DEFAULT`, `COMMENT`, `ON UPDATE`, charset, or collation. MySQL drops those attributes. A subsequent `make-migrations` run re-detects the missing attributes and emits a separate `MODIFY COLUMN` to restore them — but only as a new migration. Review the generated SQL when changing the type or nullability of a column that carries attributes, and prefer splitting such changes into their own migration.

## ALGORITHM and LOCK: what the server picks

dbwarden never emits `ALGORITHM=` or `LOCK=` clauses — every statement runs with the server default, which [selects](https://dev.mysql.com/doc/refman/8.0/en/alter-table.html):

> If the `ALGORITHM` clause is omitted, MySQL uses `ALGORITHM=INSTANT` for storage engines and `ALTER TABLE` clauses that support it. Otherwise, `ALGORITHM=INPLACE` is used. If `ALGORITHM=INPLACE` is not supported, `ALGORITHM=COPY` is used.

| Algorithm | Cost | Concurrent DML |
|-----------|------|----------------|
| `INSTANT` (8.0.12+) | Metadata in the data dictionary only | Yes |
| `INPLACE` | No data copy, but may rebuild the table in place | Typically yes |
| `COPY` | Row-by-row copy into a new table | **No** |

Specifying either clause is **fail-not-fallback**: an unsupported algorithm or lock level aborts the statement with `ERROR 1845/1846 (0A000): ALGORITHM=INSTANT is not supported for this operation...` rather than silently downgrading. Only `LOCK=DEFAULT` is permitted with `ALGORITHM=INSTANT`, and `LOCK=NONE` is not permitted at all on tables with `ON ... CASCADE` or `ON ... SET NULL` foreign key constraints.

### Version milestones for INSTANT

| Capability | MySQL | MariaDB |
|------------|-------|---------|
| Instant `ADD COLUMN` (last position) | 8.0.12 | 10.3.2 |
| Instant `RENAME COLUMN` | 8.0.28 | 10.4 (definition unchanged) |
| Instant `ADD COLUMN` at any position, instant `DROP COLUMN` | 8.0.29 | 10.4 |
| Instant `VARCHAR` lengthening | never (INPLACE, same byte-width) | 10.4.3 |

Exclusions differ too: MySQL excludes `ROW_FORMAT=COMPRESSED`, FULLTEXT-indexed, temporary, and data-dictionary tables; MariaDB excludes `ROW_FORMAT=COMPRESSED` and leaves tables in a non-canonical format that older versions cannot import until rebuilt. MariaDB has no row-version limit; MySQL has one:

!!! warning "MySQL's 64 row-version limit and the 8.0.29–8.0.31 corruption bug"

    Every instant `ADD`/`DROP COLUMN` bumps the table's row version, tracked in `INFORMATION_SCHEMA.INNODB_TABLES.TOTAL_ROW_VERSIONS`. At **64**, instant column changes are rejected: `ERROR 4092 (HY000): Maximum row versions reached... Please use COPY/INPLACE.` Remedy: `OPTIMIZE TABLE` or any rebuilding `ALTER`, which resets the counter (rows also carry a per-read version-conversion cost until then). Additionally, the instant-column redo format in **8.0.29–8.0.31 has a design flaw that can corrupt tables on crash recovery** — Percona XtraBackup refuses to back up such servers. Use **8.0.32 or later** for instant add/drop column, as the [official blog](https://dev.mysql.com/blog-archive/mysql-8-0-instant-add-and-drop-columns/) recommends.

### Which dbwarden operations rebuild the table

| Operation | Server algorithm | Rebuilds? |
|-----------|------------------|-----------|
| `ADD COLUMN` | INSTANT (default) | No |
| `DROP COLUMN` | INSTANT (8.0.29+) | No |
| `RENAME COLUMN` / `RENAME TO` | INSTANT (8.0.28+) | No |
| Type change (`MODIFY COLUMN c <type>`) | **COPY only** | **Yes, full copy** |
| Nullability change | INPLACE | Yes (in place) |
| `DEFAULT` change | INSTANT (metadata) | No |
| Comment change | Metadata only | No |
| Column charset/collation change | COPY (data conversion) | **Yes, full copy** |
| `ENGINE=` | INPLACE | **Yes — even if the engine is unchanged** |
| `ROW_FORMAT=` | INPLACE | Yes (in place) |
| `AUTO_INCREMENT=N` | INPLACE | No (in-memory value) |
| `DEFAULT CHARACTER SET` (table default) | Metadata only | No (existing columns untouched) |
| Add / drop secondary index | INPLACE | No |
| `ADD FOREIGN KEY` | **COPY** unless `foreign_key_checks=0` | Yes, with checks on |
| `DROP FOREIGN KEY` | INPLACE | No |

## Metadata locks: the real production risk

[Metadata locking](https://dev.mysql.com/doc/refman/8.0/en/metadata-locking.html) holds a table's MDL until the *transaction* ends, and DDL must wait for all holders:

> A table that is being used by a transaction within one session cannot be used in DDL statements by other sessions until the transaction ends.

The pileup that takes down production: write-lock requests have **higher priority** than read requests, so an `ALTER TABLE` waiting behind one long transaction blocks *every new query* on the table behind it — and this applies to `ALGORITHM=INSTANT` statements too, because every online DDL ["always requires an exclusive metadata lock in the final phase"](https://dev.mysql.com/doc/refman/8.0/en/innodb-online-ddl-limitations.html). A failed statement inside an explicit transaction even keeps its MDLs until the transaction ends.

Mitigations:

- `lock_wait_timeout` defaults to **31536000 seconds (one year)**. Set it to seconds on the migration session (`SET SESSION lock_wait_timeout = 10`) so a stuck migration fails fast instead of queueing half the application behind it.
- Keep transactions short during deploys; kill the blocker, not the migration.
- MySQL has no `ALTER TABLE ... NOWAIT`. **MariaDB does** (10.3+): `ALTER TABLE t WAIT 10 ...` / `NOWAIT`, plus `ALTER ONLINE TABLE` as an alias for `LOCK=NONE`.

dbwarden's MySQL lock strategy uses `GET_LOCK()`/`RELEASE_LOCK()` to coordinate concurrent migration runs and refuses to run on a server in `read_only`/`super_read_only`; it does not set `lock_wait_timeout` for you.

## Online DDL failure modes

Worth knowing before running a large `INPLACE` change ([failure conditions](https://dev.mysql.com/doc/refman/8.0/en/innodb-online-ddl-failure-conditions.html), [limitations](https://dev.mysql.com/doc/refman/8.0/en/innodb-online-ddl-limitations.html)):

- **Concurrent DML wins.** If rows written during the ALTER violate the new definition, the ALTER fails *at the very end* of the operation — possibly with `ERROR 1062 (23000): Duplicate entry` even for a duplicate that only existed transiently in the online log.
- **`innodb_online_alter_log_max_size`** (default 128MB) caps the concurrent-DML log; exceeding it aborts the ALTER with `DB_ONLINE_LOG_TOO_BIG`.
- There is no way to pause or throttle an online DDL, and rolling back a failed one can be expensive. Kill behavior differs on MariaDB: killing the connection rolls the ALTER back "in a controlled manner", slowly.

## Replication

> Long running online DDL operations can cause replication lag. An online DDL operation must finish running on the source before it is run on the replica. — [Online DDL Limitations](https://dev.mysql.com/doc/refman/8.0/en/innodb-online-ddl-limitations.html)

DDL is binlogged as statement events, and a single large DDL is one transaction the multithreaded applier cannot split — a one-hour `ALTER TABLE` on the source is (at least) a one-hour replica stall. **MariaDB 10.8+** can start the ALTER on replicas when it *starts* on the primary (`binlog_alter_two_phase`), eliminating that lag.

One 8.0 behavior change for hand-written SQL: partially-completed multi-object `DROP TABLE`/`DROP VIEW` statements replicated from a 5.7 source **fail** on an 8.0 replica. Always write `DROP TABLE IF EXISTS`.

## Charset conversion subtleties

dbwarden emits two different charset changes with very different costs:

- **Table default** (`my_charset` change → `ALTER TABLE t DEFAULT CHARACTER SET utf8mb4`): metadata only; existing columns keep their charset.
- **Column charset** (`my.field(charset=...)` change → `MODIFY COLUMN ... CHARACTER SET ...`): a data conversion — COPY algorithm, full table rewrite.

What dbwarden never emits, and you should avoid in hand-written SQL: `CONVERT TO CHARACTER SET`, which [silently widens types](https://dev.mysql.com/doc/refman/8.0/en/alter-table.html) ("a `VARCHAR` column might be converted to `MEDIUMTEXT`") and is **forbidden on tables with a character column used in a foreign key** while `foreign_key_checks=1` — converting only one side can corrupt data via `ON DELETE/UPDATE CASCADE` implicit conversion (Bug #45290, Bug #74816). The safe path for `latin1` columns that actually store `utf8mb4` bytes is the two-step conversion through `BLOB`. Note `utf8` is a deprecated alias for 3-byte `utf8mb3` since 8.0.29; new schemas should use `utf8mb4`. Since 8.0.14, changing a column `utf8mb3`→`utf8mb4` is metadata-only when the column is unindexed.

## Foreign keys and indexes

- **InnoDB auto-creates the FK index.** "Such an index is created on the referencing table automatically if it does not exist. This index might be silently dropped later if you create another index that can be used to enforce the foreign key constraint" ([FOREIGN KEY Constraints](https://dev.mysql.com/doc/refman/8.0/en/create-table-foreign-keys.html)).
- **`ADD FOREIGN KEY` validates existing rows** when `foreign_key_checks=1` — which forces the COPY algorithm. With `foreign_key_checks=0` it runs INPLACE, but rows written while checks were off are *never* re-validated: re-enabling does not scan the table.
- **`DROP FOREIGN KEY` vs `DROP CONSTRAINT`.** Foreign keys are dropped with `ALTER TABLE t DROP FOREIGN KEY name`; `DROP CONSTRAINT` (8.0.19+) only resolves to CHECK constraints on MySQL. MariaDB's `DROP CONSTRAINT` covers `UNIQUE`, `FOREIGN KEY`, and `CHECK`.
- **CHECK constraints are parsed and ignored before 8.0.16** — a `CHECK` "existing" on an older server enforces nothing.
- Adding and dropping a foreign key in the same `ALTER TABLE` statement requires `ALGORITHM=INPLACE` (not COPY).

!!! warning "dbwarden limitation: add-FK rollback uses DROP CONSTRAINT"

    The rollback for an added foreign key is emitted as `ALTER TABLE t DROP CONSTRAINT fk_name;` for all backends. That is valid on MariaDB, but MySQL rejects `DROP CONSTRAINT` for foreign keys — the rollback must be edited to `DROP FOREIGN KEY fk_name` before it can run on MySQL. (The forward direction of a *dropped* FK correctly uses `DROP FOREIGN KEY`.)

## AUTO_INCREMENT

- **Counter persistence.** On 5.7 the InnoDB counter lived in memory and was reinitialized to `SELECT MAX(col)+1` on restart — delete the newest rows, restart, and their ids are reissued. Since 8.0 the counter is written to the redo log and persisted in the data dictionary across restarts.
- **`ALTER TABLE t AUTO_INCREMENT = N`** (what dbwarden emits for `my_auto_increment`) cannot set the counter below the current maximum: the value is silently reset to `MAX+1`. Changing it is an in-memory, non-rebuilding operation.
- dbwarden never emits an *unset*: a model-side `my_auto_increment = None` is ignored, and the op is skipped entirely for newly created tables.

## Batching: one statement per change

MySQL's own grammar accepts comma-joined actions (`ALTER TABLE t ENGINE=InnoDB, ROW_FORMAT=DYNAMIC, ADD COLUMN ...`) — one statement, one table rebuild. **dbwarden never combines them**: every handler emits one `ALTER TABLE` per change, joined by blank lines in the migration file.

| Handler | Emits | Batching |
|---------|-------|----------|
| MyTableHandler | `ENGINE=` / `DEFAULT CHARACTER SET` / `COLLATE=` / `ROW_FORMAT=` / `AUTO_INCREMENT=` | One statement **per changed option** |
| ColumnHandler | `MODIFY COLUMN`, `ADD COLUMN`, `DROP COLUMN`, `RENAME COLUMN` | One statement per op |
| TableHandler | `ALTER TABLE t COMMENT = '...'` | One statement per op |
| ConstraintHandler | `ADD CONSTRAINT ... FOREIGN KEY`, `DROP FOREIGN KEY` | One statement per op |
| IndexHandler | `CREATE [UNIQUE] INDEX`, `DROP INDEX` | One statement per op |
| RenameTableHandler | `ALTER TABLE old RENAME TO new` | One statement per op |

The practical consequence: changing engine, charset, and row format in one migration produces **three separate `ALTER TABLE` statements, two of which rebuild the table**. For large tables, write the combined statement yourself in a `dbwarden new` migration. (Within `CREATE TABLE`, options *are* combined into one statement.)

## MariaDB divergences

- **`ALGORITHM=NOCOPY`** exists only on MariaDB, and a specified algorithm means "the least efficient algorithm you accept": `INPLACE` allows NOCOPY or INSTANT to be chosen instead. Default chain: INSTANT → NOCOPY → INPLACE → COPY. Since 11.2, even COPY can run `LOCK=NONE`.
- **Syntax extras MySQL lacks**: `IF [NOT] EXISTS` on subclauses (`ADD COLUMN IF NOT EXISTS`, `DROP INDEX IF EXISTS`, ...), `WAIT n`/`NOWAIT`, `ALTER ONLINE TABLE` ≡ `LOCK=NONE`, `DROP CONSTRAINT` for UNIQUE/FK/CHECK, and client progress reporting (`Stage: 1 of 2 'copy to tmp table'`).
- **Invisible columns** arrived in MariaDB 10.3.3 (column attribute `INVISIBLE`) vs MySQL 8.0.23 (`ALTER COLUMN c SET INVISIBLE`) — different syntax, same name.
- **dbwarden note**: the MariaDB model-side features (`mdb.field(invisible=..., sequence=...)`, `mdb_page_compressed`) are captured in models and snapshots, but no DDL emitter consumes them yet — changing them generates no `ALTER` statement.

## Operation catalog

Every ALTER shape dbwarden generates for MySQL/MariaDB, with its classification. Two systems apply: the **`--force` gate** (blocks migration generation/apply on dangerous changes) and the **impact-plan severity** (reported by `dbwarden check-impact`; unlisted operations default to INFO).

The `--force` gate flags only: drop table, drop column, and column type change (`WARNING`, requires `--force`). MySQL table-option and column-meta changes are **not gated** — review them in the generated SQL instead.

### Column operations

| Operation | Handler | Server algorithm | Impact severity | Example |
|-----------|---------|------------------|-----------------|---------|
| Type change | ColumnHandler | COPY (full rebuild) | WARNING | `ALTER TABLE t MODIFY COLUMN c BIGINT` |
| Nullability change | ColumnHandler | INPLACE, rebuilds | WARNING | `ALTER TABLE t MODIFY COLUMN c INT NOT NULL` |
| Default change | ColumnHandler | INSTANT (metadata) | INFO | `ALTER TABLE t MODIFY COLUMN c INT NOT NULL DEFAULT 0` |
| Comment change | ColumnHandler | Metadata only | INFO | `ALTER TABLE t MODIFY COLUMN c VARCHAR(255) COMMENT '...'` |
| my-meta change (unsigned/charset/collate/on_update) | ColumnHandler | charset: COPY; others vary | WARNING | `ALTER TABLE t MODIFY COLUMN c INT UNSIGNED` |
| Autoincrement toggle | ColumnHandler | INPLACE, `LOCK=SHARED` min. | WARNING | `ALTER TABLE t MODIFY COLUMN c INT NOT NULL AUTO_INCREMENT` |
| Add column | ColumnHandler | INSTANT | INFO | `ALTER TABLE t ADD COLUMN c INT` |
| Drop column | ColumnHandler | INSTANT (8.0.29+) | **ERROR** | `ALTER TABLE t DROP COLUMN c` |
| Rename column | ColumnHandler | INSTANT (8.0.28+) | INFO | `ALTER TABLE t RENAME COLUMN a TO b` |

### Table options (one statement per changed option)

| Option | Server algorithm | Impact severity | Example |
|--------|------------------|-----------------|---------|
| Engine | INPLACE, **always rebuilds** | INFO | `ALTER TABLE t ENGINE=InnoDB;` |
| Charset (table default) | Metadata only | INFO | `ALTER TABLE t DEFAULT CHARACTER SET utf8mb4;` |
| Collation | Metadata only | INFO | `ALTER TABLE t COLLATE=utf8mb4_unicode_ci;` |
| Row format | INPLACE, rebuilds | INFO | `ALTER TABLE t ROW_FORMAT=DYNAMIC;` |
| Auto increment | INPLACE, no rebuild | INFO | `ALTER TABLE t AUTO_INCREMENT=1000;` |
| Table comment | Metadata only | INFO | `ALTER TABLE t COMMENT = '...';` |

### Keys and indexes

| Operation | Handler | Server algorithm | Impact severity | Example |
|-----------|---------|------------------|-----------------|---------|
| Add foreign key | ConstraintHandler | COPY with checks on | INFO | `ALTER TABLE t ADD CONSTRAINT fk_t_a FOREIGN KEY (a) REFERENCES r(id);` |
| Drop foreign key | ConstraintHandler | INPLACE | WARNING | `ALTER TABLE t DROP FOREIGN KEY fk_t_a;` |
| Add index | IndexHandler | INPLACE, no rebuild | INFO | `CREATE INDEX ix_t_a ON t (a);` |
| Drop index | IndexHandler | Metadata only | WARNING | `DROP INDEX ix_t_a;` |
| Rename table | RenameTableHandler | INSTANT (metadata) | INFO | `ALTER TABLE old RENAME TO new;` |

## Related

- [MySQL & MariaDB overview](index.md): metadata declaration, snapshot format, reverse engineering
- [Migration locking](../../advanced/migration-locking.md): the `GET_LOCK()` coordination strategy
- [Rollback generation](../../correctness/rollback-generation.md): the rollback contract and placeholders
- [Common SQL databases](../sql-databases.md): cross-backend DDL behavior comparison
