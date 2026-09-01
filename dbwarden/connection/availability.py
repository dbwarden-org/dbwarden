from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import make_url

from dbwarden.config import get_database, is_skip_disabled
from dbwarden.exceptions import DBDisconnectedError


@dataclass(frozen=True)
class DatabaseAvailability:
    database: str
    available: bool
    skipped: bool = False
    error_code: str | None = None
    message: str | None = None


@dataclass
class MultiDatabaseResult:
    succeeded: list[str] = field(default_factory=list)
    skipped: list[DatabaseAvailability] = field(default_factory=list)
    failed: list[DatabaseAvailability] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.failed:
            return "failed"
        if self.skipped:
            return "partial_success"
        return "success"

    @property
    def exit_code(self) -> int:
        return 3 if self.skipped and not self.failed else (1 if self.failed else 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": not self.failed and not self.skipped,
            "status": self.status,
            "succeeded": self.succeeded,
            "skipped": [_availability_dict(item) for item in self.skipped],
            "failed": [_availability_dict(item) for item in self.failed],
        }


def configured_databases() -> dict[str, Any]:
    from dbwarden.config import get_multi_db_config

    return get_multi_db_config().databases


def run_all_databases(operation, *, databases: dict[str, Any] | None = None) -> MultiDatabaseResult:
    """Run an operation for each database with consistent availability semantics."""
    result = MultiDatabaseResult()
    databases = databases if databases is not None else configured_databases()
    for database, config in databases.items():
        availability = probe_database(database, optional=True, config=config)
        if availability.skipped:
            result.skipped.append(availability)
            continue
        if not availability.available:
            result.failed.append(availability)
            continue
        try:
            operation(database)
        except Exception as exc:
            result.failed.append(
                DatabaseAvailability(
                    database=database,
                    available=False,
                    error_code="operation_failed",
                    message=str(exc),
                )
            )
        else:
            result.succeeded.append(database)
    return result


def _availability_dict(item: DatabaseAvailability) -> dict[str, Any]:
    return {
        "database": item.database,
        "reason": item.error_code,
        "message": item.message,
    }


def _safe_message(message: str) -> str:
    try:
        return make_url(message).render_as_string(hide_password=True)
    except Exception:
        return message


def probe_database(
    database: str,
    *,
    optional: bool = False,
    disable_skip: bool | None = None,
    config: Any | None = None,
) -> DatabaseAvailability:
    """Probe one configured database without changing low-level connection behavior."""
    from dbwarden.connection.connection import get_db_connection

    config = config or get_database(database)
    skip_disabled = is_skip_disabled() if disable_skip is None else disable_skip
    try:
        with get_db_connection(database):
            pass
    except DBDisconnectedError as exc:
        message = _safe_message(str(exc))
        if optional and config.skip_if_missing and not skip_disabled:
            return DatabaseAvailability(
                database=database,
                available=False,
                skipped=True,
                error_code="connection_failed",
                message=message,
            )
        return DatabaseAvailability(
            database=database,
            available=False,
            error_code="connection_failed",
            message=message,
        )
    return DatabaseAvailability(database=database, available=True)
