---
description: Organize larger dbwarden workflows, including multi-database projects, CI patterns,
  sandbox validation, and command conventions across environments.
---

# Workflows

This guide covers larger day-to-day workflows once the basics are in place.

## Multi-Database Projects

dbwarden can manage more than one database from one config source.

```python
from dbwarden import DbwardenDatabase


class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://user:password@localhost:5432/main"
    model_paths = ["app.models"]
    model_tables = ["users", "posts", "comments"]

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    database_type = "clickhouse"
    database_url_sync = "clickhouse://default:@localhost:8123/analytics"
    model_paths = ["app.analytics_models"]
    model_tables = ["events", "page_views"]
```

The equivalent `database_config(...)` function form remains supported and is
used by some plugins and integration examples.

Apply migrations per database:

```bash
$ dbwarden migrate --database primary
$ dbwarden migrate --database analytics
```

Show status across all configured databases:

```bash
$ dbwarden status --all
```

## Separate Model Sets

Each database should usually own a distinct model set through `model_paths`. When databases share the same models package, use `model_tables` to split ownership by table name. dbwarden validates overlapping paths unless `overlap_models=True` is set explicitly.

This prevents one model tree from being interpreted as belonging to multiple databases by accident.

## CI Workflows

A common CI pattern is:

```bash
$ dbwarden export-models --database primary
$ dbwarden make-migrations "ci validation" --offline --database primary
$ dbwarden check --database primary
```

This keeps schema generation deterministic and avoids depending on a live database in every pipeline step.

For a full example, see [Cookbook: Offline & CI](../cookbook/04-offline-ci.md).

## Sandbox Validation

Before applying migrations to a real environment, you can validate them in a temporary sandbox database.

```bash
$ dbwarden migrate --sandbox --database primary
```

This is especially useful for complex migrations, risky type changes, and CI gates.

See the [Architecture Deep Dive](../architecture-deep-dive.md) for a thorough explanation of sandbox validation.

## Baselines and Partial Applies

When integrating dbwarden into an existing environment, or when applying only part of a migration sequence, these patterns are common:

- `--baseline` marks the target migration as already applied without actually running it, useful for onboarding an existing database.
- `--partial` (via `--count` or `--to-version`) applies a subset of pending migrations instead of all of them.

```bash
$ dbwarden migrate --database primary --baseline --to-version 0005
$ dbwarden migrate --database primary --count 2
$ dbwarden rollback --database primary --to-version 0007
```

See the [CLI Reference](../cli-reference.md) for a full breakdown of these flags. Use these modes carefully. They are operational tools, not everyday authoring commands.

## Operational Command Pattern

A typical production-safe pattern is:

```bash
$ dbwarden check --database primary
$ dbwarden make-migrations "release change" --database primary
$ dbwarden migrate --database primary
$ dbwarden status --database primary
$ dbwarden history --database primary
```

This keeps planning, execution, and verification as separate visible steps.

## Rollback Command Pattern

When validating rollback quality, use a loop like this:

```bash
$ dbwarden migrate --database primary
$ dbwarden rollback --count 1 --database primary
$ dbwarden migrate --database primary
```

This verifies both directions of the migration before a release depends on them.

## Where to Go Next

- Use [Cookbook Overview](../cookbook/index.md) for full working flows
- Use [Configuration](../configuration/index.md) for deeper config behavior
- Use [CLI Reference](../cli-reference.md) for command details
