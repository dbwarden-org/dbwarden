# Merge Handling

dbwarden supports branch merge reconciliation for teams using git workflows. When two branches generate migrations that diverge from a common model state, dbwarden can detect the merge, generate a reconciliation migration, and mark the branch migrations as superseded.

## Overview

dbwarden is declarative: SQLAlchemy models are the source of truth, and migrations are derived output. When two branches diverge and each generates migrations, the merged schema is defined entirely by the merged models. The correct migration after a merge is `diff(merge_base_state, merged_models)`.

This means:
- Branch migrations carry no schema meaning going forward (only provenance)
- The reconciliation migration is the only new runnable migration
- Branch migrations are marked as superseded (audit-only, never run again)

## Commands

### `dbwarden merge`

Reconciles divergent migration histories after a branch merge.

```bash
dbwarden merge --database primary
```

**Flags:**
- `--database`, `-d`: Target database name
- `--rename-column`: Column rename to confirm (format: `table.old=new`)
- `--rename-table`: Table rename to confirm (format: `old=new`)
- `--force`, `-f`: Force marking hand-edited migrations
- `--commit`: Create a git commit with the changes
- `--verbose`, `-v`: Enable verbose logging

**Algorithm:**
1. Check preconditions (clean working tree, no conflict markers, merge-base resolvable)
2. Resolve merge-base state from git
3. Rebuild current model state from merged models
4. Compute reconciliation diff
5. Probe persistent environments
6. Generate reconciliation migration
7. Mark branch migrations as superseded
8. Report

### `dbwarden rebase`

Recovers a disposable environment after a merge.

```bash
dbwarden rebase --database local
```

**Flags:**
- `--database`, `-d`: Target database name
- `--yes`, `-y`: Skip confirmation prompts
- `--force`, `-f`: Force operation even against persistent environments
- `--check`: Only check what would happen, don't make changes
- `--verbose`, `-v`: Enable verbose logging

**Algorithm:**
1. Read local applied migrations
2. Identify superseded versions
3. Rollback to merge-base (preferred) or reset (fallback)
4. Re-apply runnable chain
5. Verify convergence

### `dbwarden reconcile`

Recovers a persistent environment after a dirty merge.

```bash
dbwarden reconcile --environment staging
```

**Flags:**
- `environment`: Environment name to reconcile (required)
- `--database`, `-d`: Target database name
- `--rename-column`: Column rename to confirm
- `--dry-run`: Only show what would happen
- `--verbose`, `-v`: Enable verbose logging

## File Formats

### Superseded Marker

Prepended to migration files that have been superseded by a merge:

```sql
-- dbwarden:superseded
-- merged-into: 0006
-- merged-at: 2026-08-22T14:03:11Z
-- merge-base: 0004
-- branch: feature/profile-fields
-- applied-persistent: none
-- file-checksum: sha256:7ab1...

-- upgrade
ALTER TABLE users ADD COLUMN bio TEXT;

-- rollback
ALTER TABLE users DROP COLUMN bio;
```

**Rules:**
- Marked files are excluded from the runnable chain
- Marked files are never deleted (audit trail)
- `applied-persistent` tracks which environments applied the migration

### Reconciliation Migration Header

Added to migration files generated at merge time:

```sql
-- dbwarden:merge-reconciliation
-- merge-base: 0004 (state checksum 9f2c...)
-- supersedes: 0005_add_profile.sql, 0005_extend_billing.sql
-- probe: staging=clean, production=clean, qa=unknown
-- generated-by: dbwarden merge (0.18.0)

-- upgrade
...

-- rollback
...
```

## Environment Registry

Configure persistent vs disposable environments in your database config:

```python
database_config(
    database_name="primary",
    ...
    environments={
        "staging": {"url_env": "STAGING_DATABASE_URL", "persistent": True},
        "production": {"url_env": "PROD_DATABASE_URL", "persistent": True},
    },
)
```

**Rules:**
- Unregistered environments are disposable by default
- Persistent environments require `reconcile` after a dirty merge
- Disposable environments can be reset with `rebase`

## Merge Detection

dbwarden detects merges by checking for:
1. **Divergent generation base**: Newest migration's base checksum doesn't match current model state
2. **Version collisions**: Multiple migration files share the same version prefix
3. **Snapshot discontinuity**: Latest snapshot doesn't match model state

When detected, `make-migrations` refuses generation and `status` shows `MERGE_PENDING`.

## Workflow

### After pulling a merge

1. **Check status:**
   ```bash
   dbwarden status
   # Shows: Merge: PENDING
   ```

2. **Run merge:**
   ```bash
   dbwarden merge --database primary
   ```

3. **Recover local database:**
   ```bash
   dbwarden rebase --database local
   ```

4. **Recover persistent environments (if dirty):**
   ```bash
   dbwarden reconcile --environment staging
   ```

5. **Commit and push:**
   ```bash
   git add .
   git commit -m "Merge reconciliation"
   git push
   ```

## Known Limitations

- **MariaDB:** Snapshot support is incomplete; merge-base resolution uses `model_state.json` only
- **Git required:** All merge operations require git to be available
- **No Python data migrations:** Manual migrations are SQL-only
- **No revision branching/merging:** Linear versioned sequence per database

## See Also

- [Migration Locking](advanced/migration-locking.md): How migrations are locked
- [Offline Integrity](correctness/offline-integrity.md): Model state file management
- [CLI Reference](cli-reference.md): All commands
