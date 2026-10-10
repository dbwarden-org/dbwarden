# `data`

Inspect, validate, and audit declarative data migrations.

The `data` group renders the compiled data specification and plans produced from your declarations, while `data transition` works on individual historical transitions. Together they cover review and offline inspection; generation happens through `make-data-migration` and execution through `migrate`. For the full declaration surface, artifact contract, and recovery paths, see the [declarative data migrations reference](../data-migrations/reference.md). For a runnable walkthrough, see the [data migrations tutorial](../data-migrations/tutorial/index.md).

## Usage

```bash
$ dbwarden make-data-migration "seed countries" --database primary
$ dbwarden data render --database primary
$ dbwarden data describe --database primary --format markdown --output data-docs
$ dbwarden data docs --database primary --output data-docs
$ dbwarden data transition validate move_legacy_rows --database primary
$ dbwarden data transition dry-run move_legacy_rows --probes --database primary
$ dbwarden data transition audit 0001_move_legacy_rows --database primary
```

## `data` commands

### `data render`

Render the compiled data plan.

- `--database`, `-d` - Target database
- `--model` - Limit output to one model or table
- `--format` - `text`, `json`, `mapping`, `sql`, or `operations`
- `--include` - Sections to include: `schema`, `constraints`, `relationships`, `data`, `transitions`
- `--only-with-data` - Only include models that declare data
- `--relationship-depth` - Maximum relationship depth (0-5)
- `--show-managed-values` - Include managed row literals
- `--param` - Freeze a data expression parameter as `name=JSON` or `name=text`

### `data describe`

Describe compiled declarations as `text`, `json`, or `markdown`.

- `--database`, `-d`
- `--model`
- `--format` - `text`, `json`, or `markdown`
- `--output` - Write the description to a file
- `--include`, `--only-with-data`, `--relationship-depth`, `--show-managed-values`, `--param`

### `data docs`

Write per-model and per-transition Markdown documentation with a checksums manifest.

- `--output` (required) - Output directory
- `--database`, `-d`
- `--diagrams` - Include Mermaid diagrams (only `mermaid` is supported)
- `--show-managed-values`
- `--param`

## `data transition` commands

`data transition` and its alias `data-transition` expose the same commands.

### `data transition validate`

Validate compiled data declarations, optionally scoping to one transition by name or suffix. Exits non-zero when a declaration is invalid.

- `NAME` (optional) - Transition name or suffix
- `--database`, `-d`
- `--param`

### `data transition plan`

Render the frozen plan for one transition.

- `NAME` (required)
- `--format` - `text`, `json`, `sql`, `operations`, or `mapping`
- `--database`, `-d`, `--param`

### `data transition dry-run`

Preview a transition plan without writing. `--probes` executes read-only guard probes against the live database.

- `NAME` (required)
- `--probes` - Execute read-only guard probes
- `--database`, `-d`, `--param`

### `data transition render`

Render one transition's execution steps.

- `NAME` (required)
- `--format` - `text`, `json`, `mapping`, `sql`, or `operations`
- `--show-managed-values`, `--database`, `-d`, `--param`

### `data transition describe`

Describe one transition as `text`, `json`, or `markdown`.

- `NAME` (required)
- `--format` - `text`, `json`, or `markdown`
- `--output`, `--database`, `-d`, `--param`

### `data transition audit`

Verify the frozen artifact bundle for one transition and report its assignments, checksums, and declared transitions.

- `NAME` (required) - Frozen artifact name or suffix
- `--format` - `json` (default) or `text`
- `--database`, `-d`

### `data transition reconcile`

Inspect or resolve a data execution record after an uncertain outcome. Without `--apply` this is read-only; with `--apply` it requires `--decision abandon` or `--decision verified_retry`.

- `MIGRATION_ID` (required)
- `--apply` - Apply a safe reconciliation decision
- `--decision` - `abandon` or `verified_retry`
- `--database`, `-d`

### `data transition new`

Scaffold a new `DataTransition` declaration under the first configured `data_paths` directory.

- `NAME` (required) - New class identifier
- `--manual` - Start from a manual template
- `--from-table`, `--from-model`, `--source-snapshot` - Historical source
- `--to-model`, `--map`, `--key` - Target model, mappings, and identity
- `--source-key`, `--where` - Source identity and row filters
- `--preserve-source` / `--drop-source`, `--acknowledge-drop` - Completion policy
- `--coverage` - `all`, `subset`, `exactly_once`, or `all_assigned_once`
- `--overlap` - `error`, `fan_out`, or `priority`
- `--priority` - `Model:INTEGER`, one per target when `--overlap priority`
- `--database`, `-d`

## `make-data-migration`

Generate versioned managed-row and transformation migrations through the shared generation pipeline.

```bash
$ dbwarden make-data-migration "backfill country names" --database primary
$ dbwarden make-data-migration "seed lookup rows" --plan --show-managed-values
```

- `DESCRIPTION` - Description for the migration
- `--database`, `-d`
- `--offline` - Use the model state file instead of a live database
- `--dry-run` - Preview generated migrations without writing files
- `--plan` - Output migration plan JSON without writing files
- `--sql` - Output raw migration SQL to stdout without writing files
- `--param` - Freeze a data expression parameter as `name=JSON` or `name=text`
- `--show-managed-values` - Include data literals in `--plan` output
- `--split-at-severity` - Defer operations at or above `SAFE`, `INFO`, `WARN`, or `CRITICAL`

## Notes

- Ad-hoc reference data and logic-driven population belong in [seeds](../seeds.md); declarative data migrations are for changes that ship with a schema release.
- Generated migrations always produce `.sql`, `.plan.json`, and `.data.py` artifacts together; the frozen artifact is what executes.
- `--force` does not authorize data reapply; rolled-back data requires `migrate --reapply-data`.

See the generated [CLI option inventory](../reference/cli-options.md) for authoritative option types and defaults, and the [Python API inventory](../reference/python-api.md) for `dbwarden.data` exports.
