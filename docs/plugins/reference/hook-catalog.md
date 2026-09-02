---
description: Reference for dbwarden plugin hooks.
---

# Hook Catalog

Value hooks are registered with `registry.register(name, callable)`. Object handlers are registered with `registry.register_object_handler(handler)`. The known hook names are defined by `KNOWN_VALUE_HOOKS` in `dbwarden.plugin`; registering an unknown name raises `ValueError`.

## Value Hooks

| Hook | Multi | Signature | Returns | Provided by (example) |
|------|-------|-----------|---------|-----------------------|
| `session_factory` | no | `(database: str \| None = None, *, dev: bool = False)` | async session dependency (callable) | `dbwarden-fastapi` |
| `sync_session_factory` | no | `(database: str \| None = None, *, dev: bool = False)` | sync session dependency (callable) | `dbwarden-fastapi` |
| `clickhouse_session_factory` | no | `(database: str \| None = None, *, dev: bool = False)` | async ClickHouse dependency (callable) | `dbwarden-fastapi` |
| `clickhouse_sync_session_factory` | no | `(database: str \| None = None, *, dev: bool = False)` | sync ClickHouse dependency (callable) | `dbwarden-fastapi` |
| `load_model_module` | no | `(path: Path, base_dir: Path)` | loaded module | `dbwarden-sandbox` |
| `load_config_module` | no | `(path: Path, base_dir: Path)` | `None` | `dbwarden-sandbox` |
| `lifespan` | no | `(app=None, *, mode="check", **kwargs)` | async context manager | `dbwarden-fastapi` |
| `health_routes` | yes | `(*, auth_mode: str = "open", api_key: str \| None = None)` | `APIRouter` | `dbwarden-fastapi` |
| `migration_routes` | yes | `(*, auth_mode: str = "open", api_key: str \| None = None)` | `APIRouter` | `dbwarden-fastapi` |
| `seed_create` | no | `(description, *, seed_type="sql", database=None, verbose=False)` | `None` | `dbwarden-seeds` |
| `seed_apply` | no | `(*, version=None, dry_run=False, database=None, all_databases=False, verbose=False)` | `None` | `dbwarden-seeds` |
| `seed_list` | no | `(*, database=None, all_databases=False, verbose=False, prune=False)` | `None` | `dbwarden-seeds` |
| `seed_rollback` | no | `(*, count=None, to_version=None, database=None, all_databases=False, verbose=False)` | `None` | `dbwarden-seeds` |
| `seed_export` | no | `(*, database=None, all_databases=False, output_dir="seeds")` | `None` | `dbwarden-seeds` |
| `sandbox_provider_start` | no | `(db_name: str \| None = None)` | `None` | `dbwarden-sandbox` |
| `sandbox_provider_stop` | no | `(db_name: str \| None = None)` | `None` | `dbwarden-sandbox` |

**Multi** hooks (`health_routes`, `migration_routes`) may be provided by several plugins; core collects all of them. Every other value hook is **single**: two providers cause a `HookConflictError` when the hook runs.

## Object Handlers

Object handlers are not named hooks; they register through `registry.register_object_handler(handler)` and are keyed by the handler's `object_type`. See [object plugins](../developing/object-plugins.md). Registering the same `object_type` from two different plugins raises `ObjectHandlerConflictError`.

## How Core Calls Hooks

Core resolves value hooks through the `HookRegistry`:

```python
from dbwarden.plugin import HookRegistry, HookNotRegisteredError

# Single hook: exactly one provider, or fall back to core behavior.
try:
    dependency = HookRegistry.execute_single("session_factory", "primary", dev=False)
except HookNotRegisteredError:
    dependency = core_default_session("primary")

# Multi hook: collect every provider's contribution.
routers = HookRegistry.execute_all("health_routes", auth_mode="open")
```

- `execute_single(name, *args, **kwargs)` raises `HookNotRegisteredError` when no plugin provides the hook and `HookConflictError` when more than one does.
- `execute_all(name, *args, **kwargs)` returns a list of every provider's result (empty if none).

Plugin authors do not call these; they are shown here so you can see exactly how and with what arguments core will invoke your callable.
