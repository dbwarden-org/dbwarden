# `merge`, `rebase`, and `reconcile`

Manage branch merge reconciliation for migration histories.

## `merge`

Reconciles divergent migration histories after a branch merge.

### Usage

```bash
$ dbwarden merge --database primary
$ dbwarden merge --database primary --force
$ dbwarden merge --database primary --rename-column users.username=users.handle
```

### Options

- `--database`, `-d`: Target database name
- `--rename-column`: Column rename to confirm (format: `table.old=new`)
- `--rename-table`: Table rename to confirm (format: `old=new`)
- `--force`, `-f`: Force marking hand-edited migrations
- `--commit`: Create a git commit with the changes
- `--json`: Output results as JSON
- `--verbose`, `-v`: Enable verbose logging

### What it does

1. Checks preconditions (clean working tree, no conflict markers)
2. Resolves merge-base state from git
3. Rebuilds current model state from merged models
4. Computes reconciliation diff
5. Probes persistent environments
6. Generates reconciliation migration
7. Marks branch migrations as superseded
8. Reports results

### Example

```bash
$ dbwarden merge --database primary
Merge reconciliation summary
  Merge base:        0004 (state checksum 9f2c...)
  Superseded:        0005_add_profile.sql, 0005_extend_billing.sql
  Reconciliation:    0006_merge_feature_a_feature_b.sql
  Environments:      staging: clean, production: clean
  Next steps:        commit; developers on feature branches: dbwarden rebase
Merge reconciliation complete.
```

## `rebase`

Recovers a disposable environment after a merge.

### Usage

```bash
$ dbwarden rebase --database local
$ dbwarden rebase --database local --yes
$ dbwarden rebase --database local --check
```

### Options

- `--database`, `-d`: Target database name
- `--yes`, `-y`: Skip confirmation prompts
- `--force`, `-f`: Force operation even against persistent environments
- `--check`: Only check what would happen, don't make changes
- `--verbose`, `-v`: Enable verbose logging

### What it does

1. Reads local applied migrations
2. Identifies superseded versions
3. Rolls back to merge-base (preferred) or resets (fallback)
4. Re-applies runnable chain
5. Verifies convergence

### Example

```bash
$ dbwarden rebase --database local
Found 2 superseded migration(s) applied: 0005, 0006
Rolling back to merge-base version 0004...
Rolled back to version 0004
Re-applying migrations...
Migrations re-applied successfully.
Verifying convergence...
Convergence verified.
Rebase complete.
```

## `reconcile`

Recovers a persistent environment after a dirty merge.

### Usage

```bash
$ dbwarden reconcile --environment staging
$ dbwarden reconcile --environment staging --dry-run
```

### Options

- `environment`: Environment name to reconcile (required)
- `--database`, `-d`: Target database name
- `--rename-column`: Column rename to confirm
- `--dry-run`: Only show what would happen
- `--verbose`, `-v`: Enable verbose logging

### What it does

1. Snapshots live environment
2. Diffs against merged models
3. Generates environment-specific reconciliation
4. Applies with lock discipline
5. Updates merge record

### Example

```bash
$ dbwarden reconcile --environment staging
Reconciling environment: staging
Environment: staging (persistent: true)
Snapshotting live environment...
Computing diff against merged models...
Generating environment-specific reconciliation...
Applying reconciliation...
Updating merge record...
Reconciliation for environment 'staging' complete.
```

## See also

- [Merge Handling](../advanced/merge-handling.md): Complete merge handling guide
- [Migration Locking](../advanced/migration-locking.md): How migrations are locked
- [Status](status.md): Check migration status
