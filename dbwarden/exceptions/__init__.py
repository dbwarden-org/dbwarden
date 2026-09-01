"""DBWarden exception hierarchy.

All exceptions in dbwarden inherit from ``DBWardenError``. This package
organises them by subsystem:

- ``dbwarden.exceptions.core`` -- configuration, database, migration, seed errors
- ``dbwarden.exceptions.plugin`` -- plugin registration and API errors
- ``dbwarden.exceptions.engine`` -- engine ordering and rollback contract errors
"""
from .core import (
    ConfigurationError,
    DBDisconnectedError,
    DBWardenConfigError,
    DBWardenError,
    DatabaseError,
    DirectoryNotFoundError,
    ImmutableChangeError,
    LockError,
    NoMigrationsError,
    NoSeedsError,
    PendingMigrationsError,
    SeedError,
    VersionNotFoundError,
)
from .engine import OrderingError, RollbackContractError
from .plugin import (
    HookConflictError,
    HookNotRegisteredError,
    ObjectHandlerConflictError,
    PluginApiMismatchError,
    PluginInstallError,
)

__all__ = [
    "ConfigurationError",
    "DBDisconnectedError",
    "DBWardenConfigError",
    "DBWardenError",
    "DatabaseError",
    "DirectoryNotFoundError",
    "HookConflictError",
    "HookNotRegisteredError",
    "ImmutableChangeError",
    "LockError",
    "NoMigrationsError",
    "NoSeedsError",
    "ObjectHandlerConflictError",
    "OrderingError",
    "PendingMigrationsError",
    "PluginApiMismatchError",
    "PluginInstallError",
    "RollbackContractError",
    "SeedError",
    "VersionNotFoundError",
]
