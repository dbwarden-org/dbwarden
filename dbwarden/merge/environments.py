"""Environment registry for merge handling.

Manages persistent vs disposable environment classification.
Persistent environments require reconciliation after a dirty merge;
disposable environments can be reset.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

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


def load_environments(db_name: str | None = None) -> dict[str, EnvironmentConfig]:
    """Load environment configuration for a database.

    Args:
        db_name: Database name. If None, uses default.

    Returns:
        Dict of environment name -> EnvironmentConfig.
    """
    from dbwarden.config import get_database

    try:
        config = get_database(db_name)
        raw_envs = getattr(config, "environments", {}) or {}

        environments = {}
        for name, env_config in raw_envs.items():
            if isinstance(env_config, dict):
                environments[name] = EnvironmentConfig(
                    name=name,
                    url_env=env_config.get("url_env", ""),
                    persistent=env_config.get("persistent", False),
                )
            elif isinstance(env_config, EnvironmentConfig):
                environments[name] = env_config

        return environments

    except Exception as e:
        logger.debug("Could not load environments for %s: %s", db_name, e)
        return {}


def is_persistent(environment: str, db_name: str | None = None) -> bool:
    """Check if an environment is persistent.

    Args:
        environment: Environment name.
        db_name: Database name.

    Returns:
        True if the environment is persistent, False otherwise.
    """
    envs = load_environments(db_name)
    env_config = envs.get(environment)
    return env_config is not None and env_config.persistent


def get_persistent_environments(db_name: str | None = None) -> list[str]:
    """Get list of persistent environment names.

    Args:
        db_name: Database name.

    Returns:
        List of persistent environment names.
    """
    envs = load_environments(db_name)
    return [name for name, config in envs.items() if config.persistent]


def get_disposable_environments(db_name: str | None = None) -> list[str]:
    """Get list of disposable environment names.

    Args:
        db_name: Database name.

    Returns:
        List of disposable environment names.
    """
    envs = load_environments(db_name)
    return [name for name, config in envs.items() if not config.persistent]
