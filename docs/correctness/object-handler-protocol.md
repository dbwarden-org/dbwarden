# ObjectHandler Protocol

The `ObjectHandler` protocol is the mechanical contract that makes deterministic diffs, backend-native SQL generation, and symmetric rollbacks possible. Every backend object - tables, columns, indexes, views, roles, policies, grants, ClickHouse projections, materialized views, and more - has a dedicated handler that owns its full lifecycle: extraction, canonicalization, diffing, and SQL emission.

Without understanding the handler contract, the other correctness guarantees feel like magic. With it, they become inspectable engineering.

## The Problem It Solves

Every database backend has a different DDL dialect, different catalog tables, and different type systems. PostgreSQL uses `pg_catalog`, MySQL uses `information_schema`, ClickHouse uses `system.tables`, and SQLite uses `sqlite_master`. Column types that look similar (`VARCHAR(255)`, `String`, `TEXT`) have different internal representations. Engine metadata - partitioning, sorting keys, TTL expressions - exists only in ClickHouse. Roles and policies are structured differently across PostgreSQL and ClickHouse.

Core must treat all of these uniformly without hard-coding backend knowledge into the diff engine. Adding a new object type or a new backend must not require rewriting the diff pipeline.

The handler protocol solves this by defining a narrow interface that each backend implements per object type. Core calls the interface. Backend code provides the implementation.

## The Handler Contract

Every handler implements the `ObjectHandler` protocol defined in `dbwarden/engine/core/protocol.py`:

```python
class ObjectHandler(Protocol):
    object_type: str
    run_phase: RunPhase
    statement_order: StatementOrder
    ordering: OrderingConstraint

    def extract(self, snapshot: dict[str, Any]) -> dict[str, Any]: ...

    def model_spec_from_config(self, config: Any) -> dict[str, Any]: ...

    def model_spec_from_tables(
        self, model_tables: list[Any]
    ) -> dict[str, Any]: ...

    def canonicalize(self, spec: dict[str, Any]) -> dict[str, Any]: ...

    def diff(
        self,
        snap_spec: dict[str, Any],
        model_spec: dict[str, Any],
    ) -> Tuple[List[Op], List[Op]]: ...

    def emit(
        self, op: Op, db_name: Optional[str] = None, **kwargs: Any,
    ) -> List[MigrationStatement]: ...
```

### Method Roles

| Method | Input | Output | Responsibility |
|---|---|---|---|
| `extract` | Full database snapshot dict | Raw backend state for this object type | Pulls the handler's relevant objects out of a schema snapshot |
| `model_spec_from_config` | Backend config | Raw config-derived spec | Builds spec from configuration (used in PREAMBLE phase for objects not in model tables) |
| `model_spec_from_tables` | List of model tables | Raw model-derived spec | Builds spec from Python model definitions (used in DIFF phase) |
| `canonicalize` | Raw spec | Normalized comparable spec | Removes representation noise so that equivalent states produce identical dicts |
| `diff` | Canonical snapshot spec, canonical model spec | Upgrade ops + rollback ops | Detects differences and produces paired typed operations |
| `emit` | A single Op | List of MigrationStatement objects | Renders backend-native SQL for upgrade and rollback |

### Supporting Types

**`Op`** is the typed operation that `diff` produces:

```python
@dataclass
class Op:
    object_type: str
    upgrade_attrs: dict[str, Any] = field(default_factory=dict)
    rollback_attrs: dict[str, Any] = field(default_factory=dict)
    irreversible: bool = False
```

`upgrade_attrs` stores the data needed to emit the upgrade SQL. `rollback_attrs` stores the data needed to reverse it. Both are carried together so that rollback never needs to re-inspect the database or guess prior state.

**`MigrationStatement`** is the emit output:

```python
@dataclass
class MigrationStatement:
    order: StatementOrder
    upgrade_sql: str
    rollback_sql: str
    rollback_kind: str = "real"
    rollback_reason: str | None = None
```

`StatementOrder` is an `IntEnum` that controls the sequence of SQL emission. Upgrade statements are sorted by this value. Rollback statements are emitted in reverse order.

**`RunPhase`** controls when the handler runs:

```python
class RunPhase(IntEnum):
    PREAMBLE = 0   # runs before table-diff phase; uses model_spec_from_config
    DIFF = 1       # runs during table-diff phase; uses model_spec_from_tables
```

PREAMBLE handlers cover objects like roles and schemas that exist outside the table model tree. DIFF handlers cover objects that are derived from table metadata.

## How This Enables Correctness

### Canonicalize → Deterministic Diffs

`canonicalize` is the noise-removal step. Two specs that represent the same logical state must produce identical dictionaries after canonicalization. This guarantees that the diff only fires on real changes, not on formatting differences.

For example, PostgreSQL may report `character varying(255)` while a model says `String(255)`. The column handler's canonicalization reduces both to `varchar(255)`. A model default of `true` and a database default of `TRUE` both normalize to `true`.

If the canonicalized specs match, no diff is produced. If they differ, the diff is a real schema change.

### Diff Returns Paired Ops → Symmetric Rollbacks

`diff` returns two lists: upgrade ops and rollback ops. They are produced at the same time from the same inputs. This means rollback is not a separate best-effort pass.

Each op carries `upgrade_attrs` (what `emit` needs for the forward SQL) and `rollback_attrs` (what `emit` needs for the reverse SQL). For a column type change:

```python
Op(
    object_type="alter_column_type",
    upgrade_attrs={"table": "users", "column": "name", "model_type": "varchar(500)"},
    rollback_attrs={"table": "users", "column": "name", "model_type": "varchar(255)"},
)
```

The same `emit` method uses `upgrade_attrs` to render the upgrade statement and `rollback_attrs` to render the rollback. The reverse is structurally paired.

### Emit → Backend-Native SQL

`emit` produces raw SQL strings wrapped in `MigrationStatement` objects. Different backends implement `emit` differently for the same object type. A column type change in PostgreSQL produces:

```sql
ALTER TABLE users ALTER COLUMN name TYPE VARCHAR(500);
```

The same logical change in ClickHouse produces:

```sql
ALTER TABLE users MODIFY COLUMN name String;
```

The diff pipeline does not know or care which dialect is being generated. Core calls `emit`, and the backend handler owns the SQL.

## How Handlers Are Registered

Handlers are registered with the `RegistryDriver` in `dbwarden/engine/core/registry.py`:

```python
class RegistryDriver:
    def __init__(self, *, include_plugins: bool = True):
        self._handlers: dict[str, ObjectHandler] = {}
        self._plugin_claims: dict[str, ObjectHandlerRegistration] = {}

    def register(self, handler: ObjectHandler) -> None:
        self._handlers[handler.object_type] = handler
```

Registration happens during backend initialization. Each backend module (PostgreSQL, ClickHouse, MySQL) creates its set of handlers and registers them:

```python
driver.register(ColumnHandler())
driver.register(IndexHandler())
driver.register(TableHandler())
driver.register(ConstraintHandler())
driver.register(EnumHandler())
driver.register(DomainHandler())
driver.register(RoleHandler())
driver.register(PoliciesHandler())
driver.register(GrantsHandler())
driver.register(ViewHandler())
driver.register(TriggerHandler())
driver.register(SequenceHandler())
driver.register(PartitionHandler())
driver.register(SchemaHandler())
driver.register(FunctionHandler())
driver.register(CompositeTypeHandler())
driver.register(DefaultPrivilegesHandler())
driver.register(EventTriggerHandler())
driver.register(ExtendedStatisticsHandler())
driver.register(StorageParamsHandler())
driver.register(RenameTableHandler())
driver.register(PgTableHandler())
# ClickHouse handlers
driver.register(ChTableHandler())
driver.register(ChColumnHandler())
driver.register(ChProjectionHandler())
driver.register(ChSkipIndexHandler())
driver.register(ChMaterializedViewHandler())
driver.register(ChCommentHandler())
driver.register(ChDictionaryHandler())
driver.register(ChRoleHandler())
driver.register(ChUserHandler())
driver.register(ChRowPolicyHandler())
driver.register(ChSettingsProfileHandler())
driver.register(ChQuotaHandler())
driver.register(ChNamedCollectionHandler())
driver.register(ChGrantHandler())
driver.register(ChAggTargetHandler())
driver.register(ChDataOpHandler())
```

Core's `RegistryDriver.run()` iterates over all registered handlers, extracts their specs, canonicalizes, diffs, and collects the paired ops. The caller then orders them by `statement_order` and emits SQL.

Because registration is just a method call, third-party plugins can register handlers for custom object types using the same protocol.

## Example Walk-Through: ALTER TABLE ADD COLUMN

This trace follows a single `ALTER TABLE ADD COLUMN` through the full pipeline.

### 1. Extract

The database snapshot is a large dict of all extracted schema objects. The `ColumnHandler.extract` pulls only the column data:

```python
# ColumnHandler.extract
def extract(self, snapshot):
    result = {}
    for tname, tdata in snapshot.get("tables", {}).items():
        result[tname] = dict(tdata.get("columns", {}))
    return result
```

Input snapshot (abbreviated):
```json
{
  "tables": {
    "users": {
      "columns": {
        "id": {"type": "integer", "nullable": false, "primary_key": true},
        "name": {"type": "character varying(255)", "nullable": true}
      }
    }
  }
}
```

Output:
```json
{
  "users": {
    "id": {"type": "integer", "nullable": false, "primary_key": true},
    "name": {"type": "character varying(255)", "nullable": true}
  }
}
```

### 2. Model Spec

`model_spec_from_tables` reads the same columns from the Python model definitions:

```json
{
  "users": {
    "id": {"type": "INTEGER", "nullable": false, "primary_key": true},
    "name": {"type": "VARCHAR(255)", "nullable": true},
    "display_name": {"type": "VARCHAR(255)", "nullable": true}
  }
}
```

The model has a new column `display_name` that does not exist in the snapshot.

### 3. Canonicalize

Both specs are run through `canonicalize`. The column handler currently passes through directly, but in general this step normalizes type names, default expressions, and metadata formats so that the diff is stable.

After canonicalization, `character varying(255)` and `VARCHAR(255)` both become `varchar(255)`.

### 4. Diff

`diff` compares the canonical specs. It detects:

- `display_name` exists in the model spec but not in the snapshot spec → **add column**
- `display_name` does not exist in the snapshot spec → no column to drop → the rollback of add is **drop column**

It produces paired ops:

```python
# Upgrade op
Op(
    object_type="add_column",
    upgrade_attrs={
        "table": "users",
        "column": "display_name",
        "model_column": <ModelColumn: display_name VARCHAR(255) nullable>,
    },
    rollback_attrs={"table": "users", "column": "display_name"},
)

# Rollback op
Op(
    object_type="drop_column",
    upgrade_attrs={
        "table": "users",
        "column": "display_name",
        "definition": {"type": "VARCHAR(255)", "nullable": True},
    },
    rollback_attrs={"table": "users", "column": "display_name"},
)
```

### 5. Emit

During SQL generation, `RegistryDriver.emit_all` dispatches each op to the correct handler's `emit` method. For `add_column`, the `ColumnHandler.emit` renders:

```python
sql = generate_add_column_sql("users", model_col, db_name)
# → "ALTER TABLE users ADD COLUMN display_name VARCHAR(255);"

stmt = MigrationStatement(
    order=StatementOrder.ADD_COLUMN,
    upgrade_sql=sql,
    rollback_sql="ALTER TABLE users DROP COLUMN display_name",
)
```

The `MigrationStatement` is then included in the final migration file:

```sql
-- upgrade
ALTER TABLE users ADD COLUMN display_name VARCHAR(255);

-- rollback
ALTER TABLE users DROP COLUMN display_name;
```

The same protocol handles column type changes, nullable changes, default changes, comment changes, indexes, constraints, roles, policies, and every other supported object type across all backends.

## The Ordering Constraint

Each handler declares a `statement_order` that controls when its emitted SQL appears relative to other statements. The `StatementOrder` enum defines anchor points such as `ADD_COLUMN`, `DROP_TABLE`, `ALTER_CONSTRAINT`, and `CREATE_TYPE`.

Core sorts all `MigrationStatement` objects by their `order` value before writing the migration file. Rollback statements are emitted in reverse order so that dependencies are satisfied in both directions.

This ordering mechanism is intentionally simple. Future work may introduce a topological sort where handlers declare edges against named anchors instead of numeric enum values. For now, the integer enum provides enough granularity for correct migration ordering across all supported backends.

## How to Add a New Handler

### 1. Implement the Protocol

Create a new class that implements `ObjectHandler`:

```python
from dbwarden.engine.core.protocol import ObjectHandler, Op, RunPhase
from dbwarden.engine.core.statement_order import MigrationStatement, StatementOrder

class MyCustomHandler(ObjectHandler):
    object_type: str = "my_custom_object"
    op_types: tuple[str, ...] = ("my_custom_op",)
    run_phase: RunPhase = RunPhase.DIFF
    statement_order: StatementOrder = StatementOrder.ALTER_TABLE_OPTIONS

    def extract(self, snapshot):
        ...

    def model_spec_from_config(self, config):
        return {}

    def model_spec_from_tables(self, model_tables):
        ...

    def canonicalize(self, spec):
        ...

    def diff(self, snap_spec, model_spec):
        upgrade_ops = []
        rollback_ops = []
        ...
        return upgrade_ops, rollback_ops

    def emit(self, op, db_name=None, **kwargs):
        ...
```

### 2. Register It

```python
from dbwarden.engine.core.registry import RegistryDriver

driver = RegistryDriver()
driver.register(MyCustomHandler())
```

### 3. Write Round-Trip Tests

- Verify that `extract` + `canonicalize` applied to a known snapshot produces the expected spec.
- Verify that `diff` with identical canonical specs produces no ops.
- Verify that `diff` with differing specs produces the expected upgrade/rollback pair.
- Verify that `emit` produces the expected SQL for each operation variant.
- Verify that the full round trip (extract → canonicalize → diff → emit → apply → extract) returns to the original state.

### 4. Add Extraction Support

If the new handler reads from the database, add extraction logic in the appropriate backend extractor (e.g., `extract.py` for PostgreSQL, `extract_ch.py` for ClickHouse). The extractor populates the snapshot dict that `extract` reads.

### 5. Add Safety Classification

If the handler produces operations that could drop data or require user acknowledgement, add entries in the safety classifier in `dbwarden/engine/safety/snapshot.py`.

## Relation to Other Correctness Pages

The handler protocol is the bridge between the abstract guarantees and the concrete pipeline:

| Correctness property | Role of the handler protocol |
|---|---|
| **Deterministic diff** | `canonicalize` removes representation noise before comparison. Two equivalent states produce identical canonical forms, so diffs only fire on real changes. |
| **SQL generation** | `emit` produces backend-native SQL. The diff pipeline generates typed operations; the handler renders them for one specific backend and object family. |
| **Symmetric rollback** | `diff` returns paired upgrade and rollback ops. Each Op carries both forward and reverse attributes, so rollback is structurally paired with upgrade from the start. |

- [Deterministic Diff](deterministic-diff.md) - the handler's `canonicalize` method is what makes diffs stable
- [SQL Generation](sql-generation.md) - the handler's `emit` method is what produces backend-native SQL
- [Rollback Generation](rollback-generation.md) - the handler's `diff` method returns paired ops that make symmetric rollbacks possible
