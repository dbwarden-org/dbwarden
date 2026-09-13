from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from dbwarden.config_schema import (
    DatabaseEntry,
    DatabaseType,
    ProjectConfigEntry,
    structure_database_entry,
    structure_project_config,
)
from dbwarden.exceptions import ConfigurationError

if TYPE_CHECKING:
    from dbwarden.db_handle import DatabaseHandle


@dataclass
class _Registry:
    _entries: list[DatabaseEntry]
    _project_config: ProjectConfigEntry | None
    _reset_hooks: list[Callable[[], None]]
    _project_config_class_name: str | None = None

    def add(self, entry: DatabaseEntry) -> None:
        self._entries.append(entry)

    def entries(self) -> list[DatabaseEntry]:
        return list(self._entries)

    def set_project_config(self, entry: ProjectConfigEntry, class_name: str = "") -> None:
        if self._project_config is not None:
            first = self._project_config_class_name or "FirstClass"
            raise ConfigurationError(
                f"Only one concrete DbwardenConfig may be declared. "
                f"'{first}' is already registered; cannot register '{class_name}'."
            )
        self._project_config = entry
        self._project_config_class_name = class_name

    def project_config(self) -> ProjectConfigEntry | None:
        return self._project_config

    def reset(self) -> None:
        self._entries = []
        self._project_config = None
        self._project_config_class_name = None
        for hook in self._reset_hooks:
            hook()

    def register_reset_hook(self, hook: Callable[[], None]) -> None:
        self._reset_hooks.append(hook)


_REGISTRY = _Registry(_entries=[], _project_config=None, _reset_hooks=[])


def register_reset_hook(hook: Callable[[], None]) -> None:
    _REGISTRY.register_reset_hook(hook)


def reset_registry() -> None:
    _REGISTRY.reset()


def registered_entries() -> list[DatabaseEntry]:
    return _REGISTRY.entries()


def registered_project_config() -> ProjectConfigEntry | None:
    return _REGISTRY.project_config()


def database_config(
    *,
    database_name: str,
    database_type: DatabaseType = "sqlite",
    database_url_sync: str | None = None,
    database_url_async: str | None = None,
    secure_values: bool = False,
    skip_if_missing: bool = False,
    default: bool = False,
    migrations_dir: str | None = None,
    migration_table: str | None = None,
    model_paths: list[str] | None = None,
    model_tables: list[str] | None = None,
    dev_database_type: DatabaseType | None = None,
    dev_database_url: str | None = None,
    overlap_models: bool = False,
    auto_apply_seeds: bool = False,
    seed_table: str | None = None,
    pg_schema: str | None = None,
    pg_migration_lock_timeout: int | None = None,
    ch_cluster: str | None = None,
    ch_replicated_database: bool = False,
    clickhouse_lock_ttl: int | None = None,
    lock_namespace: str | None = None,
    migration_hooks: dict[str, list[Callable[..., Any]]] | None = None,
    **plugin_config: Any,
) -> DatabaseHandle:
    """Declare a database.

    Backend object keys such as ``pg_roles`` or ``ch_grants`` are contributed by
    plugins and accepted through ``**plugin_config``. They are validated against
    the plugins actually installed, so a key whose plugin is missing raises here
    rather than being silently ignored at migration time.
    """
    values = dict(
        database_name=database_name,
        database_type=database_type,
        database_url_sync=database_url_sync,
        database_url_async=database_url_async,
        secure_values=secure_values,
        skip_if_missing=skip_if_missing,
        default=default,
        migrations_dir=migrations_dir,
        migration_table=migration_table,
        model_paths=model_paths,
        model_tables=model_tables,
        dev_database_type=dev_database_type,
        dev_database_url=dev_database_url,
        overlap_models=overlap_models,
        auto_apply_seeds=auto_apply_seeds,
        seed_table=seed_table,
        pg_schema=pg_schema,
        pg_migration_lock_timeout=pg_migration_lock_timeout,
        ch_cluster=ch_cluster,
        ch_replicated_database=ch_replicated_database,
        clickhouse_lock_ttl=clickhouse_lock_ttl,
        lock_namespace=lock_namespace,
        migration_hooks=migration_hooks,
    )
    return _register_config_values(values, plugin_config)


def _register_config_values(
    values: dict[str, Any], plugin_config: dict[str, Any] | None = None
) -> DatabaseHandle:
    from dbwarden.db_handle import DatabaseHandle as _DH

    plugin_values = {
        key: value for key, value in (plugin_config or {}).items() if value is not None
    }
    _validate_plugin_config(plugin_values)
    entry_values = dict(values)
    entry_values["plugin_config"] = plugin_values
    entry = structure_database_entry(entry_values)
    _REGISTRY.add(entry)
    return _DH(entry.database_name, entry.database_type)


class _DbwardenDatabaseMeta(type):
    """Register concrete declarative database definitions at import time."""

    def __new__(mcls, name: str, bases: tuple[type, ...], namespace: dict[str, Any]):
        cls = super().__new__(mcls, name, bases, namespace)
        if namespace.get("__abstract__", False):
            return cls
        if not any(isinstance(base, _DbwardenDatabaseMeta) for base in bases):
            return cls

        values: dict[str, Any] = dict(_DECLARATIVE_DEFAULTS)
        plugin_values: dict[str, Any] = {}
        for base in reversed(cls.__mro__[1:]):
            for key in _DECLARATIVE_FIELDS:
                if key in base.__dict__:
                    value = base.__dict__[key]
                    values[key] = deepcopy(value) if key in _MUTABLE_FIELDS and value is not None else value
            inherited_plugins = base.__dict__.get("plugin_config", {})
            if inherited_plugins:
                plugin_values.update(deepcopy(inherited_plugins))
            for key in _plugin_config_keys():
                if key in base.__dict__:
                    plugin_values[key] = deepcopy(base.__dict__[key])
        values.update({
            key: deepcopy(value) if key in _MUTABLE_FIELDS and value is not None else value
            for key, value in namespace.items()
            if key in _DECLARATIVE_FIELDS
        })
        plugin_values.update(deepcopy(namespace.get("plugin_config", {})))
        for key in _plugin_config_keys():
            if key in namespace:
                plugin_values[key] = deepcopy(namespace[key])
        if not values.get("database_name") or (not values.get("database_url_sync") and not values.get("database_url_async")):
            raise ValueError(
                f"Concrete {name} must define database_name and at least one database URL"
            )
        # Resolve the function through the live module so declarative classes
        # remain registered even if plugin/config loading reloads this module.
        import sys
        register = sys.modules[__name__]._register_config_values
        try:
            handle = register(values, plugin_values)
        except Exception as exc:
            raise type(exc)(f"Declarative class {name}: {exc}") from exc
        cls.handle = handle
        return cls


_DECLARATIVE_FIELDS = {
    "database_name", "database_type", "database_url_sync", "database_url_async",
    "secure_values", "skip_if_missing", "default", "migrations_dir", "migration_table",
    "model_paths", "model_tables", "dev_database_type", "dev_database_url", "overlap_models",
    "auto_apply_seeds", "seed_table", "pg_schema", "pg_migration_lock_timeout",
    "ch_cluster", "ch_replicated_database",
    "clickhouse_lock_ttl", "lock_namespace", "migration_hooks",
}
_DECLARATIVE_DEFAULTS = {
    "database_type": "sqlite",
    "database_url_sync": None,
    "database_url_async": None,
    "secure_values": False,
    "skip_if_missing": False,
    "default": False,
    "migrations_dir": None,
    "migration_table": None,
    "model_paths": None,
    "model_tables": None,
    "dev_database_type": None,
    "dev_database_url": None,
    "overlap_models": False,
    "auto_apply_seeds": False,
    "seed_table": None,
    "pg_schema": None,
    "pg_migration_lock_timeout": None,
    "ch_cluster": None,
    "ch_replicated_database": False,
    "clickhouse_lock_ttl": None,
    "lock_namespace": None,
    "migration_hooks": None,
}
_MUTABLE_FIELDS = {"model_paths", "model_tables", "migration_hooks"}


def _plugin_config_keys() -> set[str]:
    from dbwarden.plugin import ConfigKeyRegistry, PLUGIN_CONFIG_KEY_OWNERS

    return set(PLUGIN_CONFIG_KEY_OWNERS) | set(ConfigKeyRegistry.keys())


class DbwardenDatabase(metaclass=_DbwardenDatabaseMeta):
    """Base class for automatically registered database configuration."""

    __abstract__ = True


def _register_project_config_values(values: dict[str, Any], class_name: str = "") -> None:
    _REGISTRY.set_project_config(structure_project_config(values), class_name=class_name)


class _DbwardenConfigMeta(type):
    """Register the one concrete project configuration at import time."""

    def __new__(mcls, name: str, bases: tuple[type, ...], namespace: dict[str, Any]):
        cls = super().__new__(mcls, name, bases, namespace)
        if namespace.get("__abstract__", False):
            return cls
        if not any(isinstance(base, _DbwardenConfigMeta) for base in bases):
            return cls

        values: dict[str, Any] = {
            key: deepcopy(value) if key in _PROJECT_CONFIG_MUTABLE_FIELDS else value
            for key, value in _PROJECT_CONFIG_DEFAULTS.items()
        }
        for base in reversed(cls.__mro__[1:]):
            for key in _PROJECT_CONFIG_FIELDS:
                if key in base.__dict__:
                    value = base.__dict__[key]
                    values[key] = (
                        deepcopy(value)
                        if key in _PROJECT_CONFIG_MUTABLE_FIELDS
                        else value
                    )
        values.update(
            {
                key: deepcopy(value) if key in _PROJECT_CONFIG_MUTABLE_FIELDS else value
                for key, value in namespace.items()
                if key in _PROJECT_CONFIG_FIELDS
            }
        )
        import sys

        register = sys.modules[__name__]._register_project_config_values
        try:
            register(values, class_name=name)
        except Exception as exc:
            raise type(exc)(f"Declarative class {name}: {exc}") from exc
        return cls


_PROJECT_CONFIG_FIELDS = {
    "pre_migrate_safety",
    "pre_migrate_impact",
    "missing_plan",
    "impact_paths",
}
_PROJECT_CONFIG_DEFAULTS = {
    "pre_migrate_safety": "off",
    "pre_migrate_impact": "off",
    "missing_plan": "off",
    "impact_paths": ["."],
}
_PROJECT_CONFIG_MUTABLE_FIELDS = {"impact_paths"}


class DbwardenConfig(metaclass=_DbwardenConfigMeta):
    """Base class for the singleton project-wide migration policy."""

    __abstract__ = True


def _validate_plugin_config(plugin_config: dict[str, Any]) -> None:
    from dbwarden.exceptions import DBWardenConfigError
    from dbwarden.plugin import PLUGIN_CONFIG_KEY_OWNERS, ConfigKeyRegistry

    for key, value in plugin_config.items():
        if ConfigKeyRegistry.is_registered(key):
            continue
        owner = PLUGIN_CONFIG_KEY_OWNERS.get(key)
        if owner is not None:
            if not value:
                # Declared but empty: nothing to migrate either way, so don't
                # force an install just to pass an empty list.
                continue
            raise DBWardenConfigError(
                f"database_config(...) got '{key}', which is provided by the "
                f"{owner} plugin, but that plugin is not installed.\n"
                f"Install it with: dbwarden plugin add {owner}"
            )
        raise DBWardenConfigError(
            f"database_config(...) got an unexpected keyword argument '{key}'."
        )
