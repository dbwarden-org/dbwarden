# Declarative data migrations

Declarative data migrations describe intended rows and transformations beside SQLAlchemy models. DBWarden compiles declarations into a canonical data specification, SQL, a checksummed plan, and a frozen `.data.py` artifact. Applying a migration uses those artifacts. It does not import current declarations.

Use this for reviewed changes to data that belongs with a schema release. Declarations generate versioned migrations; they cannot be placed in repeatable migration files. Keep one-off repair SQL and application backfills separate.

## Configure discovery

`model_paths` contains mapped SQLAlchemy models. `data_paths` optionally contains modules with `DataTransition` declarations. DBWarden does not treat `*.data.py` files as live declarations.

```python
from dbwarden import database_config

database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://localhost/app",
    model_paths=["app/models.py"],
    data_paths=["app/data"],
    data_snapshot_dir=".dbwarden/data",
    snapshot_registry=".dbwarden/snapshots/registry.json",
)
```

`model_paths` and `data_paths` name Python files or directories, not dotted module names. Missing configured paths fail discovery. Symlink paths, duplicate declaration IDs, duplicate owners of a table, and reserved DBWarden tables are rejected. `model_tables` restricts model discovery, including model data declarations. Two database entries cannot use the same live data path unless their existing `overlap_models=True` opt-in permits it. `data_snapshot_dir` and `snapshot_registry` are relative paths. They hold compiled data snapshots and historical schema registry metadata.

Declaration IDs default to the project-relative module path and class name. Set `declaration_id` on a mapped model or transition to retain its identity when moving code. Keep an ID only for the same logical owner. Changing managed key columns or owned columns under an existing ID fails planning.

## Managed rows

Put a `Data` inner class on a mapped model. `rows()` declares key columns, literal values, columns owned by the declaration, missing-row handling, and rollback policy.

```python
from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from dbwarden.data import DataMeta, rows

class Base(DeclarativeBase):
    pass

class Country(Base):
    __tablename__ = "countries"
    code: Mapped[str] = mapped_column(String(2), primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    class Data(DataMeta):
        managed_rows = rows(
            key="code",
            rows=[{"code": "UY", "name": "Uruguay"}],
            owned_columns=["name"],
            rollback="restore_previous",
        )
```

`rows()` accepts exactly one of `rows=` and `source=`. `source=` names a static CSV or JSON file. It is read during compilation and becomes canonical values in the frozen specification. Keys and owned columns must exist on the target table. `on_missing="delete"` requires a scope expression and `acknowledge_delete=True`. Choose `rollback="restore_previous"` when the generated migration can restore prior values, or `"irreversible"` when it cannot.

Omitted keys use the model's primary key. Omitted `owned_columns` use the declared non-key columns. DBWarden records ownership separately from application rows. Missing-row deletion only removes previously owned rows inside the explicit scope; it does not adopt every row with a matching key. A pre-existing row is accepted only under the declaration's value and ownership checks. Rollback checks current owned values before changing them, so later application edits cause a conflict instead of being overwritten.

CSV and JSON inputs must stay inside the project. CSV values are converted using column types. JSON rejects duplicate keys and non-finite numbers. Normalized strings and typed literals determine checksums, not source-file formatting. Removing a managed-row declaration does not silently delete its rows: first declare the intended deletion and generate its migration.

## Derived values and expressions

`derive()` owns one target column inside its declared domain. Expressions are typed ASTs, not Python callbacks or raw SQL.

```python
from dbwarden.data import DataMeta, col, derive, func

class Data(DataMeta):
    transformations = [
        derive("normalized_name", func.lower(col("name")), rollback="clear"),
    ]
```

Use `col(name, table=None)`, `literal(value)`, `param(name)`, `case(...)`, `cast(...)`, `mapping(...)`, and `func.<name>(...)` to build expressions. Arithmetic, comparisons, null tests, membership, and Boolean composition are supported. Combine predicates with `&`, `|`, and `~`; Python `and`, `or`, and `not` cannot build expressions. The function allowlist is `lower`, `upper`, `trim`, `concat`, `coalesce`, `json_extract`, `split_part`, and PostgreSQL `date_trunc`. Arbitrary functions, Python callbacks, raw SQL, volatile values, and unsupported casts fail compilation.

The compiler checks column types, nullability, ownership, and expression domains. A partial `case` or `mapping` needs an explicit fallback or a domain that proves it complete. `derive(..., when=..., on_unmatched="error" | "keep", max_rows=...)` controls its domain and affected-row limit. Rollback is required: `clear` requires a nullable target; `recompute` requires a previous expression that can be restored; `capture` records a guarded typed preimage; `irreversible` blocks rollback.

`date_trunc` requires `settings` containing nonempty `backend_version`, `timezone`, `collation`, `search_path`, and `encoding` strings. These settings become part of expression identity and are checked against PostgreSQL before execution. A numeric version pin matches the server major version; other version strings must match exactly.

Use `func.json_extract(value, "$.path")` for scalar JSON extraction and
`func.split_part(value, delimiter, index)` for one-based string splitting.
JSON paths and delimiters must be nonempty literals; the path begins with `$`.
JSON strings are decoded, numbers become text, booleans become `true` or
`false`, and JSON null, missing paths, objects, and arrays become SQL null.
ClickHouse rejects `json_extract` because its native result cannot preserve
that contract. The split index is a literal integer from 1 through 64. SQL
null stays null and a missing component returns an empty string. `split_part`
works on all five backends. DBWarden freezes both canonical expressions.

Supply parameters through repeated `--param NAME=VALUE` options on generation and data inspection commands, or through the Python generation API's `parameters` mapping. Values parse as JSON when possible, otherwise as strings. Missing or duplicate names fail. Frozen artifacts contain resolved canonical values, so execution does not reread parameters.

## Validation rules

`Data.validations` holds `validate(expression, message=...)` declarations. Each expression must hold for every target row; false and null results fail. Validation runs after relevant data writes and during `check --data`. Validation-only declarations need no rollback policy because they do not change rows.

```python
from dbwarden.data import DataMeta, col, validate

class Data(DataMeta):
    validations = [validate(col("name").is_not(None), message="Name is required")]
```

## Historical transitions

Use `historical_table()` to name a source table pinned to a snapshot. `DataTransition` sends source rows to one or more current mapped models with `into()`.

```python
from dbwarden.data import DataTransition, historical_table, into
from app.models import Customer, Person

legacy = historical_table("legacy_people", snapshot="primary__0007")

class SplitLegacyPeople(DataTransition):
    source = legacy
    source_identity = [legacy.id]
    overlap = "fan_out"
    targets = [
        into(
            Person,
            map={Person.id: legacy.id, Person.name: legacy.full_name},
            key=[Person.id],
            on_conflict="ignore_if_equivalent",
        ),
        into(
            Customer,
            map={Customer.person_id: legacy.id},
            key=[Customer.person_id],
            on_conflict="ignore_if_equivalent",
        ),
    ]
```

An explicit snapshot ID is recommended and required when compatible lineage is
ambiguous. Omitting it invokes the generation-time latest-compatible selection
described below; the frozen bundle still contains one exact ID. Current ORM
models describe targets. Historical source tables come from the pinned snapshot
and are not rediscovered at execution. A transition may preserve its source or
drop it only with the declaration's explicit completion and rollback rules. The
unified generation path removes the duplicate schema `drop_table` operation
when the transition owns that source removal.

Python target mappings use `{Target.column: source_expression}`. Target keys must identify stable rows. `on_conflict` is `error`, `ignore_if_equivalent`, or `overwrite`; overwrite requires `acknowledge_overwrite=True`. Equivalent pre-existing rows remain unowned, so rollback does not delete them. Identity ledgers bind each source key to its target key and mapped values.

| Setting | Behavior |
|---|---|
| `coverage="all"` | Every source row must match a target. |
| `coverage="subset", on_unmatched="ignore"` | Unmatched source rows are permitted. |
| `coverage="exactly_once_match"` | Each source row must match exactly one raw target predicate. |
| `coverage="all_assigned_once", overlap="priority"` | Each source row must have exactly one assignment after priority resolution. |
| `overlap="error"` | A source row matching multiple targets fails. |
| `overlap="fan_out"` | One source may populate several targets. |
| `overlap="priority"` | Distinct integer target priorities choose the first match; requires `all_assigned_once`. |
| `on_complete="keep"` | Keep a mapped current-model source. Requires `source_snapshot=...` and `on_unmatched="error"`. |
| `on_complete="preserve"` | Rename the source to its deterministic preservation table. |
| `on_complete="drop"` | Drop the source; requires `acknowledge_drop=True` and `rollback="irreversible"`. |
| `rollback="restore_preserved_source"` | Restore preserved source and remove only verified writes owned by the transition. |
| `max_rows` | Abort when source count exceeds the declared limit. |

Target foreign keys determine write order. Unsupported cycles, ambiguous identities, overlapping ownership, or references that would be broken by source removal fail before execution. Nullable expansion, data writes, validation, and non-null contraction share one dependency-ordered plan. A transition owns its source removal, preventing duplicate schema drops.

The older `exactly_once` spelling normalizes to `exactly_once_match`; `coverage="all"` with priority normalizes to `all_assigned_once`. Frozen plans store the canonical mode. Priority values on a non-priority transition are rejected.

To copy from a model that remains part of desired schema, assign the mapped
model class to `source`, set its exact `source_snapshot`, and select
`on_complete="keep"`. DBWarden requires complete coverage with
`on_unmatched="error"` and leaves the source unchanged on rollback. PostgreSQL
and SQLite keep the source name throughout. MySQL and MariaDB temporarily
freeze it with an atomic rename, verify the target, and restore its name before
success. A `historical_table(...)` or merged source cannot use `keep`.

## Deterministic merges

Use `merge_sources()` when several historical tables contribute to one current
target. Every source needs an exact snapshot, a stable identity, the same named
projection, and a distinct priority. Every non-key output needs an explicit
aggregate or winner rule.

```python
from dbwarden.data import (
    DataTransition,
    aggregate,
    batch,
    col,
    from_source,
    historical_table,
    into,
    merge_sources,
    winner,
)
from app.models import Customer

crm = historical_table("crm_customers", snapshot="primary__0010")
billing = historical_table("billing_customers", snapshot="primary__0010")

customers = merge_sources(
    from_source(
        crm,
        identity=[crm.id],
        map={
            "customer_id": crm.id,
            "display_name": crm.name,
            "balance": crm.balance,
        },
        priority=10,
    ),
    from_source(
        billing,
        identity=[billing.customer_id],
        map={
            "customer_id": billing.customer_id,
            "display_name": billing.name,
            "balance": billing.balance,
        },
        priority=20,
    ),
    key=["customer_id"],
    values={
        "display_name": winner(col("display_name")),
        "balance": aggregate("sum", col("balance")),
    },
    allow_empty=False,
)

class MergeCustomers(DataTransition):
    source = customers
    targets = [
        into(
            Customer,
            map={
                Customer.id: col("customer_id"),
                Customer.name: col("display_name"),
                Customer.balance: col("balance"),
            },
            key=[Customer.id],
            on_conflict="ignore_if_equivalent",
        )
    ]
    execution = batch(size=1000, key=["customer_id"], max_duration=60)
    on_complete = "preserve"
    rollback = "restore_preserved_source"
```

Supported aggregate names are `sum`, `avg`, `min`, `max`, `count`, and
`require_equal`. `count` counts non-null expression results.
`require_equal` fails when contributions differ or mix null with a non-null
value. For strings it compares UTF-8 bytes instead of the database's default
collation. `winner` selects the lowest numeric source priority, then orders by
the source identity's UTF-8 byte encoding. Source identities and grouping keys
must be non-null integers or strings; cast other types explicitly. Source
priorities must be distinct.

`allow_empty=False` is the default and stops before source retirement when all
inputs are empty. Set `allow_empty=True` only when an empty merged target is a
reviewed outcome. The flag changes merge identity. A merge supports preserved
or acknowledged dropped sources. Archive completion belongs to a normal
single-source transition.

DBWarden rejects arbitrary reducer functions, different projection fields,
nullable or duplicate source identities, missing grouping keys, ambiguous
field rules, repeated input relations, ambiguous qualified snapshot entries,
and merge revisions that reuse the old declaration. Create a new declaration
for changed merge semantics. ClickHouse rejects merges because it cannot
establish the required source-write boundary.

## Capture, archive, and batching

Use `rollback="capture"` on a transformation when rollback must restore the
actual typed value that existed before the generated write.

```python
from dbwarden.data import DataMeta, batch, col, derive, func

class Data(DataMeta):
    transformations = [
        derive(
            "normalized_name",
            func.lower(col("name")),
            rollback="capture",
            execution=batch(
                size=500,
                key=["id"],
                max_duration=30,
                statement_timeout=10,
                lock_timeout=5,
            ),
        )
    ]
```

Capture requires a target primary key. Apply stores the preimage before the
write in the same consistency boundary. Rollback checks that the current value
still matches the generated postimage. If application code changed it,
rollback stops rather than overwriting the later value.

`check --data` verifies the authenticated capture receipt and recorded
postimage. It does not reevaluate a captured expression or its domain on the
post-write row. That would misclassify valid one-shot expressions such as
`derive("value", col("value") + 1, rollback="capture")`.

Archive policies use `archive_table()` and an explicit archive acknowledgement.
They are available for scoped missing managed rows, unmatched transition rows,
and completed transition sources. An archive move writes or verifies the
archive row before removing the source row. The archive destination cannot be
a current managed model or overlap another incompatible declaration.

```python
from dbwarden.data import DataMeta, archive_table, col, rows

class Data(DataMeta):
    managed_rows = rows(
        key="code",
        rows=[{"code": "UY", "name": "Uruguay"}],
        owned_columns=["name"],
        on_missing="archive",
        scope=col("managed"),
        archive_to=archive_table("country_archive"),
        acknowledge_archive=True,
        rollback="restore_previous",
    )
```

For a transition, set `coverage="subset"`, `on_unmatched="archive"`, and
`unmatched_archive_to=archive_table(...)` to move unmatched rows. Set
`on_complete="archive"` and `archive_to=archive_table(...)` to retain the
completed source under the declared archive name. Either form requires
`acknowledge_archive=True`. `rollback="capture"` records overwritten target
preimages; `restore_preserved_source` restores a preserved or archived source
after verified target cleanup. Archive destinations cannot reuse an internal
name, a desired model, the source or target itself, or another declaration's
archive destination.

`batch()` uses ascending keyset ranges and never uses `OFFSET`. The key must be
the source identity for a transition or the target primary key for a
transformation, must be non-null and unique, and cannot be modified by the
statement. All chunks remain in one statement transaction and ownership
receipt. They do not commit or resume independently. A timeout or lag violation
rolls back that statement.

`max_duration` is one cooperative budget across every step and chunk in the
declaration attempt. A retry starts a new budget. The executor checks the
budget before and after SQL and between chunks; `statement_timeout` interrupts
supported statements independently. Duration, statement, lock, and lag limits
are finite positive seconds.

Backend controls differ. PostgreSQL supports statement, lock, duration, and
replication-lag limits. MariaDB supports its native statement limit; MySQL
rejects a requested DML statement timeout and supports the other admitted
controls. SQLite supports duration, statement interruption, and lock timeout,
but rejects replication-lag limits. ClickHouse rejects keyset batching.

## Historical snapshots

Use an explicit snapshot ID when branch intent matters. You may omit
`snapshot=` during authoring to request the newest compatible snapshot on one
unambiguous registered lineage. Generation then freezes the concrete snapshot
ID, database, backend, schema checksum, and parents into the bundle. Multiple
heads, multiple newest compatible candidates, a missing table, or invalid
lineage fails and asks for `snapshot=`. Execution never performs this search or
substitutes another snapshot. `latest_previous` is not an accepted alias.

For external snapshot import, use `dbwarden.data.snapshots.register_snapshot(snapshot_id, schema_state, database=..., backend=..., parent_snapshot_ids=..., registry_path=...)`. `resolve_snapshot(snapshot_id, database=..., backend=..., registry_path=..., schema_dir=...)` reads the pinned entry without changing the registry. Reusing an ID with different content fails. Existing schema snapshot files must carry their own valid checksum before they can be resolved without a registry entry.

Registry writes use a local operating-system file lock and atomic replacement, including offline imports. Parent IDs are sorted and must identify registered snapshots in the same database and backend. Resolution rejects missing parents, cycles, and duplicate IDs. Pass both branch snapshot IDs as parents when importing a merge or squash snapshot; an imported root may have no parents.

## Generate and inspect

Use the shared generator so schema and transition work become one migration when a source table changes.

```text
dbwarden make-migrations "introduce country data" --offline --database primary
dbwarden data render --database primary --format operations
dbwarden data describe --database primary --format markdown
dbwarden data docs --database primary --output docs/data --diagrams mermaid
dbwarden data transition validate SplitLegacyPeople --database primary
dbwarden data transition plan SplitLegacyPeople --database primary --format sql
dbwarden data transition dry-run SplitLegacyPeople --database primary --probes
dbwarden migrate --database primary --dry-run --data
dbwarden check --database primary --data
dbwarden diff --database primary --data
```

`data render` and `data describe` accept `--model`, `--include`, `--only-with-data`, and `--relationship-depth` (0–5). Include sections are `schema`, `constraints`, `relationships`, `data`, and `transitions`. Render formats are `text`, `json`, `mapping`, `sql`, and `operations`; describe formats are `text`, `json`, and `markdown`, with optional `--output`. Values are redacted by default. `--show-managed-values` reveals declaration literals. SQL output intentionally shows SQL literals because it is for review. `data docs` writes an index, model and transition pages, and `checksums.json`.

Render, describe, and docs can read the latest non-superseded frozen bundle when configured live inputs have been removed. This preserves the stored model schema and declarations without importing a frozen Python file. Validation and generation still require live inputs; malformed live code is not silently replaced by old artifacts.

`transition new --manual Name` writes a disabled draft beneath a configured `data_paths` directory. Fill in its source, identity, and targets before enabling it. Its direct form accepts `--from-table` or `--from-model`, `--source-snapshot`, repeated `--to-model`, `--map source:Model.target`, `--where`, source and target keys, coverage, overlap, and preserve or drop-source flags. Direct predicates accept column equality with a literal; complex predicates belong in the Python declaration. Interactive authoring requires a terminal. Existing files are not overwritten.

For direct priority authoring, pass `--coverage all_assigned_once --overlap priority` and one `--priority Model:INTEGER` per target. Values must be distinct; lower numbers win.

The transition group contains `audit`, `describe`, `new`, `render`, `validate`,
`plan`, `dry-run`, and `reconcile`. The compatibility group
`dbwarden data-transition` exposes the same commands.
For direct transition creation, use `--source-key`, `--key`, `--coverage`,
`--overlap`, `--preserve-source` or `--drop-source`, and `--acknowledge-drop`
when source removal needs acknowledgement. `dbwarden make-data-migration` and
`dbwarden make-migrations` accept repeatable `--param NAME=VALUE` generation
parameters.

Offline generation reads saved model state, declarations, and pinned snapshots. It does not need a target database connection. A historical source without a valid snapshot fails before SQL generation. `make-data-migration` uses the same generator with schema changes excluded. Use `make-migrations` when target tables or columns must change with the data.

## Complete review and execution flow

The following sequence uses the same compiled semantics from declaration review
through convergence.

1. Configure `model_paths`, `data_paths`, `data_snapshot_dir`, and
   `snapshot_registry`. Register any externally supplied historical snapshot.
2. Add the declaration beside its model or under `data_paths`. Give declarations
   explicit stable IDs before moving or renaming their Python definitions.
3. Validate and inspect the live semantics:

   ```text
   dbwarden data transition validate --database primary
   dbwarden data render --database primary --format operations
   dbwarden data transition plan SplitLegacyPeople --database primary --format json
   ```

4. Run read-only probes against the intended database. Review the database
   identity, snapshot, isolation level, observed counts, and accepted limits:

   ```text
   dbwarden data transition dry-run SplitLegacyPeople --database primary --probes
   ```

5. Generate one unified schema/data migration. Omit `--offline` when generation
   must collect live review evidence:

   ```text
   dbwarden make-migrations "split legacy people" --database primary
   ```

6. Review all three generated members together. Audit verifies their manifest
   and statically reads the frozen declaration:

   ```text
   dbwarden data transition audit primary__0012_split_legacy_people --database primary
   ```

7. Preview the pending frozen bundle selected by version and safety policy. This
   does not import the current declarations:

   ```text
   dbwarden migrate --database primary --dry-run --data
   ```

8. Apply under the normal migration lock. Use `--force` only when the reviewed
   severity requires destructive acknowledgement:

   ```text
   dbwarden migrate --database primary --force
   ```

9. Verify current state from desired declarations and applied frozen history:

   ```text
   dbwarden check --database primary --data
   dbwarden diff --database primary --data
   ```

   `check --data` succeeds only when the schema, pending migration SQL, and
   declared data converge. `--force` can acknowledge reviewed `WARN` and
   `CRITICAL` safety findings, but it cannot bypass unknown SQL classification
   or data drift.

If apply fails before any durable effect, the journal records a retryable
failure and a later ordinary migrate may retry it. If an effect may have
occurred, the state is `UNKNOWN_REQUIRES_RECONCILIATION` and ordinary migrate
stops. Inspect first:

```text
dbwarden data transition reconcile primary__0012_split_legacy_people --database primary
```

After repairing or verifying database state, record only a decision the
reported state permits:

```text
dbwarden data transition reconcile primary__0012_split_legacy_people \
  --database primary --apply --decision verified_retry
```

`verified_retry` does not ignore conflicts or infer ownership from equivalent
values. `--decision abandon` is an audited terminal decision where supported.
It does not mark the declared state converged.

Rollback uses the frozen rollback plan and refuses irreversible declarations or
changed owned values:

```text
dbwarden rollback --database primary --count 1
```

A completed data rollback records `ROLLED_BACK`. Ordinary migrate does not
silently replay it. Reapply is a separate operator decision and creates a new
linked epoch:

```text
dbwarden migrate --database primary --reapply-data
```

Baseline records history without executing the data plan. It requires an exact
target version and stores the explicit acknowledgement, reason, and skipped
checks. It does not prove current data convergence:

```text
dbwarden migrate --database primary --baseline --to-version 0012
```

## Artifacts and integrity

A data migration writes three paired files:

- `.sql` contains reviewable upgrade and rollback SQL.
- `.plan.json` contains typed operations, canonical data specification, execution steps, safety information, and integrity manifest.
- `.data.py` contains the frozen canonical declaration data.

The manifest binds ordered member names, media types, byte lengths, and SHA-256 checksums for SQL, plan, and frozen artifact. The plan records semantic identity separately from probe evidence. Canonical JSON normalizes Unicode and rejects duplicate normalized keys, non-finite values, and unsupported literal types. Static input rows are embedded; replay does not depend on the original CSV or JSON file.

The plan member's hash and length cover its canonical JSON payload with `data_bundle` omitted; they do not measure the indented file that also stores the manifest. SQL and frozen Python hashes cover their UTF-8 text. This keeps each checksum outside its own hash input.

`transition audit` reads the frozen file with the AST reader, verifies the bundle, and does not import it. A merge that supersedes a data bundle leaves its SQL unchanged and writes a `.superseded.json` sidecar bound to the complete original SQL checksum. Tampering makes the marker fail closed. File publication restores prior contents on failure and publishes the plan last.

## Apply, journal, rollback, and reconciliation

Execution verifies the complete bundle before writes and uses the existing database migration lock. Backend behavior differs:

| Backend | Execution boundary |
|---|---|
| PostgreSQL | One transaction includes data and success history. Existing referenced tables are locked against competing writes through guards and mutation. |
| SQLite | An explicit immediate transaction covers guards, writes, and success history. |
| MySQL / MariaDB | Durable per-statement checkpoints account for implicit DDL commits. Insert ownership requires transactional DML and InnoDB target/checkpoint tables. Interrupted statements require reconciliation; a transaction rollback is not treated as proof that all effects vanished. |
| ClickHouse | Versioned append-only journal records, synchronous mutations, and mutation-status checks. Managed rows and transformations require irreversible rollback policy. Historical transitions, merges, archive, capture rollback, batching, and managed-row `on_missing="delete"` are unsupported. |

Autocommit or concurrent schema steps cannot be combined with a data bundle. Unsupported backend operations fail during planning rather than emitting placeholder SQL.

Recorded native evidence includes six lifecycle flows on MySQL 8.4.11 and the
same six independently on MariaDB 11.4.4: managed revision and reapply,
two-revision archive rollback, captured derivation rollback, preserved
transition rollback, batched merge round trip, and retained-source tamper
refusal. Connection-loss, process-crash, and broader-version qualification
remain release gates. No live ClickHouse result is claimed.

`_dbwarden_data_runs` records migration ID, application epoch, bundle checksum, run ID, status, last completed operation, sanitized error, baseline flag, and timestamps. `_dbwarden_data_events` records state transitions; nontransactional backends also persist statement checkpoints. A successful checksum-matched migration returns as already applied. Changed checksums fail. A fully rolled-back transactional failure is retryable; uncertain durable effects produce `UNKNOWN_REQUIRES_RECONCILIATION` and block automatic replay.

On MySQL and MariaDB, claimed insert counts must match the driver's affected-row count. The executor commits a `WRITE_VERIFIED` checkpoint in the same DML transaction as the inserted rows. An equivalent concurrent application insert cannot become migration-owned merely by passing a value comparison. Count mismatches produce terminal `FAILED_FINAL`; a crash with only a `PENDING` ownership insert cannot be reconciled from equivalent target values alone.

Generated MySQL/MariaDB rollback steps persist intent before acquiring row locks. Pure DML steps then keep `SELECT ... FOR UPDATE` locks through identity verification, guards, mutation, and the `DONE` checkpoint commit. Target and checkpoint tables must use InnoDB with autocommit disabled. DDL runs in separate steps because it can commit implicitly.

MySQL and MariaDB rename a historical source to a reserved internal name before capturing its rows. Writes through the original name then fail while the transition runs. A failed attempt can leave that internal source in place for explicit reconciliation. Rollback restores the original name after target cleanup succeeds.

A MySQL/MariaDB merge freezes all input sources in one multi-table rename, so
no source remains writable after another source has reached the merge snapshot.
Merge staging, aggregate guards, target ownership, contribution edges, and
source retirement remain ordered journal steps.

Captured values, archive receipts, and merge contribution edges live in
reserved internal tables and are authenticated or checksum-bound to the frozen
plan. Back them up with the execution journal. Removing them can make rollback
or convergence unverifiable.

Batched statements call fencing and progress hooks for each emitted chunk. The
operation becomes complete only after every chunk, guard, edge record, and
postcondition succeeds. Chunks never create independent application epochs.

Dry-run probes read the original source name without renaming it. Apply-only DDL existence checks run during execution. Later convergence checks use the preserved source and omit temporary rename-state checks.

ClickHouse managed-row deletion is rejected because the backend does not provide the atomic ownership receipt needed to distinguish migration inserts from concurrent application inserts. Historical transitions are also rejected: its nonblocking rename cannot establish the source-write barrier needed for complete capture and safe source retirement.

Retained identity edges bind source identity, target identity, and mapped values with HMAC authentication using a per-database key. Convergence and rollback verify those edges against current target rows. Treat the key and journal tables as part of the database backup. Baseline application records `baseline=True` plus an acknowledgement, reason, and skipped check identifiers in its event. The Python executor accepts `baseline_reason`; the CLI records that baseline was explicitly requested. This does not prove that declared data already exists.

Distinct source keys that collapse to the same normalized identity are rejected. On transactional backends, that failure rolls back the transition. On a nontransactional backend, any durable effects still require reconciliation.

Rollback follows each declaration's policy. Irreversible data operations refuse rollback. A completed rollback records `ROLLED_BACK`. Ordinary migrate refuses to replay that data; `migrate --reapply-data` explicitly starts a later epoch linked to the previous one. `--force` does not authorize reapply, and `--reapply-data` does not rerun an already successful migration. The direct Python executor uses `reapply=True` for the same decision.

When the same bundle creates a target table, rollback removes owned rows before checking whether the table is empty. Any remaining application row prevents the table drop. PostgreSQL and SQLite keep that check inside the locked transaction; SQLite also rejects remaining dependent views, foreign keys, and triggers. MySQL/MariaDB first rename the target into a reserved rollback table, then check and drop it. If that check fails, the renamed table retains its rows for inspection and recovery.

Use `dbwarden data transition reconcile MIGRATION_ID` to inspect journal state. `--apply --decision abandon` records an explicit abandonment where supported. `--apply --decision verified_retry` requires backend-specific proof: transactional rollback evidence, or matching checkpoints and verified postconditions for durable statements. An ambiguous partial statement remains blocked. The command takes the migration lock before it writes.

A partially completed MySQL/MariaDB rollback can remain blocked when it has already removed or changed target rows needed by the original identity proof. Verified checkpoints alone do not bypass that proof. Inspect and repair the affected state before retrying; DBWarden does not guess the missing before-image.

Persistent-environment `dbwarden reconcile` is separate: it generates a new reconciliation bundle from the declarations actually applied on that database to the merged frozen target. It retains original migration history, checks schema and data convergence, then marks branch versions reconciled. Repeating reconciliation after convergence produces no new change.

Data guards are read-only count queries run before or after a step. They enforce maximum or minimum rows, validation predicates, coverage, and conflict policies. `transition dry-run --probes` executes those queries and records count, scope checksum, database, isolation level, observation time, thresholds, and evidence checksum. It does not predict post-write values when the required schema or rows do not yet exist.

`migrate --dry-run --data` inspects pending frozen bundles after version and severity selection. It reports unavailable probes without creating a missing SQLite database. Probe success describes the observed database at that time; apply reruns guards inside its execution boundary.

## Safety and convergence

Data operations carry INFO, WARN, or CRITICAL severity based on their declaration and rollback behavior. Destructive source handling, overwrite behavior, or irreversible work deserve CRITICAL review. A passing compile and a successful guard prove only the declared checks ran. They do not prove application semantics or data quality outside declared ownership.

`category=` on `rows`, `derive`, or `DataTransition` selects a named migration group. Groups use the existing category and CLI plugin API; they do not add severity levels. SAFE, INFO, WARN, and CRITICAL retain their ordering. Safety-scoped generation defers dependent data and schema operations together, and each generated group has its own verified bundle. See [safety-scoped migrations](correctness/safety-scoped-migrations.md) and [plugin API](plugins/developing/overview.md).

`check --data` and `diff --data` add managed-row, derived-value, validation,
transition, merge, archive, and capture convergence checks to schema drift
checks. Preserved sources allow source-to-target comparison. Dropped sources
require authenticated retained edges and matching applied journal evidence.
Merge checks validate grouped values and contribution provenance. Archive and
capture checks validate their receipts and current postimages. Missing or
changed evidence reports an unverifiable declaration instead of assuming
convergence. Reserved ownership, edge, capture, archive, merge, and journal
tables are excluded from schema drift.

Re-run `data render`, `transition plan`, and bundle audit after a declaration change. Once a migration exists, edit the live declaration and generate a new migration. Do not edit frozen data files or migration SQL.

## Compared with legacy seeds

Seed files are useful for optional initial content and ad hoc loading. Declarative data migrations are versioned migration operations with canonical identity, ownership, rollback policy, guards, a durable journal, and frozen source artifacts. Use them when data change belongs to the same release contract as schema change.

The [design specification](design/declarative-data-migrations.md) defines the artifact and execution contract. The generated [Python API reference](reference/python-api.md) and [CLI option reference](reference/cli-options.md) list signatures and command options.
