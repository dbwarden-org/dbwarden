"""Environment registry for merge handling.

Manages persistent vs disposable environment classification.
Persistent environments require reconciliation after a dirty merge;
disposable environments can be reset.
"""
from __future__ import annotations

from dataclasses import dataclass

from dbwarden.logging import get_component_logger

logger = get_component_logger("merge")


@dataclass
class EnvironmentConfig:
    """Configuration for a database environment.

    Attributes:
        name: Environment name (e.g., "staging", "production").
        url_env: Environment variable name for connection URL.
        persistent: True if data must survive (cannot be reset).
    """
    name: str
    url_env: str
    persistent: bool


def load_environments(db_name: str | None = None) -> list[EnvironmentConfig]:
    """Load environment configuration for a database.

    Args:
        db_name: Database name. If None, uses default.

    Returns:
        List of EnvironmentConfig objects.
    """
    from dbwarden.config_registry import registered_entries

    try:
        entries = registered_entries()
        if db_name is not None:
            entries = [e for e in entries if e.database_name == db_name]
        else:
            entries = [e for e in entries if e.default]

        if not entries:
            return []

        raw_envs = getattr(entries[0], "environments", None) or []

        environments: list[EnvironmentConfig] = []
        for env in raw_envs:
            if isinstance(env, EnvironmentConfig):
                environments.append(env)
            elif isinstance(env, dict):
                environments.append(EnvironmentConfig(
                    name=env.get("name", ""),
                    url_env=env.get("url_env", ""),
                    persistent=env.get("persistent", False),
                ))

        return environments

    except Exception as e:
        logger.debug("Could not load environments for %s: %s", db_name, e)
        return []


def is_persistent(environment: str, db_name: str | None = None) -> bool:
    """Check if an environment is persistent.

    Args:
        environment: Environment name.
        db_name: Database name.

    Returns:
        True if the environment is persistent, False otherwise.
    """
    envs = load_environments(db_name)
    return any(e.name == environment and e.persistent for e in envs)


def get_persistent_environments(db_name: str | None = None) -> list[str]:
    """Get list of persistent environment names.

    Args:
        db_name: Database name.

    Returns:
        List of persistent environment names.
    """
    envs = load_environments(db_name)
    return [e.name for e in envs if e.persistent]


def get_disposable_environments(db_name: str | None = None) -> list[str]:
    """Get list of disposable environment names.

    Args:
        db_name: Database name.

    Returns:
        List of disposable environment names.
    """
    envs = load_environments(db_name)
    return [e.name for e in envs if not e.persistent]
