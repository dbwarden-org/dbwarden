---
description: Build DBWarden object plugins that participate in schema diffing.
---

# Object Plugins

An object plugin adds a database object **type** to DBWarden's schema convergence pipeline. Core drives your handler through the same extract → canonicalize → diff → emit flow it uses for tables, so the object is diffed against the live database and emitted in the right SQL order.

## Scope Test

An object plugin is the right tool when the feature is **database state declared by the model (or config) and converged by DBWarden**, not a one-off script. Good candidates: extensions, roles, grants, policies, triggers, functions, sequences, and backend-specific declarative objects. If the thing you want isn't schema state DBWarden should diff and emit, use a value hook or a migration instead.

## The `ObjectHandler` Contract

A handler is any object exposing these attributes and methods (see `dbwarden.engine.core.protocol.ObjectHandler`):

```python
object_type: str                 # unique registration key, e.g. "pg_extension"
op_types: tuple[str, ...]        # op names this handler emits, e.g. ("create_pg_extension", ...)
run_phase: RunPhase              # PREAMBLE or DIFF
ordering: OrderingConstraint     # public anchors (optional)

def extract(self, snapshot) -> dict: ...           # DB snapshot -> current spec
def model_spec_from_config(self, config) -> dict: ...  # config -> desired spec
def model_spec_from_tables(self, model_tables) -> dict: ...  # models -> desired spec
def canonicalize(self, spec) -> dict: ...          # normalize for comparison
def diff(self, snap_spec, model_spec) -> tuple[list[Op], list[Op]]: ...  # (upgrade, rollback)
def emit(self, op, db_name=None, **kwargs) -> list[MigrationStatement]: ...  # Op -> SQL
```

## Step By Step: A `CREATE EXTENSION` Handler

The goal: converge the PostgreSQL extensions declared in config (`config.pg_extensions`) with those present in the database. This mirrors the real [`dbwarden-pgsql-extensions`](https://github.com/dbwarden-org/dbwarden-pgsql-extensions) `PgExtensionHandler`.

### 1. Import the public types

```python
from dbwarden.engine.core import (
    Anchor,
    MigrationStatement,
    Op,
    OrderingConstraint,
    RunPhase,
)
```

### 2. Declare identity, phase, and ordering

Extensions must exist before anything that uses them, so pin the handler between `PREAMBLE` and `BEFORE_TABLES`:

```python
class PgExtensionHandler:
    object_type = "pg_extension"
    op_types = ("create_pg_extension", "drop_pg_extension")
    run_phase = RunPhase.PREAMBLE
    ordering = OrderingConstraint(
        after=(Anchor.PREAMBLE,),
        before=(Anchor.BEFORE_TABLES,),
    )
```

### 3. Read current state from the snapshot

```python
    def extract(self, snapshot):
        return snapshot.get("pg_extensions", {})
```

### 4. Read desired state

```python
    def model_spec_from_config(self, config):
        return {name: {} for name in getattr(config, "pg_extensions", [])}

    def model_spec_from_tables(self, model_tables):
        return {}  # extensions come from config, not model tables
```

### 5. Canonicalize and diff

`canonicalize` normalizes both sides so comparison is apples-to-apples (the real handler lowercases names and sorts). `diff` returns `(upgrade_ops, rollback_ops)`, emitting a create for what's missing and a drop for what's extra, with the inverse recorded for rollback:

```python
    def canonicalize(self, spec):
        return {str(n).lower(): dict(v or {}) for n, v in sorted((spec or {}).items())}

    def diff(self, snap_spec, model_spec):
        upgrade_ops, rollback_ops = [], []
        snap, model = snap_spec or {}, model_spec or {}
        for name in sorted(set(model) - set(snap)):        # missing -> create
            attrs = {"name": name}
            upgrade_ops.append(Op("create_pg_extension", attrs, attrs))
            rollback_ops.insert(0, Op("drop_pg_extension", attrs, attrs))
        for name in sorted(set(snap) - set(model)):        # extra -> drop
            attrs = {"name": name}
            upgrade_ops.append(Op("drop_pg_extension", attrs, attrs))
            rollback_ops.insert(0, Op("create_pg_extension", attrs, attrs))
        return upgrade_ops, rollback_ops
```

### 6. Emit SQL

`emit` turns each `Op` into ordered SQL, branching on `op.object_type`. Use `self.statement_order`, which DBWarden sets from your `ordering` anchors (see below):

```python
    def emit(self, op, db_name=None, **kwargs):
        name = '"' + str(op.upgrade_attrs["name"]).replace('"', '""') + '"'
        if op.object_type == "create_pg_extension":
            return [MigrationStatement(
                order=self.statement_order,
                upgrade_sql=f"CREATE EXTENSION IF NOT EXISTS {name};",
                rollback_sql=f"DROP EXTENSION IF EXISTS {name};",
            )]
        if op.object_type == "drop_pg_extension":
            return [MigrationStatement(
                order=self.statement_order,
                upgrade_sql=f"DROP EXTENSION IF EXISTS {name};",
                rollback_sql=f"CREATE EXTENSION IF NOT EXISTS {name};",
            )]
        return []
```

### 7. Register it

`setup` lives in the package `__init__.py` and imports the handler module lazily:

```python
# src/dbwarden_example/__init__.py
def setup(registrar) -> None:
    from dbwarden_example.handler import PgExtensionHandler

    registrar.register_object_handler(PgExtensionHandler())
```

### 8. Declare your config keys

If your handler is driven by `database_config(...)` keys, register them too. A plugin owns its config keys, not just its handlers.

```python
CONFIG_KEYS = ("pg_extensions",)


def setup(registrar) -> None:
    from dbwarden_example.handler import PgExtensionHandler

    registrar.register_object_handler(PgExtensionHandler())
    registrar.register_config_key(*CONFIG_KEYS)
```

Core validates every keyword argument passed to `database_config(...)` against the registered keys. A key no plugin owns is rejected as an unknown argument, and a key belonging to a plugin that is not installed raises `DBWardenConfigError` naming the plugin to install. Both fire when `dbwarden.py` loads, so a missing plugin surfaces immediately rather than as a migration that silently produces nothing.

Read the config values off the `config` object your handler already receives, using the key name directly:

```python
extensions = getattr(config, "pg_extensions", []) or []
```

To stay loadable against cores that predate the config-key registry, guard the call:

```python
    register_config_key = getattr(registrar, "register_config_key", None)
    if register_config_key is not None:
        register_config_key(*CONFIG_KEYS)
```

## Ordering And The DAG

DBWarden orders all object handlers into a single directed acyclic graph, then emits their statements in that order.

- **Anchors** place a handler relative to core milestones. At registration, DBWarden validates your `OrderingConstraint` and derives a private `statement_order` from the anchors: you never touch the integers.
- **Object-to-object** constraints (`after_object`, `before_object`) place your handler relative to *other* handlers by `object_type`:

```python
ordering = OrderingConstraint(after_object=("role",))  # emit after the "role" handler
```

Anchors are the **public contract**: `PREAMBLE`, `BEFORE_TABLES`, `AFTER_TABLES`, `AFTER_CONSTRAINTS`, `AFTER_INDEXES`, `POSTAMBLE`. See [ordering anchors](../reference/ordering-anchors.md) for the full map and failure modes (unknown references, cycles, impossible pairs).

## Shared SQL Helpers

Emitting SQL that matches core's byte-for-byte matters: a plugin that quotes identifiers differently, or that skips `ON CLUSTER`, produces migrations that disagree with the ones core generates for neighbouring objects. Rather than have you copy those rules, the shared implementations are public in `dbwarden.engine.core.plugin_api`:

| Name | Use |
|---|---|
| `quote_pg(name)` | Quote a PostgreSQL identifier the way core does (reserved words only). |
| `qualified_name(name, schema)` | Render `schema.name`, or bare `name` when unqualified. |
| `build_create_policy_sql`, `build_alter_policy_sql` | Render row-level-security policy DDL. |
| `build_grant_sql`, `build_revoke_sql` | Render `GRANT` / `REVOKE`. |
| `emit_with_cluster`, `ClusterableStatement` | Wrap ClickHouse DDL so it honours the configured `ON CLUSTER`. |
| `strip_secret_values`, `has_visible_secrets`, `REDACTED` | Redact secret values before they reach a snapshot. |

The module sits under `dbwarden.engine.core`, so importing from it satisfies the public-API check in the [Verified standard](verified-standard.md).

## Conflicts And Overrides

`object_type` is the registration key. Two *different* plugins registering the same `object_type` raises `ObjectHandlerConflictError`. A plugin handler and a core handler with the same type is allowed: the plugin handler overrides core, which is how official plugins replace built-in fallbacks (e.g. the core `CREATE EXTENSION` preamble).

Overriding is a real transfer of responsibility, so DBWarden announces it rather than swapping silently:

```
WARNING  dbwarden.registry - Plugin 'dbwarden-pgsql-rbac' overrides the built-in
handler for object type 'role'. Migration SQL for 'role' now comes from the
plugin, not DBWarden core.
```

The warning is emitted once per plugin and object type per run. If you see one you did not expect, a plugin has taken over DDL generation for that object type, and the SQL in your migrations for it is the plugin's, not core's. Plugins requesting the Verified tier must declare their overrides in the verification issue.

## Tests

Object plugins run the value-plugin conformance checks plus two handler-specific ones from the shared harness (`dbwarden.plugin_conformance`); see the [Verified standard](verified-standard.md).

- `test_object_handler_conformance`: `assert_object_handler_conformance(handler, config=...)` exercises `extract`, `canonicalize`, `diff`, and `emit` against a minimal fixture and validates the returned types and SQL shape.
- `test_ordering_constraint_satisfiable`: `assert_ordering_constraint_satisfiable(handler)` confirms the constraint is not statically impossible (no impossible anchor pair; no unknown/cyclic object references).
