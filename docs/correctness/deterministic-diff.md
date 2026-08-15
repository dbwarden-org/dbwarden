# Deterministic Diff

The deterministic diff is the reason dbwarden can generate stable, reviewable migrations. Given the same model state and the same database snapshot, dbwarden should produce the same operation list every time.

Without deterministic diffing, automated migrations become noisy. A pull request might alternate between two equivalent SQL forms. Reviewers would waste time checking fake changes instead of real schema changes.

## The Problem

Databases and model code often describe the same schema in different ways.

Examples:

- A model says `String(255)` while PostgreSQL reports `character varying(255)`.
- A model says `Boolean` while a database default is rendered as `false` or `FALSE`.
- A PostgreSQL identifier may appear quoted in SQL and unquoted in model metadata.
- ClickHouse engine parameters may be returned in a different order than they were declared.
- Whitespace in view definitions may differ while the query is logically unchanged.

If dbwarden compared raw strings directly, it would produce false diffs.

## Canonicalization

dbwarden handlers canonicalize specs before diffing. Each handler owns the rules for its object type.

The shape is:

```text
Model spec
  |
  v
ObjectHandler.canonicalize(model_spec)
  |
  v
Canonical model spec

Database snapshot
  |
  v
ObjectHandler.canonicalize(snapshot_spec)
  |
  v
Canonical snapshot spec

Canonical model spec + canonical snapshot spec
  |
  v
ObjectHandler.diff(...)
  |
  v
Typed operations
```

The important property is that equivalent states normalize to the same representation.

## Handler Contract

Backend handlers expose a small contract:

```text
extract(snapshot) -> raw backend state
model_spec_from_tables(models) -> raw model state
canonicalize(spec) -> stable comparable state
diff(snapshot_spec, model_spec) -> operations
emit(operation) -> SQL statements
```

The `canonicalize` step sits between raw state and diffing. It is the boundary where noisy representation differences are removed.

## PostgreSQL Normalization Examples

### Type Aliases

PostgreSQL may report type names differently than SQLAlchemy models declare them.

```text
Model declaration:
  String(255)

Database extraction:
  character varying(255)

Canonical form:
  varchar(255)
```

After canonicalization, dbwarden does not emit a type-change migration for this column.

### Boolean Defaults

Different sources may use different boolean spelling.

```text
Model default:
  true

Database default:
  TRUE

Canonical meaning:
  true
```

The diff should compare the normalized meaning, not the original spelling.

### Identifier Quoting

PostgreSQL accepts both quoted and unquoted identifiers, but quoted identifiers preserve case. dbwarden normalizes safe names while preserving names that require quotes.

```text
users.email
"users"."email"

Canonical object identity:
  users.email
```

The goal is not to remove all quotes from emitted SQL. The goal is to compare object identity consistently before SQL generation.

## ClickHouse Normalization Examples

### Engine Metadata

ClickHouse table metadata is not just the engine name. A `MergeTree` table can include partition keys, primary keys, order keys, sample keys, TTL expressions, and settings.

```text
Model:
  engine = MergeTree()
  order_by = ["event_date", "id"]
  settings = {"index_granularity": "8192"}

Snapshot:
  ENGINE = MergeTree()
  ORDER BY (event_date, id)
  SETTINGS index_granularity = 8192

Canonical form:
  engine family: MergeTree
  order_by: [event_date, id]
  settings.index_granularity: 8192
```

The canonical form lets dbwarden decide whether a real engine change occurred.

### Projection and Skip Index Specs

ClickHouse projections and skip indexes contain expressions. dbwarden normalizes the supported metadata fields so the diff sees stable names, expressions, types, and granularities.

```text
Projection model:
  name: by_date
  select: SELECT event_date, count() GROUP BY event_date

Snapshot projection:
  name: by_date
  query returned by system tables

Canonical comparison:
  same projection identity and same normalized query body
```

## Tricky Case Walkthrough

Consider a model column:

```python
name: Mapped[str] = mapped_column(String(255), nullable=False, default="hello")
```

PostgreSQL may report:

```sql
name character varying(255) NOT NULL DEFAULT 'hello'::character varying
```

The raw strings differ:

```text
String(255)
character varying(255)

"hello"
'hello'::character varying
```

The canonical comparison should reduce them to the same meaning:

```text
type: varchar(255)
nullable: false
default: 'hello'
```

Result:

```text
No operation emitted
```

If the model changes to `String(500)`, canonicalization no longer hides the difference:

```text
Canonical model type: varchar(500)
Canonical snapshot type: varchar(255)
Result: alter column type operation
```

## Why Determinism Matters

Determinism gives dbwarden four practical properties.

### Reviewability

The same input produces the same migration. Reviewers can trust that a migration is caused by a real schema change, not by formatter noise.

### Auditability

Generated SQL can be compared across CI runs. If the output changes, the inputs changed or the generator changed.

### No Flapping

Flapping happens when a tool alternates between two equivalent forms. For example:

```text
Run 1 emits: ALTER COLUMN name TYPE VARCHAR(255)
Run 2 emits: ALTER COLUMN name TYPE character varying(255)
Run 3 emits: ALTER COLUMN name TYPE VARCHAR(255)
```

Canonicalization prevents this class of migration noise.

### Safe Automation

CI can block drift because the diff is stable. A nondeterministic diff would make CI untrustworthy.

## Link to SQL Generation

The canonical spec is not the final SQL. It is the stable state that the diff engine compares. Once the diff produces typed operations, backend emitters render SQL.

See [SQL Generation](sql-generation.md) for the next stage in the pipeline.
