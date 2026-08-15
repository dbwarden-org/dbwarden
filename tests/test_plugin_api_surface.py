"""Snapshot of the surface dbwarden promises to plugins.

Plugins may import anything from core, but only these names carry a stability
promise: they change on a major version, with a deprecation cycle, and never by
accident. This file is what makes "we kept the external API the same" checkable
instead of aspirational.

When a test here fails, that is the point. Decide deliberately:

* Renamed or removed something? That is a breaking change. Bump
  ``PLUGIN_API_VERSION``, give the old name a deprecation shim for one minor
  cycle, and run ``scripts/plugin-impact.py`` to see who you are about to break.
* Added a new export? Add it to the table below in the same commit, so the
  surface and its snapshot cannot drift.
* Changed a signature? Same as a rename. Callers are compiled against the old
  one.
"""
from __future__ import annotations

import inspect

import pytest

import dbwarden.engine.core as engine_core
import dbwarden.engine.core.plugin_api as plugin_api
import dbwarden.exceptions as exceptions
import dbwarden.plugin as plugin
import dbwarden.plugin_conformance as conformance

# --- names, with signatures where they are callable ------------------------

PLUGIN_NAMES: dict[str, str | None] = {
    # Registration, what setup() is handed.
    "PluginRegistrar": None,
    "HookRegistry": None,
    "ObjectPluginRegistry": None,
    "ObjectHandlerRegistration": None,
    "ConfigKeyRegistry": None,
    "PLUGIN_CONFIG_KEY_OWNERS": None,
    # Errors a plugin can be expected to see or catch.
    "HookNotRegisteredError": None,
    "HookConflictError": None,
    "ObjectHandlerConflictError": None,
    "PluginApiMismatchError": None,
    # The contract itself.
    "PLUGIN_API_VERSION": None,
    "PLUGIN_API_ATTR": None,
    "PLUGIN_GROUP": None,
    "KNOWN_VALUE_HOOKS": None,
    "MULTI_VALUE_HOOKS": None,
    "HOOK_CALL_SPECS": None,
}

REGISTRAR_METHODS: dict[str, str] = {
    "register": "(self, hook_name: str, fn: Callable[..., Any]) -> None",
    "register_object_handler": "(self, handler: Any) -> None",
    "register_config_key": "(self, *keys: str) -> None",
}

HOOK_REGISTRY_METHODS: dict[str, str] = {
    "execute_single": "(hook_name: str, *args: Any, **kwargs: Any) -> Any",
    "execute_all": "(hook_name: str, *args: Any, **kwargs: Any) -> list[Any]",
    "is_registered": "(hook_name: str) -> bool",
    "providers": "(hook_name: str) -> list[str]",
    "hooks": "() -> dict[str, list[tuple[str, Callable[..., Any]]]]",
    "clear": "() -> None",
}

OBJECT_REGISTRY_METHODS: dict[str, str] = {
    "handlers": "() -> dict[str, dbwarden.plugin.ObjectHandlerRegistration]",
    "has_handler": "(object_type: str) -> bool",
    "clear": "() -> None",
}

EXCEPTION_NAMES: frozenset[str] = frozenset({
    "DBWardenError",
    "DBWardenConfigError",
    "ConfigurationError",
    "DatabaseError",
    "DBDisconnectedError",
    "DirectoryNotFoundError",
    "ImmutableChangeError",
    "LockError",
    "NoMigrationsError",
    "NoSeedsError",
    "PendingMigrationsError",
    "SeedError",
    "VersionNotFoundError",
})

# These two declare __all__, so the snapshot is exhaustive: an added export is a
# failure here until it is added below too.
ENGINE_CORE_ALL: frozenset[str] = frozenset({
    "RunPhase",
    "Op",
    "ObjectHandler",
    "Anchor",
    "OrderingConstraint",
    "OrderingError",
    "StatementOrder",
    "MigrationStatement",
    "Change",
    "RegistryDriver",
    "ModelColumn",
    "IndexInfo",
    "ModelTable",
    "STATE_FORMAT_VERSION",
    "model_state_to_dict",
    "normalize_model_state",
    "reconstruct_model_table",
    "reconstruct_model_column",
    "compute_checksum",
    "get_schemas_directory",
    "write_snapshot",
    "read_snapshot",
    "find_latest_snapshot",
    "extract_snapshot_tables",
    "TableRenameIntent",
    "RENAME_TABLE_OVERLAP_THRESHOLD",
    "detect_renames",
})

PLUGIN_API_ALL: frozenset[str] = frozenset({
    "ClusterableStatement",
    "REDACTED",
    "build_alter_policy_sql",
    "build_create_policy_sql",
    "build_grant_sql",
    "build_revoke_sql",
    "emit_with_cluster",
    "has_visible_secrets",
    "qualified_name",
    "quote_pg",
    "strip_secret_values",
})

CONFORMANCE_SIGNATURES: dict[str, str] = {
    "assert_entry_point_declared": (
        "(distribution: str | None = None, *, pyproject_path: str | pathlib.Path = 'pyproject.toml') -> None"
    ),
    "assert_import_has_no_side_effects": "(package: str) -> None",
    "assert_setup_registers": (
        "(setup: Callable[[Any], NoneType], *, plugin: str = 'plugin-under-test', "
        "value_hooks: tuple[str, ...] = (), object_types: tuple[str, ...] = ()) "
        "-> dbwarden.plugin_conformance.RecordingRegistrar"
    ),
    "assert_hook_signatures": (
        "(setup: Callable[[Any], NoneType], *, plugin: str = 'plugin-under-test') -> None"
    ),
    "assert_core_imports_resolve": "(package: str) -> None",
    "assert_api_version_declared": "(package: str) -> None",
    "core_imports_outside_stable_api": "(package: str) -> list[str]",
    "assert_object_handler_conformance": (
        "(handler: Any, *, snapshot: dict[str, Any] | None = None, config: Any = None, "
        "model_spec: dict[str, Any] | None = None) -> None"
    ),
    "assert_ordering_constraint_satisfiable": "(handler: Any) -> None",
    "assert_idempotent_setup": (
        "(setup: Callable[[Any], NoneType], *, plugin: str = 'plugin-under-test') -> None"
    ),
}


# --- dbwarden.plugin -------------------------------------------------------

@pytest.mark.parametrize("name", sorted(PLUGIN_NAMES))
def test_plugin_module_exports_promised_name(name: str) -> None:
    assert hasattr(plugin, name), (
        f"dbwarden.plugin no longer exports '{name}'. Plugins import it directly."
    )


def test_plugin_all_matches_snapshot() -> None:
    """Exhaustive: `__all__` is the promise, so it may not drift from this file.

    The module also holds the CLI's install and provenance machinery, which stays
    importable but is deliberately absent from `__all__`: promising it would mean
    freezing how dbwarden installs plugins.
    """
    assert set(plugin.__all__) == set(PLUGIN_NAMES)


def test_exceptions_all_matches_snapshot() -> None:
    assert set(exceptions.__all__) == EXCEPTION_NAMES


def _signature(fn) -> str:
    """Render a signature with annotations resolved.

    The modules under test use `from __future__ import annotations`, so without
    eval_str every annotation renders as a quoted string and the snapshot would
    describe the quoting rather than the API.
    """
    import re

    sig = str(inspect.signature(fn, eval_str=True))
    # Normalize across Python versions: strip typing. prefix (3.12) and
    # private submodule segments like pathlib._local.Path (3.13).
    sig = sig.replace("typing.", "")
    sig = re.sub(r"\._[a-zA-Z_][a-zA-Z0-9_]*\.", ".", sig)
    return sig


@pytest.mark.parametrize("name,signature", sorted(REGISTRAR_METHODS.items()))
def test_plugin_registrar_method_signature(name: str, signature: str) -> None:
    assert _signature(getattr(plugin.PluginRegistrar, name)) == signature


@pytest.mark.parametrize("name,signature", sorted(HOOK_REGISTRY_METHODS.items()))
def test_hook_registry_method_signature(name: str, signature: str) -> None:
    assert _signature(getattr(plugin.HookRegistry, name)) == signature


@pytest.mark.parametrize("name,signature", sorted(OBJECT_REGISTRY_METHODS.items()))
def test_object_registry_method_signature(name: str, signature: str) -> None:
    assert _signature(getattr(plugin.ObjectPluginRegistry, name)) == signature


def test_plugin_api_version_is_an_int() -> None:
    assert isinstance(plugin.PLUGIN_API_VERSION, int)


def test_hook_call_specs_cover_every_known_hook() -> None:
    """A hook core can call must document how it calls it."""
    assert set(plugin.HOOK_CALL_SPECS) == set(plugin.KNOWN_VALUE_HOOKS)


def test_multi_value_hooks_are_known_hooks() -> None:
    assert plugin.MULTI_VALUE_HOOKS <= plugin.KNOWN_VALUE_HOOKS


# --- dbwarden.exceptions ---------------------------------------------------

@pytest.mark.parametrize("name", sorted(EXCEPTION_NAMES))
def test_exception_is_exported(name: str) -> None:
    assert hasattr(exceptions, name)


@pytest.mark.parametrize("name", sorted(EXCEPTION_NAMES))
def test_exception_descends_from_the_base(name: str) -> None:
    """A plugin catching DBWardenError should catch all of them."""
    assert issubclass(getattr(exceptions, name), exceptions.DBWardenError)


# --- dbwarden.engine.core and plugin_api -----------------------------------

def test_engine_core_all_matches_snapshot() -> None:
    assert set(engine_core.__all__) == ENGINE_CORE_ALL


def test_plugin_api_all_matches_snapshot() -> None:
    assert set(plugin_api.__all__) == PLUGIN_API_ALL


@pytest.mark.parametrize("name", sorted(ENGINE_CORE_ALL))
def test_engine_core_export_resolves(name: str) -> None:
    """__all__ entries must actually exist; a stale one breaks `import *`."""
    assert hasattr(engine_core, name)


@pytest.mark.parametrize("name", sorted(PLUGIN_API_ALL))
def test_plugin_api_export_resolves(name: str) -> None:
    assert hasattr(plugin_api, name)


# --- dbwarden.plugin_conformance -------------------------------------------

@pytest.mark.parametrize("name,signature", sorted(CONFORMANCE_SIGNATURES.items()))
def test_conformance_signature(name: str, signature: str) -> None:
    """Plugin suites call these by keyword; the parameters are the contract."""
    assert _signature(getattr(conformance, name)) == signature


def test_conformance_error_is_an_assertion_error() -> None:
    """Plugin suites rely on failures reading as assertions, not crashes."""
    assert issubclass(conformance.ConformanceError, AssertionError)
