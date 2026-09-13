"""Migration lifecycle hook contexts and execution helpers.

Frozen dataclasses for the six lifecycle hooks. Contexts expose metadata
for observability and policy decisions but never a live connection,
transaction, secret URL, or raw SQL statement.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dbwarden.exceptions import HookVetoError
from dbwarden.logging import get_component_logger

logger = get_component_logger("hooks")

Command = Literal["migrate", "rollback", "downgrade"]
MigrationKind = Literal["versioned", "runs_always", "runs_on_change"]


@dataclass(frozen=True)
class MigrationPlanEntry:
    """A single migration in the pending batch."""

    version: str | None
    description: str
    file_path: Path
    checksum: str
    kind: MigrationKind
    statement_count: int


@dataclass(frozen=True)
class MigrationHookContext:
    """Base context for all migration lifecycle hooks."""

    database: str
    command: Command
    dry_run: bool
    dev: bool


@dataclass(frozen=True)
class PreMigrationRunContext(MigrationHookContext):
    """Context for pre_migration_run hook."""

    pending_migrations: tuple[MigrationPlanEntry, ...]


@dataclass(frozen=True)
class PreMigrationContext(MigrationHookContext):
    """Context for pre_migration hook."""

    migration: MigrationPlanEntry


@dataclass(frozen=True)
class MigrationProgressContext(MigrationHookContext):
    """Context for migration_progress hook."""

    migration: MigrationPlanEntry
    statement_index: int
    total_statements: int
    progress_pct: float
    elapsed_ms: float


@dataclass(frozen=True)
class PostMigrationContext(MigrationHookContext):
    """Context for post_migration hook."""

    migration: MigrationPlanEntry
    duration_ms: float
    statements_executed: int


@dataclass(frozen=True)
class MigrationFailureContext(MigrationHookContext):
    """Context for on_migration_failure hook."""

    migration: MigrationPlanEntry | None
    error: Exception
    statements_executed_before_failure: int


@dataclass(frozen=True)
class PostMigrationRunContext(MigrationHookContext):
    """Context for post_migration_run hook."""

    applied_migrations: tuple[str, ...]
    total_duration_ms: float


def execute_veto_hook(
    hook_name: str,
    hooks: list[Callable[..., Any]],
    ctx: Any,
    *,
    command: str,
) -> None:
    """Execute a veto-capable hook (pre_migration_run or pre_migration).

    Raises HookVetoError if any hook raises an exception.
    """
    for fn in hooks:
        fn_name = getattr(fn, "__name__", repr(fn))
        logger.debug("Executing %s hook: %s", hook_name, fn_name)
        start = time.perf_counter()
        try:
            fn(ctx)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "%s hook %s vetoed in %.1fms: %s",
                hook_name,
                fn_name,
                elapsed_ms,
                exc,
            )
            raise HookVetoError(
                f"{hook_name} hook {fn_name} vetoed the {command} run: {exc}"
            ) from exc
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.debug(
            "%s hook %s completed in %.1fms",
            hook_name,
            fn_name,
            elapsed_ms,
        )


def execute_observer_hook(
    hook_name: str,
    hooks: list[Callable[..., Any]],
    ctx: Any,
) -> None:
    """Execute an observer hook (migration_progress, post_migration, etc.).

    Observer failures are logged as warnings and do not abort the run.
    """
    for fn in hooks:
        fn_name = getattr(fn, "__name__", repr(fn))
        logger.debug("Executing %s hook: %s", hook_name, fn_name)
        start = time.perf_counter()
        try:
            fn(ctx)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.warning(
                "%s hook %s raised %s after %.1fms: %s",
                hook_name,
                fn_name,
                type(exc).__name__,
                elapsed_ms,
                exc,
            )
            continue
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.debug(
            "%s hook %s completed in %.1fms",
            hook_name,
            fn_name,
            elapsed_ms,
        )


def execute_failure_hook(
    hook_name: str,
    hooks: list[Callable[..., Any]],
    ctx: Any,
    original_error: Exception,
) -> Exception:
    """Execute on_migration_failure hook. Preserves original error.

    Returns the original error. If the failure hook also raises,
    logs the secondary failure but still returns the original.
    """
    for fn in hooks:
        fn_name = getattr(fn, "__name__", repr(fn))
        logger.debug("Executing %s hook: %s", hook_name, fn_name)
        start = time.perf_counter()
        try:
            fn(ctx)
        except Exception as secondary:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.warning(
                "%s hook %s raised %s after %.1fms (preserving original error): %s",
                hook_name,
                fn_name,
                type(secondary).__name__,
                elapsed_ms,
                secondary,
            )
            continue
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.debug(
            "%s hook %s completed in %.1fms",
            hook_name,
            fn_name,
            elapsed_ms,
        )
    return original_error


from typing import Callable  # noqa: E402
