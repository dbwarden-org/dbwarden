---
description: Build DBWarden value plugins with named hooks.
---

# Value Plugins

A value plugin supplies a value at a named hook point. Core calls the hook where optional behavior is needed, and, for single hooks, exactly one plugin may provide it. See the [hook catalog](../reference/hook-catalog.md) for every hook, its signature, and whether it is single or multi.

Common value hooks:

- `session_factory`, `sync_session_factory`: SQLAlchemy session dependency factories
- `clickhouse_session_factory`, `clickhouse_sync_session_factory`: ClickHouse client factories
- `load_model_module`, `load_config_module`: custom module loaders
- `lifespan`: FastAPI lifespan builder
- `health_routes`, `migration_routes`: route bundles (multi hooks)
- `seed_create`, `seed_apply`, `seed_list`, `seed_rollback`, `seed_export`: seed commands

## Registering A Hook

Define the hook function at module top level and register it in `setup()`:

```python
# src/dbwarden_example/__init__.py
def session_factory(database: str | None = None, *, dev: bool = False):
    from dbwarden.extensions.fastapi.engines import _async_session_factory  # deferred

    async def _dependency():
        async with _async_session_factory(database, dev=dev)() as session:
            yield session

    return _dependency


def setup(registrar) -> None:
    registrar.register("session_factory", session_factory)
```

`registrar.register(name, callable)` raises `ValueError` if `name` is not a known hook, so typos fail fast at load time.

## Walkthrough: The `dbwarden-fastapi` `setup()`

The official `dbwarden-fastapi` plugin defines each hook as a top-level function in its `__init__.py` and registers them all at the end. Its `setup()` is exactly:

```python
def setup(registrar) -> None:
    registrar.register("session_factory", session_factory)
    registrar.register("sync_session_factory", sync_session_factory)
    registrar.register("clickhouse_session_factory", clickhouse_session_factory)
    registrar.register("clickhouse_sync_session_factory", clickhouse_sync_session_factory)
    registrar.register("lifespan", lifespan)
    registrar.register("health_routes", health_routes)       # multi hook
    registrar.register("migration_routes", migration_routes) # multi hook
```

Each registered function imports what it needs from DBWarden (and FastAPI) **inside its body**, so importing the package registers the hooks without pulling in FastAPI. Core then resolves them where needed: `dbwarden.extensions.fastapi.get_session("primary")` calls the registered `session_factory`; if no plugin registered it, core uses its built-in fallback.

## The Import-Deferred Pattern

`setup` is exported from `__init__.py`, and registration happens only when DBWarden calls it. Keep heavy imports out of module scope by deferring them into each hook function:

```python
# src/dbwarden_example/__init__.py
def health_routes(*, auth_mode: str = "open", api_key: str | None = None):
    from fastapi import APIRouter  # deferred: FastAPI loads only when this hook runs

    router = APIRouter()
    # ... define routes ...
    return router


def setup(registrar) -> None:
    registrar.register("health_routes", health_routes)


__all__ = ["health_routes", "setup"]
```

Importing `dbwarden_example` must not import FastAPI or register anything until `setup()` runs.

## Multi vs Single Hooks

`health_routes` and `migration_routes` are **multi** hooks: several plugins may each contribute a router, and core collects them with `execute_all`. Every other value hook is **single**: if two plugins register it, core raises `HookConflictError` when the hook is invoked.

## Tests

Use the shared conformance harness (`dbwarden.plugin_conformance`) so your suite matches the [Verified standard](verified-standard.md) and earns the Verified badge; the [template](publishing.md#starting-from-the-template) wires it up for you. At minimum, value plugins should include:

- `test_setup_registers_hooks`: `setup(PluginRegistrar("dist"))` registers the declared hooks.
- `test_import_has_no_side_effects`: importing the package/module registers nothing and mutates no plugin global state.
- `test_entry_point_is_declared`: `pyproject.toml` exposes a discoverable `dbwarden.plugins` entry point.
- `test_hook_signature_compliance`: each registered callable accepts the documented arguments.

The official plugins use a lightweight fake registrar, matching how core calls `setup`:

```python
from dbwarden.plugin import HookRegistry
from dbwarden_example import setup


def setup_function() -> None:
    HookRegistry.clear()


def test_setup_registers_hooks() -> None:
    class Registrar:
        def register(self, hook_name, fn) -> None:
            HookRegistry.register(hook_name, fn, plugin="dbwarden-example")

    setup(Registrar())

    assert HookRegistry.is_registered("session_factory") is True
```
