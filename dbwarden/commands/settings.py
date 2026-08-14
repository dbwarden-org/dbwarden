from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from dbwarden.config import (
    display_value,
    get_database,
    get_multi_db_config,
    get_settings_source_file,
)
from dbwarden.config_schema import DatabaseEntry
from dbwarden.output import emit_json, json_mode, kv_table, render, section


def _display_db_type(value: str) -> str:
    mapping = {
        "postgresql": "PostgreSQL",
        "sqlite": "SQLite",
        "mysql": "MySQL",
        "mariadb": "MariaDB",
        "clickhouse": "ClickHouse",
    }
    return mapping.get(value, value)


def _print_field(label: str, value: Any) -> None:
    render(kv_table(None, ((label, value),)))


def _database_payload(name: str, default_name: str) -> dict:
    db_config = get_database(name)
    return {
        "default": name == default_name,
        "type": _display_db_type(db_config.database_type),
        "url": display_value(db_config, "database_url_sync", db_config.sqlalchemy_url),
        "migrations_dir": db_config.migrations_dir,
        "migration_table": db_config.migration_table,
        "seed_table": db_config.seed_table,
        "model_paths": db_config.model_paths,
        "dev_database_type": db_config.dev_database_type,
        "dev_database_url": display_value(
            db_config, "dev_database_url", db_config.dev_database_url
        ),
        "overlap_models": db_config.overlap_models,
        "skip_if_missing": db_config.skip_if_missing,
    }


def handle_settings_show(database: str | None = None, all_databases: bool = False) -> None:
    """Show current settings configuration."""
    config = get_multi_db_config()

    entries = [(name, config.databases[name]) for name in config.databases]

    if database and not all_databases:
        entries = [(n, d) for n, d in entries if n == database]
    elif all_databases:
        pass
    else:
        name = database or config.default
        entries = [(name, config.databases[name])] if name else []

    if json_mode():
        emit_json(
            {
                name: _database_payload(name, config.default)
                for name, _entry in entries
            }
        )
        return

    for name, _entry in entries:
        is_default = name == config.default
        label = f"Database: {name.upper()}"
        if is_default:
            label += " (default)"
        section(label)

        # get_database() returns the resolved DatabaseConfig. The raw
        # DatabaseEntry has neither sqlalchemy_url nor secure_display_values,
        # which display_value() needs to mask secrets.
        db_config = get_database(name)

        _print_field("Default", str(is_default))
        _print_field("Type", _display_db_type(db_config.database_type))
        _print_field(
            "URL", display_value(db_config, "database_url_sync", db_config.sqlalchemy_url)
        )
        _print_field("Migrations Directory", db_config.migrations_dir)
        _print_field("Migration Table", db_config.migration_table)
        _print_field("Seed Table", db_config.seed_table)
        _print_field("Model Paths", db_config.model_paths)
        _print_field("Dev Database Type", db_config.dev_database_type)
        _print_field(
            "Dev Database URL",
            display_value(db_config, "dev_database_url", db_config.dev_database_url),
        )
        _print_field("Overlap Models", str(db_config.overlap_models))
        _print_field("Skip If Missing", str(db_config.skip_if_missing))
