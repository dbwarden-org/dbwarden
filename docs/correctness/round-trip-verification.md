# Round-Trip Verification

Round-trip verification checks whether dbwarden can extract a database schema, represent it as models, generate SQL from those models, and arrive back at the same schema.

It is a consistency check between extractors, model metadata, diffing, and emitters.

## What Round-Tripping Means

The loop is:

```text
Existing database
  |
  v
dbwarden generate-models
  |
  v
SQLAlchemy models with backend-specific Meta
  |
  v
dbwarden make-migrations
  |
  v
Generated SQL migration
  |
  v
Apply migration to another empty database
  |
  v
Extract schema again
  |
  v
Compare extracted schema with original shape
```

If the second extraction matches the first, the handler round trip is stable for those object types.

## Why It Matters

Every supported database has features that are not visible in simple `CREATE TABLE` syntax.

PostgreSQL examples:

- Identity column options
- Generated columns
- Collation
- Per-column storage and compression
- Table storage parameters
- Exclusion constraints
- Row-level security policies
- Materialized view settings

ClickHouse examples:

- `MergeTree` engine families
- `ORDER BY`, `PRIMARY KEY`, `PARTITION BY`, and `SAMPLE BY`
- Engine settings
- Projections
- Skip indexes
- Materialized views
- Dictionaries
- RBAC objects

Round-trip verification proves that dbwarden does not lose those details when moving between database state, model state, and SQL.

## It Is Not a Full Mathematical Proof

Round-tripping is strong evidence, not a complete proof of all possible database behavior.

It proves internal consistency for supported schema objects. It does not prove:

- Application data is preserved.
- Manually written SQL has an inverse.
- Runtime behavior such as locks, query plans, replication lag, or trigger side effects is harmless.
- A database extension behaves identically across all versions.

That is why round-trip verification complements the [Convergence Gate](convergence-gate.md). Round-trip verification checks extractor and emitter consistency. The convergence gate checks that the repository's migration history reproduces the current model state.

## PostgreSQL Example

Start with a PostgreSQL schema that uses backend-specific features:

```sql
CREATE TABLE accounts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id integer NOT NULL,
    email character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
) WITH (fillfactor = 80);

CREATE INDEX idx_accounts_tenant_email
    ON accounts USING btree (tenant_id, email);
```

dbwarden extracts the schema:

```bash
dbwarden generate-models --database primary --output generated_models.py
```

The generated model should preserve the meaningful PostgreSQL metadata:

```python
class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    class Meta(PGTableMeta):
        pg_storage_params = {"fillfactor": 80}
        indexes = [
            IndexSpec(
                name="idx_accounts_tenant_email",
                columns=["tenant_id", "email"],
            ),
        ]
```

The exact generated Python may differ by import style, but the important part is that schema-relevant metadata is represented. The identity column, storage parameters, and index are not discarded.

Then generate migrations from that model against an empty database:

```bash
dbwarden make-migrations "recreate accounts" --database primary
dbwarden migrate --database primary
dbwarden diff --database primary --out table
```

The expected diff is empty. If dbwarden emits a migration every time from the generated model, some part of extraction, canonicalization, or emission is losing information.

## ClickHouse Example

Start with a ClickHouse table that uses a `MergeTree` engine, partitioning, a projection, and a skip index:

```sql
CREATE TABLE events (
    id UInt64,
    event_date Date,
    user_id UInt64,
    path String,
    INDEX idx_path path TYPE bloom_filter(0.01) GRANULARITY 64,
    PROJECTION by_date (
        SELECT event_date, count()
        GROUP BY event_date
    )
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(event_date)
ORDER BY (event_date, id)
SETTINGS index_granularity = 8192;
```

The generated model must preserve engine and table metadata:

```python
class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_date: Mapped[date] = mapped_column()
    user_id: Mapped[int] = mapped_column()
    path: Mapped[str] = mapped_column()

    class Meta(CHTableMeta):
        ch = ch_table(
            engine=merge_tree(),
            partition_by="toYYYYMM(event_date)",
            order_by=["event_date", "id"],
            settings={"index_granularity": "8192"},
            projections=[
                ProjectionSpec(
                    name="by_date",
                    select="SELECT event_date, count() GROUP BY event_date",
                ),
            ],
            indexes=[
                ChIndexSpec(
                    name="idx_path",
                    expr="path",
                    clickhouse_type="bloom_filter(0.01)",
                    granularity=64,
                ),
            ],
        )
```

The verification flow is the same:

```bash
dbwarden generate-models --database analytics --output generated_analytics.py
dbwarden make-migrations "recreate clickhouse schema" --database analytics
dbwarden migrate --database analytics
dbwarden diff --database analytics --out table
```

The diff should be empty. If the engine settings, projection, or skip index are missing after extraction, the round trip fails and the handler needs a fix.

## Manual Round-Trip Checklist

Use this checklist when validating a backend feature:

1. Create the object directly in a disposable database.
2. Run `dbwarden generate-models` for that database.
3. Inspect the generated `class Meta` block.
4. Apply the generated model to an empty disposable database with `make-migrations` and `migrate`.
5. Run `dbwarden diff` and confirm no schema drift remains.
6. Add a test for the feature so future changes cannot regress it.

## What Round-Trip Verification Does Not Cover

Round-trip verification does not cover data migration semantics. A table can round-trip perfectly while a manual `UPDATE` statement still needs human review.

It also does not replace backend integration tests. For example, ClickHouse may accept syntax but behave differently depending on engine family. PostgreSQL may require a lock level that matters in production. Those concerns belong in safety review and integration testing.

Use round-trip verification to prove schema representation fidelity. Use the [Convergence Gate](convergence-gate.md) to prove the repository history lands on the declared model state.
