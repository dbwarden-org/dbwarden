"""Core dbwarden exceptions."""
from __future__ import annotations


class DBWardenError(Exception):
    """Base exception for all dbwarden errors."""

    pass


class DirectoryNotFoundError(DBWardenError):
    """Raised when the migrations directory is not found."""

    pass


class ConfigurationError(DBWardenError):
    """Raised when there is a configuration error."""

    pass


class DBWardenConfigError(ConfigurationError):
    """Raised when dbwarden-specific model/config metadata is invalid."""

    pass


class VersionNotFoundError(DBWardenError):
    """Raised when a migration version is not found."""

    pass


class PendingMigrationsError(DBWardenError):
    """Raised when there are pending migrations but the operation requires none."""

    pass


class LockError(DBWardenError):
    """Raised when there is a lock error during migration."""

    pass


class DatabaseError(DBWardenError):
    """Raised when there is a database error."""

    pass


class NoMigrationsError(DBWardenError):
    """Raised when no migrations are found."""

    pass


class SeedError(DBWardenError):
    """Raised when there is a seed-related error."""

    pass


class NoSeedsError(DBWardenError):
    """Raised when no seeds are found."""

    pass


class DBDisconnectedError(DatabaseError):
    """Raised when the database is unreachable after retries."""

    pass


class ImmutableChangeError(DBWardenError):
    """Raised when a schema change would modify an immutable option.

    Some ClickHouse table options (e.g. PARTITION BY, PRIMARY KEY) cannot be
    changed after table creation. This error refuses the change and explains
    the immutable constraint and available alternatives (recreate + data copy
    via ``data_op()``).
    """

    pass
