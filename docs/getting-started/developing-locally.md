---
description: Use DBWarden locally with dev mode, SQLite translation, reverse-engineering,
  diffing, safety checks, and offline migrations.
---

# Developing Locally

This guide covers the local development workflow: using a development database, checking diffs safely, reverse-engineering models, and generating offline migrations.

## Use Dev Mode

Dev mode swaps the configured database target for `dev_database_url` and `dev_database_type`.

Configuration example:

```python
from dbwarden import database_config


primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:password@localhost:5432/main",
    model_paths=["app.models"],
    model_tables=["users", "posts", "comments"],
    dev_database_type="sqlite",
    dev_database_url="sqlite:///./development.db",
)
```

Run local commands against the development target:

```text
$ dbwarden --dev make-migrations "test local change" --database primary
Created migration: migrations/primary/primary__0002_test_local_change.sql
$ dbwarden --dev migrate --database primary
Applying migration: primary__0002_test_local_change.sql
Migration applied successfully
```

## SQLite Translation

Using SQLite in dev mode is common, but not every type or default translates perfectly from server databases. DBWarden handles this by translating backend-specific types and warning when fidelity is reduced.

If you want those warnings to become hard failures, use:

```bash
$ dbwarden --dev --strict-translation make-migrations "validate translation" --database primary
```

## Check the Planned Changes

Use `diff` when you want to inspect differences without writing files:

```bash
$ dbwarden diff --database primary
```

Use `check` when you want a safety classification:

```text
$ dbwarden check --database primary
SAFE      add column users.bio
WARN      shrink varchar users.email
CRITICAL  drop table audit_log
```


## Offline Migrations

Offline migrations let you generate SQL without connecting to a live database. The workflow is:

1. Export the current model state.
2. Change your models.
3. Generate a migration with `--offline`.

Commands:

```bash
$ dbwarden export-models --database primary
$ dbwarden make-migrations "offline schema change" --offline --database primary
```

This is useful for CI, restricted environments, and workflows where the migration plan should not depend on a live database connection.

For a full walkthrough, see [Cookbook: Offline & CI](../cookbook/04-offline-ci.md).

## Local Validation Loop

A practical local loop looks like this:

```bash
$ dbwarden --dev diff --database primary
$ dbwarden --dev check --database primary
$ dbwarden --dev make-migrations "local change" --database primary
$ dbwarden --dev migrate --database primary
$ dbwarden --dev status --database primary
```

This keeps feedback fast while still using the same toolchain you use in production.

Next, continue with [Workflows](workflows.md).
