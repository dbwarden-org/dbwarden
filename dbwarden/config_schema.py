from __future__ import annotations

from typing import Any, Literal
import re

import cattrs
from attrs import define, field, validators
from cattrs import transform_error

from dbwarden.exceptions import ConfigurationError
from dbwarden.plugin import PLUGIN_CONFIG_KEY_OWNERS

DatabaseType = Literal["sqlite", "postgresql", "mysql", "mariadb", "clickhouse"]
ProjectPolicy = Literal["off", "warn", "block"]
VALID_DATABASE_TYPES = frozenset(
    {"sqlite", "postgresql", "mysql", "mariadb", "clickhouse"}
)
VALID_PROJECT_POLICIES = frozenset({"off", "warn", "block"})

DATABASE_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")
MODEL_PATH_RE = re.compile(r"^[a-zA-Z_./][a-zA-Z0-9_./-]*$")
IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
MAX_PATH_LENGTH = 200
MAX_PATH_COUNT = 10


def _validate_database_name(_self, _attribute, value: str) -> None:
    if not value or not DATABASE_NAME_RE.match(value):
        raise ValueError(
            "Invalid database_name. Must start with letter, contain only "
            "alphanumeric and underscores."
        )


def _validate_model_paths_for_list(value: list[str]) -> None:
    """Validate model_paths when it's a non-empty list."""
    if not isinstance(value, list):
        raise ValueError("model_paths must be a list")
    if len(value) > MAX_PATH_COUNT:
        raise ValueError(f"Too many model_paths: max {MAX_PATH_COUNT}")
    for path in value:
        if not isinstance(path, str):
            raise ValueError("model_paths must contain strings")
        if len(path) > MAX_PATH_LENGTH:
            raise ValueError(
                f"model_path too long: max {MAX_PATH_LENGTH} chars"
            )
        if ".." in path or path.startswith("/"):
            raise ValueError(
                f"Invalid model_path '{path}': no absolute paths or traversal"
            )
        if not MODEL_PATH_RE.match(path):
            raise ValueError(
                f"Invalid model_path '{path}': must be relative, alphanumeric/underscore"
            )


def _validate_data_paths(_self, _attribute, value: list[str]) -> None:
    try:
        _validate_model_paths_for_list(value)
    except ValueError as exc:
        raise ValueError(str(exc).replace("model_paths", "data_paths").replace("model_path", "data_path")) from exc


def _validate_relative_path(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty relative path")
    if len(value) > MAX_PATH_LENGTH or ".." in value or value.startswith("/") or not MODEL_PATH_RE.match(value):
        raise ValueError(f"Invalid {field_name} '{value}': no absolute paths or traversal")


def _validate_data_snapshot_dir(_self, _attribute, value: str) -> None:
    _validate_relative_path("data_snapshot_dir", value)


def _validate_snapshot_registry(_self, _attribute, value: str) -> None:
    _validate_relative_path("snapshot_registry", value)


def _validate_database_type(_self, _attribute, value: str) -> None:
    if value not in VALID_DATABASE_TYPES:
        allowed = ", ".join(sorted(VALID_DATABASE_TYPES))
        raise ValueError(
            f"Invalid database_type '{value}'. Must be one of: {allowed}"
        )


def _validate_identifier_field(field_name: str, value: str) -> None:
    if not value or not IDENTIFIER_RE.match(value):
        raise ValueError(
            f"Invalid {field_name} '{value}'. Must start with letter/underscore and contain only alphanumeric or underscore."
        )


def _validate_migration_table(_self, _attribute, value: str | None) -> None:
    if value is None:
        return
    _validate_identifier_field("migration_table", value)


def _validate_seed_table(_self, _attribute, value: str | None) -> None:
    if value is None:
        return
    _validate_identifier_field("seed_table", value)


def _validate_pg_schema(_self, _attribute, value: str | None) -> None:
    if value is None:
        return
    _validate_identifier_field("pg_schema", value)


def _validate_lock_timeout(_self, _attribute, value: int | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("pg_migration_lock_timeout must be a non-negative integer")


def _validate_project_policy_value(field_name: str, value: str) -> None:
    if value not in VALID_PROJECT_POLICIES:
        raise ValueError(f"{field_name} must be one of: off, warn, block")


def _validate_project_policy(_self, attribute, value: str) -> None:
    _validate_project_policy_value(attribute.name, value)


def _validate_impact_paths(_self, _attribute, value: list[str]) -> None:
    if not isinstance(value, list) or not all(isinstance(path, str) for path in value):
        raise ValueError("impact_paths must be a list of strings")
    for path in value:
        if path.startswith("/"):
            raise ValueError(f"impact_paths must not contain absolute paths: {path}")
        if ".." in path:
            raise ValueError(f"impact_paths must not contain traversal: {path}")
        # Check for symlink escapes (Section 6.1)
        from pathlib import Path
        resolved = Path(path).resolve()
        cwd_resolved = Path.cwd().resolve()
        if not resolved.is_relative_to(cwd_resolved):
            raise ValueError(
                f"impact_paths must not escape the project root via symlinks: {path}"
            )


@define(slots=False)
class DatabaseEntry:
    database_name: str = field(validator=_validate_database_name)
    database_type: DatabaseType = field(validator=_validate_database_type)
    database_url_sync: str | None = None
    database_url_async: str | None = None

    def __attrs_post_init__(self) -> None:
        if not self.database_url_sync and not self.database_url_async:
            raise ValueError(
                "At least one of database_url_sync or database_url_async must be provided."
            )

    secure_values: bool = False
    skip_if_missing: bool = False
    default: bool = False
    migrations_dir: str | None = None
    migration_table: str | None = field(default=None, validator=_validate_migration_table)
    model_paths: list[str] | None = None
    model_tables: list[str] | None = None
    data_paths: list[str] = field(factory=list, validator=_validate_data_paths)
    data_snapshot_dir: str = field(default=".dbwarden/data", validator=_validate_data_snapshot_dir)
    snapshot_registry: str = field(default=".dbwarden/snapshots/registry.json", validator=_validate_snapshot_registry)
    dev_database_type: DatabaseType | None = None
    dev_database_url: str | None = None
    overlap_models: bool = False
    auto_apply_seeds: bool = False
    seed_table: str | None = field(default=None, validator=_validate_seed_table)
    pg_schema: str | None = field(default=None, validator=_validate_pg_schema)
    pg_migration_lock_timeout: int | None = field(default=None, validator=_validate_lock_timeout)
    # ClickHouse cluster configuration
    ch_cluster: str | None = None
    ch_replicated_database: bool = False
    # ClickHouse lock tuning
    clickhouse_lock_ttl: int | None = field(default=None, validator=_validate_lock_timeout)
    lock_namespace: str | None = field(default=None, validator=_validate_migration_table)
    # Recovery policy (Sec 9): halt, resume_idempotent, force
    recovery_policy: str = "halt"
    # Pooling escape hatch (Sec 7.1.4): assume session pooling is safe
    assume_session_pooling: bool = False
    # Connection tuning (Sec 10.7, 10.19)
    tcp_keepalive: bool = True
    # SQLite busy_timeout (Sec 7.3.2)
    sqlite_busy_timeout: int | None = None
    # Per-statement history recording for non-TX engines (Sec 8.3.4)
    per_statement_history: bool = False
    # Rename policy for merge: strict, prompt, auto-high-confidence (Sec 9 R9.1.7)
    rename_policy: str = "prompt"
    split_at_severity: str | None = field(default=None, validator=validators.optional(validators.in_(("SAFE", "INFO", "WARN", "CRITICAL"))))
    max_severity: str = field(default="CRITICAL", validator=validators.in_(("SAFE", "INFO", "WARN", "CRITICAL")))
    strict_pending: bool = field(default=False, validator=validators.instance_of(bool))
    # Per-database migration lifecycle hooks
    migration_hooks: dict[str, list] | None = None
    # Environment registry for merge handling (persistent vs disposable)
    environments: list[Any] | None = None
    # Backend object keys contributed by plugins (pg_roles, ch_grants, and so on).
    # Kept as a dict so core does not have to know each plugin's key list.
    plugin_config: dict[str, Any] = field(factory=dict)

    def __getattr__(self, name: str) -> Any:
        # Lets consumers keep reading entry.pg_roles / entry.ch_grants. Only fires
        # for attributes attrs did not define, and only answers for keys a plugin
        # could own, so genuine typos still raise.
        if name in PLUGIN_CONFIG_KEY_OWNERS:
            return self.plugin_config.get(name, [])
        raise AttributeError(name)


@define(slots=False)
class ProjectConfigEntry:
    pre_migrate_safety: ProjectPolicy = field(
        default="off", validator=_validate_project_policy
    )
    pre_migrate_impact: ProjectPolicy = field(
        default="off", validator=_validate_project_policy
    )
    missing_plan: ProjectPolicy = field(
        default="off", validator=_validate_project_policy
    )
    impact_paths: list[str] = field(factory=list, validator=_validate_impact_paths)

    def __attrs_post_init__(self) -> None:
        if self.pre_migrate_impact == "block" and self.missing_plan != "block":
            raise ValueError(
                "missing_plan must be 'block' when pre_migrate_impact is 'block'"
            )


@define(slots=False)
class MultiDatabaseConfig:
    default: str
    databases: dict[str, DatabaseEntry]


_CONVERTER = cattrs.Converter(detailed_validation=True)


def structure_database_entry(kwargs: dict) -> DatabaseEntry:
    sync = kwargs.get("database_url_sync")
    async_ = kwargs.get("database_url_async")
    if not sync and not async_:
        raise ConfigurationError(
            "At least one of database_url_sync or database_url_async must be provided."
        )
    model_paths = kwargs.get("model_paths")
    if model_paths is not None:
        _validate_model_paths_for_list(model_paths)
    data_paths = kwargs.get("data_paths")
    if data_paths is None:
        data_paths = []
        kwargs = {**kwargs, "data_paths": data_paths}
    try:
        _validate_data_paths(None, None, data_paths)
        _validate_relative_path("data_snapshot_dir", kwargs.get("data_snapshot_dir", ".dbwarden/data"))
        _validate_relative_path("snapshot_registry", kwargs.get("snapshot_registry", ".dbwarden/snapshots/registry.json"))
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
    model_tables = kwargs.get("model_tables")
    pg_schema = kwargs.get("pg_schema")
    if pg_schema is not None:
        try:
            _validate_identifier_field("pg_schema", pg_schema)
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc
    lock_timeout = kwargs.get("pg_migration_lock_timeout")
    if lock_timeout is not None:
        try:
            _validate_lock_timeout(None, None, lock_timeout)
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc
    if model_tables is not None:
        if not isinstance(model_tables, list):
            raise ConfigurationError("model_tables must be a list of strings or None")
        if len(model_tables) > MAX_PATH_COUNT:
            raise ConfigurationError(f"Too many model_tables: max {MAX_PATH_COUNT}")
        for name in model_tables:
            if not isinstance(name, str):
                raise ConfigurationError("model_tables must contain strings")
            if not IDENTIFIER_RE.match(name):
                raise ConfigurationError(
                    f"Invalid table name '{name}' in model_tables. "
                    "Must start with letter/underscore and contain only alphanumeric or underscore."
                )
    try:
        return _CONVERTER.structure(kwargs, DatabaseEntry)
    except Exception as exc:
        messages = transform_error(exc)
        joined = "; ".join(messages) if messages else str(exc)
        raise ConfigurationError(joined) from exc


def structure_project_config(kwargs: dict[str, Any]) -> ProjectConfigEntry:
    for field_name in (
        "pre_migrate_safety",
        "pre_migrate_impact",
        "missing_plan",
    ):
        if field_name in kwargs:
            try:
                _validate_project_policy_value(field_name, kwargs[field_name])
            except ValueError as exc:
                raise ConfigurationError(str(exc)) from exc
    if "impact_paths" in kwargs:
        try:
            _validate_impact_paths(None, None, kwargs["impact_paths"])
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc
    if (
        kwargs.get("pre_migrate_impact", "off") == "block"
        and kwargs.get("missing_plan", "off") != "block"
    ):
        raise ConfigurationError(
            "missing_plan must be 'block' when pre_migrate_impact is 'block'"
        )
    try:
        return _CONVERTER.structure(kwargs, ProjectConfigEntry)
    except Exception as exc:
        messages = transform_error(exc)
        joined = "; ".join(messages) if messages else str(exc)
        raise ConfigurationError(joined) from exc
