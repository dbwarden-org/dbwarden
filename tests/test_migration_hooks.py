"""Tests for migration lifecycle hooks."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dbwarden.commands.migrate.hooks import (
    MigrationPlanEntry,
    MigrationHookContext,
    PreMigrationRunContext,
    PreMigrationContext,
    MigrationProgressContext,
    PostMigrationContext,
    MigrationFailureContext,
    PostMigrationRunContext,
    execute_veto_hook,
    execute_observer_hook,
    execute_failure_hook,
)
from dbwarden.exceptions import HookVetoError


# --- Context dataclass tests ---


def test_migration_plan_entry_frozen():
    entry = MigrationPlanEntry(
        version="0001",
        description="test",
        file_path=Path("migrations/0001_test.sql"),
        checksum="abc123",
        kind="versioned",
        statement_count=3,
    )
    with pytest.raises(AttributeError):
        entry.version = "0002"


def test_migration_hook_context_base():
    ctx = MigrationHookContext(
        database="primary",
        command="migrate",
        dry_run=False,
        dev=False,
    )
    assert ctx.database == "primary"
    assert ctx.command == "migrate"
    assert ctx.dry_run is False
    assert ctx.dev is False


def test_pre_migration_run_context():
    entry = MigrationPlanEntry(
        version="0001",
        description="test",
        file_path=Path("migrations/0001_test.sql"),
        checksum="abc123",
        kind="versioned",
        statement_count=3,
    )
    ctx = PreMigrationRunContext(
        database="primary",
        command="migrate",
        dry_run=False,
        dev=False,
        pending_migrations=(entry,),
    )
    assert len(ctx.pending_migrations) == 1
    assert ctx.pending_migrations[0].version == "0001"


def test_pre_migration_context():
    entry = MigrationPlanEntry(
        version="0001",
        description="test",
        file_path=Path("migrations/0001_test.sql"),
        checksum="abc123",
        kind="versioned",
        statement_count=3,
    )
    ctx = PreMigrationContext(
        database="primary",
        command="migrate",
        dry_run=False,
        dev=False,
        migration=entry,
    )
    assert ctx.migration.version == "0001"


def test_migration_progress_context():
    entry = MigrationPlanEntry(
        version="0001",
        description="test",
        file_path=Path("migrations/0001_test.sql"),
        checksum="abc123",
        kind="versioned",
        statement_count=3,
    )
    ctx = MigrationProgressContext(
        database="primary",
        command="migrate",
        dry_run=False,
        dev=False,
        migration=entry,
        statement_index=2,
        total_statements=3,
        progress_pct=66.7,
        elapsed_ms=150.0,
    )
    assert ctx.statement_index == 2
    assert ctx.progress_pct == 66.7


def test_post_migration_context():
    entry = MigrationPlanEntry(
        version="0001",
        description="test",
        file_path=Path("migrations/0001_test.sql"),
        checksum="abc123",
        kind="versioned",
        statement_count=3,
    )
    ctx = PostMigrationContext(
        database="primary",
        command="migrate",
        dry_run=False,
        dev=False,
        migration=entry,
        duration_ms=250.0,
        statements_executed=3,
    )
    assert ctx.duration_ms == 250.0
    assert ctx.statements_executed == 3


def test_migration_failure_context():
    entry = MigrationPlanEntry(
        version="0001",
        description="test",
        file_path=Path("migrations/0001_test.sql"),
        checksum="abc123",
        kind="versioned",
        statement_count=3,
    )
    ctx = MigrationFailureContext(
        database="primary",
        command="migrate",
        dry_run=False,
        dev=False,
        migration=entry,
        error=ValueError("test error"),
        statements_executed_before_failure=1,
    )
    assert ctx.error.args[0] == "test error"
    assert ctx.statements_executed_before_failure == 1


def test_migration_failure_context_no_migration():
    ctx = MigrationFailureContext(
        database="primary",
        command="migrate",
        dry_run=False,
        dev=False,
        migration=None,
        error=ValueError("test error"),
        statements_executed_before_failure=0,
    )
    assert ctx.migration is None


def test_post_migration_run_context():
    ctx = PostMigrationRunContext(
        database="primary",
        command="migrate",
        dry_run=False,
        dev=False,
        applied_migrations=("0001", "0002"),
        total_duration_ms=500.0,
    )
    assert ctx.applied_migrations == ("0001", "0002")
    assert ctx.total_duration_ms == 500.0


# --- Hook execution tests ---


def test_execute_veto_hook_calls_all():
    hook1 = MagicMock()
    hook2 = MagicMock()
    ctx = MagicMock()
    execute_veto_hook("pre_migration_run", [hook1, hook2], ctx, command="migrate")
    hook1.assert_called_once_with(ctx)
    hook2.assert_called_once_with(ctx)


def test_execute_veto_hook_raises_on_exception():
    def failing_hook(ctx):
        raise RuntimeError("veto!")

    ctx = MagicMock()
    with pytest.raises(HookVetoError, match="veto!"):
        execute_veto_hook("pre_migration", [failing_hook], ctx, command="migrate")


def test_execute_veto_hook_stops_on_first_failure():
    hook1 = MagicMock(side_effect=RuntimeError("veto!"))
    hook2 = MagicMock()
    ctx = MagicMock()
    with pytest.raises(HookVetoError):
        execute_veto_hook("pre_migration", [hook1, hook2], ctx, command="migrate")
    hook2.assert_not_called()


def test_execute_observer_hook_calls_all():
    hook1 = MagicMock()
    hook2 = MagicMock()
    ctx = MagicMock()
    execute_observer_hook("post_migration", [hook1, hook2], ctx)
    hook1.assert_called_once_with(ctx)
    hook2.assert_called_once_with(ctx)


def test_execute_observer_hook_continues_on_failure():
    def failing_hook(ctx):
        raise RuntimeError("observer error!")

    hook2 = MagicMock()
    ctx = MagicMock()
    execute_observer_hook("post_migration", [failing_hook, hook2], ctx)
    hook2.assert_called_once_with(ctx)


def test_execute_failure_hook_preserves_original_error():
    def failing_hook(ctx):
        raise RuntimeError("secondary error!")

    original_error = ValueError("original error")
    ctx = MagicMock()
    result = execute_failure_hook(
        "on_migration_failure", [failing_hook], ctx, original_error
    )
    assert result is original_error


def test_execute_failure_hook_returns_original_on_success():
    hook = MagicMock()
    original_error = ValueError("original error")
    ctx = MagicMock()
    result = execute_failure_hook(
        "on_migration_failure", [hook], ctx, original_error
    )
    assert result is original_error
    hook.assert_called_once_with(ctx)


# --- register_migration_hooks tests ---


def test_register_migration_hooks_validates_callable():
    from dbwarden import register_migration_hooks
    from dbwarden.exceptions import HookValidationError

    with pytest.raises(HookValidationError, match="callable"):
        register_migration_hooks(pre_migration_run=["not a callable"])


def test_register_migration_hooks_validates_known_hook():
    from dbwarden.plugin import HookRegistry, KNOWN_VALUE_HOOKS
    from dbwarden.exceptions import HookValidationError

    # The function only accepts known hook names, so an unknown name
    # should raise a TypeError (unexpected keyword argument)
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        from dbwarden import register_migration_hooks
        register_migration_hooks(unknown_hook=[lambda ctx: None])


def test_register_migration_hooks_registers_global():
    from dbwarden import register_migration_hooks
    from dbwarden.plugin import HookRegistry

    called = []
    def my_hook(ctx):
        called.append(ctx)

    register_migration_hooks(post_migration=[my_hook])
    assert HookRegistry.is_registered("post_migration")
    assert any(fn is my_hook for _, fn in HookRegistry._hooks.get("post_migration", []))


def test_register_migration_hooks_multiple_hooks():
    from dbwarden import register_migration_hooks
    from dbwarden.plugin import HookRegistry

    hook1 = MagicMock()
    hook2 = MagicMock()
    register_migration_hooks(
        pre_migration_run=[hook1],
        post_migration=[hook2],
    )
    assert any(fn is hook1 for _, fn in HookRegistry._hooks.get("pre_migration_run", []))
    assert any(fn is hook2 for _, fn in HookRegistry._hooks.get("post_migration", []))


# --- Plugin hook name tests ---


def test_lifecycle_hooks_in_known_value_hooks():
    from dbwarden.plugin import KNOWN_VALUE_HOOKS, LIFECYCLE_HOOK_NAMES

    for name in LIFECYCLE_HOOK_NAMES:
        assert name in KNOWN_VALUE_HOOKS


def test_lifecycle_hooks_in_multi_value_hooks():
    from dbwarden.plugin import MULTI_VALUE_HOOKS, LIFECYCLE_HOOK_NAMES

    for name in LIFECYCLE_HOOK_NAMES:
        assert name in MULTI_VALUE_HOOKS


def test_hook_call_specs_for_lifecycle_hooks():
    from dbwarden.plugin import HOOK_CALL_SPECS, LIFECYCLE_HOOK_NAMES

    for name in LIFECYCLE_HOOK_NAMES:
        assert name in HOOK_CALL_SPECS
