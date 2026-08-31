# check-impact

Scan your codebase for references to schema elements affected by a migration.

## Usage

```bash
dbwarden check-impact 0042 --database primary
dbwarden check-impact 0042 --database primary --out json
dbwarden check-impact 0042 --database primary --scan-path app/
dbwarden check-impact path/to/primary__0042_add_bio.plan.json
```

## What it does

`check-impact` reports which code references would be affected by a migration before you apply it. It uses AST analysis with a grep fallback, so results reflect actual code structure rather than text matches.

Run this before any destructive deploy (column drops, type changes, table renames) to surface breaking changes before they reach production.

## Options

| Option | Description |
|--------|-------------|
| `migration` | Migration version (e.g. `0042`) or path to a plan JSON file (required) |
| `--database`/`-d` | Target database name |
| `--out`/`-o` | Output format: `text` (default) or `json` |
| `--scan-path` | Directory to scan for affected code (default: `.`) |
| `--deep` | Enable deep introspection (imports models live) |
| `--verbose`/`-v` | Include INFO-level operations in the scan |

## Output

```
drop_column on users.username
  References: 2
    app/routes/users.py:34  attribute_access
      .username
    app/templates/profile.jinja2:12  grep
      user.username
```

Each affected operation lists the schema element, the number of references found, and the specific files and line numbers where those references occur.

## See also

- [check](check.md) - Classify operations by danger level
- [Safety & Impact cookbook](../cookbook/06-safety-impact.md) - Practical examples
