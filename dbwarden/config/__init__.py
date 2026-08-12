"""Configuration package (split for maintainability).

Re-exports all names previously defined in the monolithic config.py
for full backward compatibility.
"""

from dbwarden.config.state import (
    DatabaseConfig,
    DatabaseType,
    DEFAULT_MIGRATION_TABLE,
    DEFAULT_SEEDS_TABLE,
    MultiDbConfig,
    _ResolvedSource,
)
from dbwarden.config.resolve import (
    _build_database_target_key,
    _clear_source_cache,
    _discover_dbwarden_files,
    _extract_variable_value_expressions,
    _file_has_database_config_call,
    _full_scan_database_config_calls,
    _IGNORE_DIRS,
    _import_as_package_module,
    _IMPORT_ROOTS,
    _import_source,
    _infer_database_type,
    _is_literal_node,
    _MULTI_DB_CONFIG_CACHE,
    _MULTI_DB_CONFIG_CWD,
    _normalized_url,
    _RESOLVED_CWD,
    _RESOLVED_SOURCE_CACHE,
    _resolve_import_root,
    _resolve_source,
    _STRICT_TRANSLATION,
    _USE_DEV_DATABASE,
    _workspace_root,
    get_settings_source_file,
    get_toml_path,
    is_dev_mode,
    is_skip_disabled,
    is_strict_translation,
    set_dev_mode,
    set_disable_skip,
    set_strict_translation,
)
from dbwarden.config.build import (
    _entry_model_paths,
    _finalize_entries,
    display_value,
    get_config,
    get_database,
    get_multi_db_config,
    list_databases,
)
from dbwarden.config_registry import DbwardenDatabase
from dbwarden.exceptions import ConfigurationError
