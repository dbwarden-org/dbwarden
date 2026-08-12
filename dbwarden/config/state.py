from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from dbwarden.plugin import PLUGIN_CONFIG_KEY_OWNERS

DatabaseType = Literal["sqlite", "postgresql", "mysql", "mariadb", "clickhouse"]
DEFAULT_MIGRATION_TABLE = "_dbwarden_migrations"
DEFAULT_SEEDS_TABLE = "_dbwarden_seeds"

_IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "site",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "examples",
    "tests",
}


@dataclass
class DatabaseConfig:
    database_type: DatabaseType
    sqlalchemy_url_sync: str | None = None
    sqlalchemy_url_async: str | None = None
    secure_values: bool = False
    skip_if_missing: bool = False
    secure_display_values: dict[str, str] = field(default_factory=dict)
    model_paths: list[str] | None = None
    model_tables: list[str] | None = None
    migrations_dir: str = "migrations"
    migration_table: str = DEFAULT_MIGRATION_TABLE
    seed_table: str = DEFAULT_SEEDS_TABLE
    auto_apply_seeds: bool = False
    postgres_schema: str | None = None
    dev_database_url: str | None = None
    dev_database_type: DatabaseType | None = None
    overlap_models: bool = False
    pg_migration_lock_timeout: int | None = None
    # Backend object keys contributed by plugins (pg_roles, ch_grants, and so on).
    plugin_config: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        # Keeps getattr(config, "pg_roles", None) working for callers across the
        # engine. Restricted to plugin-owned key names so typos still raise.
        if name in PLUGIN_CONFIG_KEY_OWNERS:
            return self.plugin_config.get(name, [])
        raise AttributeError(name)

    @property
    def sqlalchemy_url(self) -> str:
        if self.sqlalchemy_url_sync is not None:
            return self.sqlalchemy_url_sync
        if self.sqlalchemy_url_async:
            return self.sqlalchemy_url_async
        return ""


@dataclass
class MultiDbConfig:
    databases: dict[str, DatabaseConfig] = field(default_factory=dict)
    default: str = "default"


@dataclass
class _ResolvedSource:
    kind: Literal["file", "module"]
    value: str
    classification: Literal["isolated", "in_package"] | None = None
    import_root: str | None = None
    module_name: str | None = None
