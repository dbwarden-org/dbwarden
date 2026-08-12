from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from dbwarden.config.resolve import (
    _clear_source_cache,
    _extract_variable_value_expressions,
    _import_source,
    _infer_database_type,
    _normalized_url,
    _build_database_target_key,
    _resolve_source,
    is_dev_mode,
)
from dbwarden.config.state import (
    DatabaseConfig,
    DEFAULT_MIGRATION_TABLE,
    DEFAULT_SEEDS_TABLE,
    MultiDbConfig,
)
from dbwarden.config_registry import registered_entries, reset_registry
from dbwarden.config_schema import DatabaseEntry
from dbwarden.exceptions import ConfigurationError


def _entry_model_paths(entry: DatabaseEntry) -> set[str]:
    return {p for p in (entry.model_paths or [])}


def _finalize_entries(
    entries: list[DatabaseEntry],
    base_dir: Path,
    variable_value_expressions: list[dict[str, str]] | None = None,
) -> MultiDbConfig:
    if not entries:
        raise ConfigurationError(
            "No database_config(...) call found. Add dbwarden init or set DBWARDEN_CONFIG_MODULE."
        )

    defaults = [e for e in entries if e.default]
    if len(defaults) != 1:
        raise ConfigurationError(
            f"Exactly one default=True required, found {len(defaults)}"
        )

    if len(entries) > 1:
        missing_model_paths = [e.database_name for e in entries if not e.model_paths]
        if missing_model_paths:
            names = ", ".join(missing_model_paths)
            raise ConfigurationError(
                f"model_paths is required when more than one database is configured. Missing for: {names}"
            )

    databases: dict[str, DatabaseConfig] = {}
    url_owners: dict[str, str] = {}
    migration_owners: dict[str, str] = {}
    target_owners: dict[str, str] = {}

    for index, entry in enumerate(entries):
        if entry.database_name in databases:
            raise ConfigurationError(f"Duplicate database_name: '{entry.database_name}'")

        migrations_dir = entry.migrations_dir or f"migrations/{entry.database_name}"
        if migrations_dir in migration_owners:
            raise ConfigurationError(
                f"Duplicate migrations_dir: '{migrations_dir}' in '{entry.database_name}' and '{migration_owners[migrations_dir]}'"
            )
        migration_owners[migrations_dir] = entry.database_name

        if entry.database_url_sync:
            normalized_url = _normalized_url(entry.database_url_sync)
            if normalized_url in url_owners:
                raise ConfigurationError(
                    f"Duplicate database_url_sync: '{entry.database_url_sync}'"
                )
            url_owners[normalized_url] = entry.database_name

            target_key = _build_database_target_key(
                entry.database_url_sync,
                entry.database_type,
                base_dir,
            )
            if target_key in target_owners:
                raise ConfigurationError(
                    "Duplicate database target detected: "
                    f"'{entry.database_name}' collides with '{target_owners[target_key]}'"
                )
            target_owners[target_key] = entry.database_name

        if entry.database_url_async:
            async_target_key = _build_database_target_key(
                entry.database_url_async,
                entry.database_type,
                base_dir,
            )
            if async_target_key in target_owners and target_owners[async_target_key] != entry.database_name:
                raise ConfigurationError(
                    "Duplicate database target detected for async URL: "
                    f"'{entry.database_name}' collides with '{target_owners[async_target_key]}'"
                )
            target_owners[async_target_key] = entry.database_name

        if not entry.database_url_sync and not entry.database_url_async:
            raise ConfigurationError(
                f"At least one of database_url_sync or database_url_async must be provided for '{entry.database_name}'."
            )

        if entry.dev_database_type and not entry.dev_database_url:
            raise ConfigurationError(
                f"dev_database_url is required when dev_database_type is set for '{entry.database_name}'"
            )

        if entry.dev_database_url:
            dev_url_key = _normalized_url(entry.dev_database_url)
            owner = url_owners.get(dev_url_key)
            if owner is not None:
                raise ConfigurationError(
                    f"Duplicate dev_database_url: '{entry.dev_database_url}'"
                )
            url_owners[dev_url_key] = entry.database_name

            dev_type = entry.dev_database_type or _infer_database_type(
                entry.dev_database_url
            )
            dev_target_key = _build_database_target_key(
                entry.dev_database_url,
                dev_type,
                base_dir,
            )
            if dev_target_key in target_owners:
                raise ConfigurationError(
                    "Duplicate database target detected for dev database: "
                    f"'{entry.database_name}' collides with '{target_owners[dev_target_key]}'"
                )
            target_owners[dev_target_key] = entry.database_name

        secure_display_values: dict[str, str] = {}
        if entry.secure_values and variable_value_expressions is not None:
            if index < len(variable_value_expressions):
                secure_display_values = variable_value_expressions[index]

        databases[entry.database_name] = DatabaseConfig(
            sqlalchemy_url_sync=entry.database_url_sync,
            sqlalchemy_url_async=entry.database_url_async,
            database_type=entry.database_type,
            secure_values=entry.secure_values,
            skip_if_missing=entry.skip_if_missing,
            secure_display_values=secure_display_values,
            model_paths=entry.model_paths,
            model_tables=entry.model_tables,
            migrations_dir=migrations_dir,
            migration_table=entry.migration_table or DEFAULT_MIGRATION_TABLE,
            seed_table=entry.seed_table or DEFAULT_SEEDS_TABLE,
            auto_apply_seeds=entry.auto_apply_seeds,
            postgres_schema=entry.pg_schema,
            dev_database_url=entry.dev_database_url,
            dev_database_type=entry.dev_database_type,
            overlap_models=entry.overlap_models,
            pg_migration_lock_timeout=entry.pg_migration_lock_timeout,
            plugin_config=dict(entry.plugin_config or {}),
        )

    for i, left in enumerate(entries):
        for right in entries[i + 1 :]:
            overlap = _entry_model_paths(left).intersection(_entry_model_paths(right))
            if not overlap:
                continue
            if not left.overlap_models:
                raise ConfigurationError(
                    "model_paths overlap detected: "
                    f"path '{sorted(overlap)[0]}' from '{right.database_name}' is also defined in '{left.database_name}'; "
                    "set overlap_models=True to allow"
                )
            if not right.overlap_models:
                raise ConfigurationError(
                    "model_paths overlap detected: "
                    f"path '{sorted(overlap)[0]}' from '{left.database_name}' is also defined in '{right.database_name}'; "
                    "set overlap_models=True to allow"
                )

    for i, left in enumerate(entries):
        for right in entries[i + 1 :]:
            left_tables = set(left.model_tables or [])
            right_tables = set(right.model_tables or [])
            if not left_tables or not right_tables:
                continue
            overlap = left_tables.intersection(right_tables)
            if not overlap:
                continue
            if not left.overlap_models:
                raise ConfigurationError(
                    "model_tables overlap detected: "
                    f"table '{sorted(overlap)[0]}' in '{left.database_name}' is also in '{right.database_name}'; "
                    "set overlap_models=True to allow"
                )
            if not right.overlap_models:
                raise ConfigurationError(
                    "model_tables overlap detected: "
                    f"table '{sorted(overlap)[0]}' in '{right.database_name}' is also in '{left.database_name}'; "
                    "set overlap_models=True to allow"
                )

    default_name = defaults[0].database_name
    return MultiDbConfig(databases=databases, default=default_name)


def get_multi_db_config() -> MultiDbConfig:
    import dbwarden.config.resolve as _resolve

    current_cwd = str(Path.cwd().resolve())
    if _resolve._MULTI_DB_CONFIG_CACHE is not None and _resolve._MULTI_DB_CONFIG_CWD == current_cwd:
        return _resolve._MULTI_DB_CONFIG_CACHE

    reset_registry()
    _resolve._MULTI_DB_CONFIG_CWD = current_cwd

    source = _resolve_source()
    variable_value_expressions: list[dict[str, str]] | None = None
    if source.kind == "file":
        variable_value_expressions = _extract_variable_value_expressions(
            Path(source.value)
        )
    base_dir = _import_source(source)
    entries = registered_entries()
    result = _finalize_entries(entries, base_dir, variable_value_expressions)
    _resolve._MULTI_DB_CONFIG_CACHE = result
    return result


def display_value(db: DatabaseConfig, field_name: str, value: Any) -> Any:
    if db.secure_values and field_name in db.secure_display_values:
        return db.secure_display_values[field_name]
    return value


def get_database(name: str | None = None) -> DatabaseConfig:
    config = get_multi_db_config()

    if name is None:
        name = config.default

    if name not in config.databases:
        available = list(config.databases.keys())
        raise ConfigurationError(
            f"Database '{name}' not found in settings config. Available databases: {available}"
        )

    selected = config.databases[name]

    if not is_dev_mode():
        return selected

    if not selected.dev_database_url:
        raise ConfigurationError(
            f"--dev mode is enabled, but database '{name}' has no dev_database_url configured."
        )

    dev_database_type = selected.dev_database_type or _infer_database_type(
        selected.dev_database_url
    )

    return replace(
        selected,
        sqlalchemy_url_sync=selected.dev_database_url,
        sqlalchemy_url_async=selected.dev_database_url,
        database_type=dev_database_type,
    )


def list_databases() -> list[str]:
    return list(get_multi_db_config().databases.keys())


def get_config() -> DatabaseConfig:
    return get_database(None)
