# 11. Review and generate

Declarations describe intent; a migration is what actually runs. This page turns
the declarations you wrote in earlier pages into reviewable, frozen artifacts.

## What you'll learn

- What the three paired migration artifacts are and how their manifest secures them
- How to inspect compiled semantics with `data render`, `data describe`, and `data docs`
- How to validate, plan, and probe a transition before generating anything
- When to use `make-data-migration` instead of `make-migrations`
- How to freeze parameters and generate offline for CI

## Prerequisites

- A working dbwarden project with a model `Data(DataMeta)` declaration (see the
  [basics example](../../../examples/data-migrations/basics/)).
- `dbwarden` on your `PATH`, and the project already initialized with `dbwarden init`.
- Earlier tutorial pages (see the [tutorial index](../index.md)), or equivalent
  familiarity with managed rows, derived values, validations, and transitions.

## Why artifacts, not declarations

Applying a migration never re-imports your Python declarations. Generation
compiles them once into an immutable bundle; apply consumes only that bundle. So
the review step is the whole point: whatever you see here is exactly what runs.
If you change a declaration later, you generate a *new* migration rather than
editing an old one.

## Step 1: Generate the bundle

From an example project, generate one unified schema and data migration:

```bash
dbwarden make-migrations "seed countries, currencies, and slugs"
```

```text
Generated: primary__0001_seed_countries_currencies_and_slugs.sql (8 ops, max severity WARN)
```

One command writes three paired files under `migrations/primary/`:

```text
primary__0001_seed_countries_currencies_and_slugs.sql
primary__0001_seed_countries_currencies_and_slugs.plan.json
primary__0001_seed_countries_currencies_and_slugs.data.py
```

- `.sql` is the reviewable `-- upgrade` and `-- rollback` output.
- `.plan.json` is the machine-readable contract: typed operations, canonical data
  spec, execution steps, safety information, and the integrity manifest.
- `.data.py` is the frozen canonical declaration data, written as a literal. It is
  audit data, never an execution input.

The `.plan.json` carries the manifest that binds the members together:

```json
"data_bundle": {
  "format_version": 1,
  "migration_id": "primary__0001_seed_countries_currencies_and_slugs",
  "semantic_checksum": "88e1bdbe…",
  "checksum": "853e6b59…",
  "components": {
    "sql":    {"sha256": "9564da88…"},
    "plan":   {"sha256": "76f213f4…"},
    "frozen": {"sha256": "2ddf9e0d…"}
  },
  "members": [
    {"name": "…sql",     "media_type": "application/sql",   "byte_length": 6930,   "sha256": "…"},
    {"name": "…plan.json","media_type": "application/json", "byte_length": 103058, "sha256": "…"},
    {"name": "…data.py",  "media_type": "text/x-python",    "byte_length": 13171,  "sha256": "…"}
  ]
}
```

Apply verifies every member before it writes anything, so a hand-edited SQL file
or a stale plan fails closed. The manifest records ordered names, media types, byte
lengths, and SHA-256 checksums for all three members.

Values are redacted by default. `--show-managed-values` reveals declaration
literals in plans and rendered output. SQL output intentionally shows literals
because it is meant for review - that is the `.sql` file's job.

## Step 2: Render the compiled operations

`data render` prints the compiled data specification. Run it *before* generating
to see the pending operations, because after generation the live declarations are
no longer a pending diff:

```bash
dbwarden data render --database primary --format operations
```

```text
c9e6163b4a6d166b3d72e4b226b52ecd8a33168171345c4249c27f4306a76d62 managed_rows countries INFO
529c1f3392406ba8ebb6500c66b2facfa5f2ee40e9a10f48e17c5cc1f32ff349 managed_rows currencies INFO
c1715070192f11c3668e4a432ecf71811fa7d4bbe16de86cad8310a330d4c812 managed_rows users INFO
2f376fed733af0d98ecfc77936819138f273177a8dba84fe1dd2ede6a61955ff transformations users WARN
f4011c0dbcedd56c02da823ba4f07cb7388d74bee53fcc3bb4dc4c9ad89ca289 validations users INFO
```

Each line is `<operation id> <kind> <table> <severity>`. The other render formats
are `text`, `json`, `mapping`, and `sql`; `--show-managed-values` reveals literals,
and `--include` / `--model` / `--only-with-data` narrow the view. Use `--format sql`
to see exactly the statements the bundle will emit.

## Step 3: Describe and document

`data describe` summarizes models, constraints, and declared data in a
human-readable form:

```bash
dbwarden data describe --database primary --format markdown
```

```text
# DBWarden data declarations

Database: `primary`

## Models

### countries

| Column | Type | Null | Default |
|---|---|---|---|
| code | VARCHAR(2) | no |  |
| name | VARCHAR(100) | no |  |
```

Describe formats are `text`, `json`, and `markdown` (add `--output <path>` to write
a file). For a full documentation tree, generate pages and a manifest:

```bash
dbwarden data docs --database primary --output docs/data --diagrams mermaid
```

This writes `index.md`, model and transition pages under `models/` and
`transitions/`, and `checksums.json`. Generated documentation is never a
discovery source; it is for humans.

## Step 4: Validate, plan, and probe a transition

Transitions get their own review commands. Validation checks the live semantics;
planning renders the dependency-ordered operations; probes read the intended
database without writing:

```bash
dbwarden data transition validate SplitLegacyPeople --database primary
dbwarden data transition plan SplitLegacyPeople --database primary --format sql
dbwarden data transition dry-run SplitLegacyPeople --database primary --probes
```

```text
Data declarations are valid.
```

`plan --format json` gives the machine contract and `--format sql` gives the
statements. `dry-run --probes` executes only read-only guards and reports the
observed counts, scope checksums, database identity, isolation level, thresholds,
and evidence checksums. It does not predict post-write values when the target
schema or rows do not exist yet - you may see:

```text
(sqlite3.OperationalError) no such table: customer
```

That is expected before the first apply: probes that require the new schema are
reported as unavailable rather than guessed.

## Step 5: Choose the generator

`make-data-migration` uses the same generator with schema changes excluded. It is
for data-only releases where target tables already exist:

```bash
dbwarden make-data-migration "seed reference data"
```

```text
Generated: primary__0001_seed_reference_data.sql (5 ops, max severity WARN)
```

Use `make-migrations` when target tables or columns must change with the data. It
unifies schema and data operations into one dependency-ordered migration, and it
removes a duplicate schema `drop_table` when a transition already owns that source
removal.

## Step 6: Parameters and offline generation

Expressions may reference parameters. Freeze them with repeated `--param`, parsing
each value as JSON when possible and as a string otherwise:

```bash
dbwarden make-migrations "seed reference data" --param region='"EU"' --param min_id=10
```

Missing or duplicate names fail, and the resolved canonical values are stored in
the frozen artifacts - execution never rereads parameters.

Offline generation reads saved model state, declarations, and pinned snapshots
without a target database connection:

```bash
dbwarden export-models
dbwarden make-migrations "offline data change" --offline --database primary
```

```text
Exported 3 model(s) to .dbwarden/model_state.primary.json
```

A historical source without a valid snapshot fails before SQL generation, so
offline mode is safe for CI but still requires pinned lineage.

## Recap

- Generation compiles declarations into `.sql`, `.plan.json`, and `.data.py`, bound
  by a manifest of names, media types, lengths, and SHA-256 checksums.
- Apply consumes only the frozen bundle, so review the bundle, not the declarations.
- `data render` / `data describe` / `data docs` inspect semantics; `data transition
  validate|plan|dry-run` review a transition; probes are read-only.
- Use `make-data-migration` for data-only releases and `make-migrations` when schema
  changes travel with the data.
- `--param` freezes values and `--offline` generates without a database connection.

## What's next

Apply, roll back, and reapply the bundle you just reviewed:
[12. Apply, roll back, and reapply](./12-apply-rollback-reapply.md).
