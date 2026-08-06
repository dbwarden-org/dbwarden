# 6. Safety & Impact Analysis

Schema changes are the highest-risk operation in most deployments. Dropping a column that application code still references causes runtime errors. Changing a column type can break queries. DBWarden provides two tools to detect these issues before deploy: `check` classifies every migration operation by danger level, and `check-impact` finds affected code references.

For complete documentation see the [`check`](../commands/check.md) and [`check-impact`](../cli-reference.md) command references.

## What You'll Learn

- How `dbwarden check` classifies operations by danger level
- How `dbwarden check-impact` finds code references affected by a migration
- How to detect destructive changes before they reach production

## Prerequisites

- Completed [Section 3](03-apply-and-inspect.md) (migrations applied)
- `examples/core/` project

## Step 1: Safety Check

```bash
cd examples/core
bash scripts/06-safety-impact.sh
```

The key command:

```bash
$ dbwarden check --database primary
```

This scans every migration file and classifies each SQL operation by safety level:

| Level | Meaning |
|-------|---------|
| **SAFE** | No data loss risk (add table, add nullable column, create index) |
| **INFO** | Metadata changes (comments, renames) |
| **WARN** | Potential impact (change column type, drop default) |
| **CRITICAL** | Destructive (drop table, drop column, remove NOT NULL) |

Output for our baseline migrations:

```
Safety Check - primary
┌──────────┬──────────────┬──────────┬────────┬─────────┬───────────────┐
│ Severity │ Change       │ Table    │ Column │ Message │ Required Flag │
├──────────┼──────────────┼──────────┼────────┼─────────┼───────────────┤
│ SAFE     │ create_table │ users    │        │         │               │
│ SAFE     │ create_table │ posts    │        │         │               │
│ SAFE     │ create_table │ products │        │         │               │
│ SAFE     │ create_table │ tags     │        │         │               │
│ SAFE     │ create_index │          │        │         │               │
│ INFO     │ comment_on   │ users    │        │         │               │
└──────────┴──────────────┴──────────┴────────┴─────────┴───────────────┘
```

A migration with a destructive change would show:

```
┌──────────┬──────────────┬───────┬──────────┬─────────┬───────────────┐
│ Severity │ Change       │ Table │ Column   │ Message │ Required Flag │
├──────────┼──────────────┼───────┼──────────┼─────────┼───────────────┤
│ CRITICAL │ drop_column  │ users │ username │         │               │
└──────────┴──────────────┴───────┴──────────┴─────────┴───────────────┘
```

This gives you a quick visual signal during code review: if a migration contains CRITICAL operations, it needs extra scrutiny.

## Step 2: Code Impact Analysis

```bash
$ dbwarden check-impact 0001 --database primary
```

`check-impact` scans your application code (not just migration files) for references that would be affected by a migration. It uses AST analysis with a grep fallback:

```
No impact detected
Scanned: .
```

A more realistic scenario with a destructive change:

```
Migration: 0002_drop_username
Impact detected: 1 operation(s) affect code

drop_column on users.username
  References: 2
    app/routes/users.py:34  attribute_access
      .username
    app/templates/profile.jinja2:12  grep
      user.username
```

The scan finds each reference, identifies the access pattern (attribute access in Python, grep match in templates), and reports the file and line number.

### How It Works

1. Reads the migration's plan file and parses the schema changes
2. Identifies schema changes (DROP COLUMN, ALTER COLUMN TYPE, etc.)
3. Scans `.py` files using Python's `ast` module for attribute access patterns
4. Falls back to grep for non-Python files (templates, configs, etc.)
5. Reports all references grouped by change type

### Flags

- `--scan-path app/`: limit scanning to a specific directory (default: project root)
- `--deep`: also scan dependencies (imported packages)
- `--out json`: output as JSON for CI processing
- `--verbose`: show scan progress

## Pre-Deploy Workflow

Combine both tools for a safe deploy sequence:

```bash
# 1. Check migration safety
$ dbwarden check --database primary

# 2. Check code impact
$ dbwarden check-impact 0042 --database primary

# 3. Only proceed if no unexpected CRITICAL or WARN items
$ dbwarden migrate --database primary
```

In CI:

```yaml
- name: Safety check
  run: dbwarden check --database primary
- name: Impact analysis
  run: dbwarden check-impact 0042 --database primary
- name: Apply (only if previous steps succeeded)
  run: dbwarden migrate --database primary
```

## Key Takeaways

- `check` classifies every migration operation by safety level using a severity table
- `check-impact` finds code references affected by a migration using AST + grep
- Together they catch breaking changes before deploy
- CRITICAL operations aren't blocked; they're flagged for human review
- Use `--out json` for CI integration

## Related Documentation

- [`check` command](../commands/check.md)
- [`check-impact` command](../cli-reference.md) (see CLI reference)
- [Safe Deployment](../advanced/safe-deployment.md)

## Next

[Section 7: Seeds](07-seeds.md)
