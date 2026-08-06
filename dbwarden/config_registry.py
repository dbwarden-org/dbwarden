from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from dbwarden.config_schema import DatabaseEntry, DatabaseType, structure_database_entry

if TYPE_CHECKING:
    from dbwarden.db_handle import DatabaseHandle


@dataclass
class _Registry:
    _entries: list[DatabaseEntry]
    _reset_hooks: list[Callable[[], None]]

    def add(self, entry: DatabaseEntry) -> None:
        self._entries.append(entry)

    def entries(self) -> list[DatabaseEntry]:
        return list(self._entries)

    def reset(self) -> None:
        self._entries = []
        for hook in self._reset_hooks:
            hook()

    def register_reset_hook(self, hook: Callable[[], None]) -> None:
        self._reset_hooks.append(hook)


_REGISTRY = _Registry(_entries=[], _reset_hooks=[])


def register_reset_hook(hook: Callable[[], None]) -> None:
    _REGISTRY.register_reset_hook(hook)


def reset_registry() -> None:
    _REGISTRY.reset()


def registered_entries() -> list[DatabaseEntry]:
    return _REGISTRY.entries()


def database_config(
    *,
    database_name: str,
    database_type: DatabaseType = "sqlite",
    database_url_sync: str | None = None,
    database_url_async: str | None = None,
    secure_values: bool = False,
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
    **plugin_config: Any,
) -> DatabaseHandle:
    """Declare a database.

    Backend object keys such as ``pg_roles`` or ``ch_grants`` are contributed by
    plugins and accepted through ``**plugin_config``. They are validated against
    the plugins actually installed, so a key whose plugin is missing raises here
    rather than being silently ignored at migration time.
    """
    from dbwarden.db_handle import DatabaseHandle as _DH

    _validate_plugin_config(plugin_config)

    entry = structure_database_entry(
        dict(
            database_name=database_name,
            database_type=database_type,
            database_url_sync=database_url_sync,
            database_url_async=database_url_async,
            secure_values=secure_values,
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
            plugin_config={k: v for k, v in plugin_config.items() if v is not None},
        )
    )
    _REGISTRY.add(entry)
    return _DH(entry.database_name, entry.database_type)


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
