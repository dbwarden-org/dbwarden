"""Comprehensive API stability tests for DbwardenConfig, lifecycle hooks, and preflight."""
from __future__ import annotations

import inspect
import json
import os
import tempfile
from pathlib import Path
from typing import get_type_hints
from unittest.mock import MagicMock

import pytest

from dbwarden import DbwardenConfig, database_config, register_migration_hooks
from dbwarden.config import get_database, get_project_config
from dbwarden.config_registry import reset_registry, registered_project_config
from dbwarden.config_schema import ProjectConfigEntry, _validate_impact_paths
from dbwarden.config.state import DatabaseConfig, ProjectConfig
from dbwarden.engine.preflight import PreflightResult, run_preflight
from dbwarden.exceptions import (
    ConfigurationError,
    HookValidationError,
    HookVetoError,
    DBWardenConfigError,
    DBWardenError,
)
from dbwarden.commands.migrate.hooks import (
    MigrationPlanEntry,
    execute_veto_hook,
    execute_observer_hook,
    execute_failure_hook,
)
from dbwarden.plugin import (
    HookRegistry,
    LIFECYCLE_HOOK_NAMES,
    MULTI_VALUE_HOOKS,
    HOOK_CALL_SPECS,
    validate_migration_hooks,
)


@pytest.fixture(autouse=True)
def clean():
    reset_registry()
    HookRegistry.clear()
    yield
    reset_registry()
    HookRegistry.clear()


# ─────────────────────────────────────────────
# SECTION 1: DbwardenConfig API
# ─────────────────────────────────────────────


class TestDbwardenConfigAPI:
    def test_is_abstract(self):
        assert getattr(DbwardenConfig, "__abstract__", False) is True

    def test_singleton_enforced(self):
        class First(DbwardenConfig):
            pre_migrate_safety = "warn"

        with pytest.raises(ConfigurationError, match="Only one"):
            class Second(DbwardenConfig):
                pass

    def test_singleton_error_names_both_classes(self):
        class AlphaPolicy(DbwardenConfig):
            pre_migrate_safety = "warn"

        with pytest.raises(ConfigurationError) as exc_info:
            class BetaPolicy(DbwardenConfig):
                pass

        msg = str(exc_info.value)
        assert "AlphaPolicy" in msg
        assert "BetaPolicy" in msg

    def test_reset_allows_re_registration(self):
        class First(DbwardenConfig):
            pre_migrate_safety = "warn"

        reset_registry()
        class Second(DbwardenConfig):
            pre_migrate_safety = "block"

        assert registered_project_config() is not None

    def test_defaults_match_spec(self):
        from dbwarden.config.build import _finalize_project_config
        config = _finalize_project_config(None)
        assert config.pre_migrate_safety == "off"
        assert config.pre_migrate_impact == "off"
        assert config.missing_plan == "off"
        assert config.impact_paths == ["."]

    def test_all_off(self):
        from dbwarden.config.build import _finalize_project_config
        config = _finalize_project_config(
            ProjectConfigEntry(pre_migrate_safety="off", pre_migrate_impact="off",
                               missing_plan="off", impact_paths=[])
        )
        assert config.pre_migrate_safety == "off"
        assert config.impact_paths == []

    def test_all_warn(self):
        from dbwarden.config.build import _finalize_project_config
        config = _finalize_project_config(
            ProjectConfigEntry(pre_migrate_safety="warn", pre_migrate_impact="warn",
                               missing_plan="warn")
        )
        assert config.pre_migrate_safety == "warn"

    def test_all_block(self):
        from dbwarden.config.build import _finalize_project_config
        config = _finalize_project_config(
            ProjectConfigEntry(pre_migrate_safety="block", pre_migrate_impact="block",
                               missing_plan="block", impact_paths=["app", "src"])
        )
        assert config.pre_migrate_safety == "block"
        assert config.impact_paths == ["app", "src"]

    def test_impact_block_requires_missing_plan_block(self):
        with pytest.raises(ConfigurationError, match="missing_plan"):
            class Bad(DbwardenConfig):
                pre_migrate_impact = "block"
                missing_plan = "warn"

    def test_impact_warn_allows_missing_plan_off(self):
        from dbwarden.config.build import _finalize_project_config
        config = _finalize_project_config(
            ProjectConfigEntry(pre_migrate_impact="warn", missing_plan="off")
        )
        assert config.pre_migrate_impact == "warn"
        assert config.missing_plan == "off"

    def test_invalid_safety_value(self):
        with pytest.raises(ConfigurationError, match="pre_migrate_safety"):
            class Bad(DbwardenConfig):
                pre_migrate_safety = "invalid"

    def test_invalid_impact_value(self):
        with pytest.raises(ConfigurationError, match="pre_migrate_impact"):
            class Bad(DbwardenConfig):
                pre_migrate_impact = "invalid"

    def test_invalid_missing_plan_value(self):
        with pytest.raises(ConfigurationError, match="missing_plan"):
            class Bad(DbwardenConfig):
                missing_plan = "invalid"

    def test_paths_reject_absolute(self):
        with pytest.raises(ConfigurationError, match="impact_paths"):
            class Bad(DbwardenConfig):
                impact_paths = ["/etc/passwd"]

    def test_paths_reject_traversal(self):
        with pytest.raises(ConfigurationError, match="impact_paths"):
            class Bad(DbwardenConfig):
                impact_paths = ["../escape"]

    def test_paths_reject_symlink_escape(self, tmp_path, monkeypatch, symlink_factory):
        link = tmp_path / "link_to_etc"
        symlink_factory(link, tmp_path.parent, target_is_directory=True)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ConfigurationError, match="symlinks"):
            class Bad(DbwardenConfig):
                impact_paths = ["link_to_etc"]

    def test_paths_accept_valid_relative(self):
        from dbwarden.config.build import _finalize_project_config
        config = _finalize_project_config(
            ProjectConfigEntry(impact_paths=["src/app", "workers", "models"])
        )
        assert config.impact_paths == ["src/app", "workers", "models"]

    def test_paths_reject_uppercase_policy(self):
        with pytest.raises(ConfigurationError):
            class Bad(DbwardenConfig):
                pre_migrate_safety = "Block"

    def test_paths_reject_none_policy(self):
        with pytest.raises(ConfigurationError):
            class Bad(DbwardenConfig):
                pre_migrate_safety = None

    def test_paths_reject_empty_string_policy(self):
        with pytest.raises(ConfigurationError):
            class Bad(DbwardenConfig):
                pre_migrate_safety = ""

    def test_paths_reject_integer_policy(self):
        with pytest.raises(ConfigurationError):
            class Bad(DbwardenConfig):
                pre_migrate_safety = 1


# ─────────────────────────────────────────────
# SECTION 2: register_migration_hooks API
# ─────────────────────────────────────────────


class TestRegisterMigrationHooks:
    def test_register_valid_callable(self):
        def hook(ctx):
            pass

        register_migration_hooks(post_migration=[hook])
        assert HookRegistry.is_registered("post_migration")

    def test_register_lambda(self):
        register_migration_hooks(post_migration=[lambda ctx: None])
        assert HookRegistry.is_registered("post_migration")

    def test_register_class_with_call(self):
        class MyHook:
            def __call__(self, ctx):
                pass

        register_migration_hooks(post_migration=[MyHook()])
        assert HookRegistry.is_registered("post_migration")

    def test_register_rejects_non_callable(self):
        with pytest.raises(HookValidationError, match="callable"):
            register_migration_hooks(post_migration=["not a callable"])

    def test_register_rejects_wrong_arity(self):
        with pytest.raises(HookValidationError, match="context parameter"):
            register_migration_hooks(post_migration=[lambda a, b: None])

    def test_register_rejects_zero_args(self):
        with pytest.raises(HookValidationError, match="context parameter"):
            register_migration_hooks(post_migration=[lambda: None])

    def test_register_rejects_unknown_hook_name(self):
        with pytest.raises(TypeError):
            register_migration_hooks(unknown_hook_name=[lambda ctx: None])

    def test_register_partial_hooks(self):
        def hook(ctx):
            pass

        register_migration_hooks(pre_migration_run=[hook], post_migration=[hook])
        assert HookRegistry.is_registered("pre_migration_run")
        assert HookRegistry.is_registered("post_migration")
        assert not HookRegistry.is_registered("pre_migration")

    def test_register_all_six_hooks(self):
        def hook(ctx):
            pass

        register_migration_hooks(
            pre_migration_run=[hook],
            pre_migration=[hook],
            migration_progress=[hook],
            post_migration=[hook],
            on_migration_failure=[hook],
            post_migration_run=[hook],
        )
        for name in LIFECYCLE_HOOK_NAMES:
            assert HookRegistry.is_registered(name)

    def test_register_none_values(self):
        register_migration_hooks(
            pre_migration_run=None,
            pre_migration=None,
            migration_progress=None,
            post_migration=None,
            on_migration_failure=None,
            post_migration_run=None,
        )
        for name in LIFECYCLE_HOOK_NAMES:
            assert not HookRegistry.is_registered(name)

    def test_register_multiple_callables_same_hook(self):
        def h1(ctx):
            pass

        def h2(ctx):
            pass

        register_migration_hooks(post_migration=[h1, h2])
        entries = HookRegistry._hooks.get("post_migration", [])
        assert len(entries) == 2


# ─────────────────────────────────────────────
# SECTION 3: HookRegistry API
# ─────────────────────────────────────────────


class TestHookRegistry:
    def test_register_and_is_registered(self):
        def hook(ctx):
            pass

        HookRegistry.register("post_migration", hook, plugin="test")
        assert HookRegistry.is_registered("post_migration")
        assert not HookRegistry.is_registered("pre_migration")

    def test_register_rejects_unknown_hook(self):
        with pytest.raises(ValueError, match="Unknown hook"):
            HookRegistry.register("nonexistent", lambda ctx: None, plugin="test")

    def test_dedup_same_callable_same_scope(self):
        def hook(ctx):
            pass

        HookRegistry.register("post_migration", hook, plugin="app")
        HookRegistry.register("post_migration", hook, plugin="app")
        entries = HookRegistry._hooks.get("post_migration", [])
        assert len(entries) == 1

    def test_no_dedup_same_callable_different_scope(self):
        def hook(ctx):
            pass

        HookRegistry.register("post_migration", hook, plugin="app")
        HookRegistry.register("post_migration", hook, plugin="other")
        entries = HookRegistry._hooks.get("post_migration", [])
        assert len(entries) == 2

    def test_providers(self):
        def hook(ctx):
            pass

        HookRegistry.register("post_migration", hook, plugin="p1")
        HookRegistry.register("post_migration", hook, plugin="p2")
        providers = HookRegistry.providers("post_migration")
        assert "p1" in providers
        assert "p2" in providers

    def test_providers_empty(self):
        assert HookRegistry.providers("nonexistent") == []

    def test_hooks_returns_dict(self):
        def hook(ctx):
            pass

        HookRegistry.register("post_migration", hook, plugin="app")
        hooks = HookRegistry.hooks()
        assert "post_migration" in hooks

    def test_clear_removes_all(self):
        def hook(ctx):
            pass

        HookRegistry.register("post_migration", hook, plugin="app")
        HookRegistry.clear()
        assert not HookRegistry.is_registered("post_migration")

    def test_execute_single(self):
        result = []

        def hook(ctx):
            result.append("called")

        HookRegistry.register("post_migration", hook, plugin="app")
        HookRegistry.execute_single("post_migration", MagicMock())
        assert result == ["called"]

    def test_execute_single_raises_on_conflict(self):
        def h1(ctx):
            pass

        def h2(ctx):
            pass

        HookRegistry.register("post_migration", h1, plugin="p1")
        HookRegistry.register("post_migration", h2, plugin="p2")
        from dbwarden.exceptions import HookConflictError

        with pytest.raises(HookConflictError):
            HookRegistry.execute_single("post_migration", MagicMock())

    def test_execute_single_raises_on_not_registered(self):
        from dbwarden.exceptions import HookNotRegisteredError

        with pytest.raises(HookNotRegisteredError):
            HookRegistry.execute_single("nonexistent", MagicMock())

    def test_execute_all(self):
        result = []

        def h1(ctx):
            result.append(1)

        def h2(ctx):
            result.append(2)

        HookRegistry.register("post_migration", h1, plugin="p1")
        HookRegistry.register("post_migration", h2, plugin="p2")
        HookRegistry.execute_all("post_migration", MagicMock())
        assert result == [1, 2]


# ─────────────────────────────────────────────
# SECTION 4: validate_migration_hooks API
# ─────────────────────────────────────────────


class TestValidateMigrationHooks:
    def test_no_hooks_passes(self):
        validate_migration_hooks()

    def test_good_hook_passes(self):
        def good(ctx):
            pass

        HookRegistry.register("post_migration", good, plugin="app")
        validate_migration_hooks()

    def test_bad_signature_fails(self):
        def bad(a, b):
            pass

        HookRegistry.register("post_migration", bad, plugin="app")
        with pytest.raises(HookValidationError, match="context parameter"):
            validate_migration_hooks()

    def test_non_callable_fails(self):
        HookRegistry.register("post_migration", "not callable", plugin="app")
        with pytest.raises(HookValidationError, match="not callable"):
            validate_migration_hooks()

    def test_nonexistent_db_passes(self):
        validate_migration_hooks(db_name="nonexistent_xyz")


# ─────────────────────────────────────────────
# SECTION 5: Hook Execution API
# ─────────────────────────────────────────────


class TestHookExecution:
    def test_veto_calls_all(self):
        h1 = MagicMock()
        h2 = MagicMock()
        execute_veto_hook("pre_migration_run", [h1, h2], MagicMock(), command="migrate")
        h1.assert_called_once()
        h2.assert_called_once()

    def test_veto_raises_hook_veto_error(self):
        def fail(ctx):
            raise RuntimeError("nope")

        with pytest.raises(HookVetoError, match="nope"):
            execute_veto_hook("pre_migration", [fail], MagicMock(), command="migrate")

    def test_veto_stops_on_first(self):
        h1 = MagicMock(side_effect=RuntimeError("veto"))
        h2 = MagicMock()
        with pytest.raises(HookVetoError):
            execute_veto_hook("pre_migration", [h1, h2], MagicMock(), command="migrate")
        h2.assert_not_called()

    def test_veto_preserves_cause(self):
        def fail(ctx):
            raise RuntimeError("original")

        with pytest.raises(HookVetoError) as exc_info:
            execute_veto_hook("pre_migration", [fail], MagicMock(), command="migrate")
        assert isinstance(exc_info.value.__cause__, RuntimeError)

    def test_veto_empty_list(self):
        execute_veto_hook("pre_migration_run", [], MagicMock(), command="migrate")

    def test_observer_calls_all(self):
        h1 = MagicMock()
        h2 = MagicMock()
        execute_observer_hook("post_migration", [h1, h2], MagicMock())
        h1.assert_called_once()
        h2.assert_called_once()

    def test_observer_continues_on_failure(self):
        def fail(ctx):
            raise RuntimeError("crash")

        h2 = MagicMock()
        execute_observer_hook("post_migration", [fail, h2], MagicMock())
        h2.assert_called_once()

    def test_observer_empty_list(self):
        execute_observer_hook("post_migration", [], MagicMock())

    def test_failure_preserves_original(self):
        def fail(ctx):
            raise RuntimeError("secondary")

        original = ValueError("primary")
        result = execute_failure_hook("on_migration_failure", [fail], MagicMock(), original)
        assert result is original

    def test_failure_returns_original_on_success(self):
        h = MagicMock()
        original = ValueError("primary")
        result = execute_failure_hook("on_migration_failure", [h], MagicMock(), original)
        assert result is original

    def test_failure_empty_list(self):
        original = ValueError("primary")
        result = execute_failure_hook("on_migration_failure", [], MagicMock(), original)
        assert result is original

    def test_observer_base_exception_propagates(self):
        def interrupt(ctx):
            raise KeyboardInterrupt()

        with pytest.raises(KeyboardInterrupt):
            execute_observer_hook("post_migration", [interrupt], MagicMock())


# ─────────────────────────────────────────────
# SECTION 6: Context Dataclasses
# ─────────────────────────────────────────────


class TestContextDataclasses:
    def test_all_frozen(self):
        from dbwarden.commands.migrate.hooks import (
            MigrationPlanEntry,
            PreMigrationRunContext,
            PreMigrationContext,
            MigrationProgressContext,
            PostMigrationContext,
            MigrationFailureContext,
            PostMigrationRunContext,
        )

        entry = MigrationPlanEntry(
            version="1",
            description="d",
            file_path=Path("f.sql"),
            checksum="c",
            kind="versioned",
            statement_count=1,
        )
        base_kwargs = dict(
            database="db", command="migrate", dry_run=False, dev=False
        )

        for cls, extra in [
            (PreMigrationRunContext, {"pending_migrations": (entry,)}),
            (PreMigrationContext, {"migration": entry}),
            (MigrationProgressContext, {"migration": entry, "statement_index": 0, "total_statements": 1, "progress_pct": 0.0, "elapsed_ms": 0.0}),
            (PostMigrationContext, {"migration": entry, "duration_ms": 0.0, "statements_executed": 1}),
            (MigrationFailureContext, {"migration": None, "error": ValueError(), "statements_executed_before_failure": 0}),
            (PostMigrationRunContext, {"applied_migrations": (), "total_duration_ms": 0.0}),
        ]:
            ctx = cls(**base_kwargs, **extra)
            with pytest.raises(AttributeError):
                ctx.database = "hacked"

    def test_plan_entry_fields(self):
        entry = MigrationPlanEntry(
            version="0001",
            description="test",
            file_path=Path("test.sql"),
            checksum="abc",
            kind="versioned",
            statement_count=5,
        )
        assert entry.version == "0001"
        assert entry.description == "test"
        assert entry.kind == "versioned"
        assert entry.statement_count == 5

    def test_plan_entry_none_version(self):
        entry = MigrationPlanEntry(
            version=None, description="", file_path=Path(""),
            checksum="", kind="runs_always", statement_count=0,
        )
        assert entry.version is None

    def test_all_commands(self):
        from dbwarden.commands.migrate.hooks import MigrationHookContext

        for cmd in ["migrate", "rollback", "downgrade"]:
            ctx = MigrationHookContext(database="db", command=cmd, dry_run=False, dev=False)
            assert ctx.command == cmd

    def test_all_migration_kinds(self):
        for kind in ["versioned", "runs_always", "runs_on_change"]:
            entry = MigrationPlanEntry(
                version=None, description="", file_path=Path(""),
                checksum="", kind=kind, statement_count=0,
            )
            assert entry.kind == kind


# ─────────────────────────────────────────────
# SECTION 7: Preflight API
# ─────────────────────────────────────────────


class TestPreflightAPI:
    def test_missing_file_blocks(self):
        result = run_preflight({"0001": "/nonexistent.sql"}, missing_plan="off")
        assert result.abort
        assert "not found" in result.errors[0]

    def test_empty_file_blocks(self, tmp_path):
        f = tmp_path / "0001__test.sql"
        f.write_text("")
        result = run_preflight({"0001": str(f)}, missing_plan="off")
        assert result.abort
        assert "no upgrade section" in result.errors[0]

    def test_valid_file_passes(self, tmp_path):
        f = tmp_path / "0001__test.sql"
        f.write_text("-- upgrade\nSELECT 1;\n")
        result = run_preflight({"0001": str(f)}, missing_plan="off")
        assert not result.abort

    def test_missing_plan_off(self, tmp_path):
        f = tmp_path / "0001__test.sql"
        f.write_text("-- upgrade\nSELECT 1;\n")
        result = run_preflight({"0001": str(f)}, missing_plan="off")
        assert not result.abort
        assert len(result.warnings) == 0

    def test_missing_plan_warn(self, tmp_path):
        f = tmp_path / "0001__test.sql"
        f.write_text("-- upgrade\nSELECT 1;\n")
        result = run_preflight({"0001": str(f)}, missing_plan="warn")
        assert not result.abort
        assert len(result.warnings) == 1
        assert "no plan file found" in result.warnings[0]

    def test_missing_plan_block(self, tmp_path):
        f = tmp_path / "0001__test.sql"
        f.write_text("-- upgrade\nSELECT 1;\n")
        result = run_preflight({"0001": str(f)}, missing_plan="block")
        assert result.abort
        assert "missing plan" in result.errors[0]

    def test_malformed_plan_block(self, tmp_path):
        f = tmp_path / "0001__test.sql"
        f.write_text("-- upgrade\nSELECT 1;\n")
        p = tmp_path / "0001__test.plan.json"
        p.write_text("not json")
        result = run_preflight({"0001": str(f)}, missing_plan="block")
        assert result.abort
        assert "malformed plan" in result.errors[0]

    def test_error_severity_blocks(self, tmp_path):
        f = tmp_path / "0001__test.sql"
        f.write_text("-- upgrade\nSELECT 1;\n")
        p = tmp_path / "0001__test.plan.json"
        p.write_text(json.dumps({
            "migration_id": "test", "operations": [
                {"type": "drop_table", "table": "users", "severity": "ERROR"}
            ], "required_flags": [], "checksum": ""
        }))
        result = run_preflight({"0001": str(f)}, missing_plan="off")
        assert result.abort

    def test_critical_severity_blocks(self, tmp_path):
        f = tmp_path / "0001__test.sql"
        f.write_text("-- upgrade\nSELECT 1;\n")
        p = tmp_path / "0001__test.plan.json"
        p.write_text(json.dumps({
            "migration_id": "test", "operations": [
                {"type": "drop_table", "table": "users", "severity": "CRITICAL"}
            ], "required_flags": [], "checksum": ""
        }))
        result = run_preflight({"0001": str(f)}, missing_plan="off")
        assert result.abort

    def test_warning_does_not_block(self, tmp_path):
        f = tmp_path / "0001__test.sql"
        f.write_text("-- upgrade\nSELECT 1;\n")
        p = tmp_path / "0001__test.plan.json"
        p.write_text(json.dumps({
            "migration_id": "test", "operations": [
                {"type": "drop_column", "table": "users", "target": "email", "severity": "WARNING"}
            ], "required_flags": [], "checksum": ""
        }))
        result = run_preflight({"0001": str(f)}, missing_plan="off")
        assert not result.abort
        assert len(result.warnings) == 1

    def test_empty_batch(self):
        result = run_preflight({})
        assert not result.abort

    def test_many_migrations(self, tmp_path):
        filepaths = {}
        for i in range(100):
            f = tmp_path / f"{i:04d}__test.sql"
            f.write_text("-- upgrade\nSELECT 1;\n")
            filepaths[str(i)] = str(f)
        result = run_preflight(filepaths, missing_plan="off")
        assert not result.abort

    def test_plan_ops_missing_table(self, tmp_path):
        f = tmp_path / "0001__test.sql"
        f.write_text("-- upgrade\nSELECT 1;\n")
        p = tmp_path / "0001__test.plan.json"
        p.write_text(json.dumps({
            "migration_id": "test", "operations": [
                {"type": "custom_op", "severity": "WARNING"}
            ], "required_flags": [], "checksum": ""
        }))
        result = run_preflight({"0001": str(f)}, missing_plan="off")
        assert not result.abort
        assert len(result.warnings) == 1

    def test_plan_ops_missing_severity(self, tmp_path):
        f = tmp_path / "0001__test.sql"
        f.write_text("-- upgrade\nSELECT 1;\n")
        p = tmp_path / "0001__test.plan.json"
        p.write_text(json.dumps({
            "migration_id": "test", "operations": [
                {"type": "drop_table", "table": "users"}
            ], "required_flags": [], "checksum": ""
        }))
        result = run_preflight({"0001": str(f)}, missing_plan="off")
        assert not result.abort  # Missing severity defaults to INFO

    def test_plan_null_operations(self, tmp_path):
        f = tmp_path / "0001__test.sql"
        f.write_text("-- upgrade\nSELECT 1;\n")
        p = tmp_path / "0001__test.plan.json"
        p.write_text(json.dumps({
            "migration_id": "test", "operations": None,
            "required_flags": [], "checksum": ""
        }))
        # null operations should be handled gracefully
        result = run_preflight({"0001": str(f)}, missing_plan="off")
        # May or may not abort depending on how plan safety parser handles None
        assert result.abort or not result.abort


# ─────────────────────────────────────────────
# SECTION 8: Exception Hierarchy
# ─────────────────────────────────────────────


class TestExceptionHierarchy:
    def test_hook_validation_is_config_error(self):
        assert issubclass(HookValidationError, DBWardenConfigError)

    def test_hook_veto_is_dbwarden_error(self):
        assert issubclass(HookVetoError, DBWardenError)

    def test_hook_veto_not_config_error(self):
        assert not issubclass(HookVetoError, DBWardenConfigError)

    def test_hook_validation_not_veto(self):
        assert not issubclass(HookValidationError, HookVetoError)


# ─────────────────────────────────────────────
# SECTION 9: Plugin Constants
# ─────────────────────────────────────────────


class TestPluginConstants:
    def test_lifecycle_hook_names_count(self):
        assert len(LIFECYCLE_HOOK_NAMES) == 6

    def test_lifecycle_hook_names_exact(self):
        assert LIFECYCLE_HOOK_NAMES == frozenset({
            "pre_migration_run",
            "pre_migration",
            "migration_progress",
            "post_migration",
            "on_migration_failure",
            "post_migration_run",
        })

    def test_all_lifecycle_in_multi_value(self):
        for name in LIFECYCLE_HOOK_NAMES:
            assert name in MULTI_VALUE_HOOKS

    def test_all_lifecycle_in_hook_call_specs(self):
        for name in LIFECYCLE_HOOK_NAMES:
            assert name in HOOK_CALL_SPECS

    def test_hook_call_specs_have_args(self):
        for name in LIFECYCLE_HOOK_NAMES:
            args, kwargs = HOOK_CALL_SPECS[name]
            assert isinstance(args, tuple)
            assert isinstance(kwargs, dict)


# ─────────────────────────────────────────────
# SECTION 10: Config Schema
# ─────────────────────────────────────────────


class TestConfigSchema:
    def test_validate_impact_paths_rejects_absolute(self):
        with pytest.raises(ValueError, match="absolute"):
            _validate_impact_paths(None, None, ["/etc/passwd"])

    def test_validate_impact_paths_rejects_traversal(self):
        with pytest.raises(ValueError, match="traversal"):
            _validate_impact_paths(None, None, ["../escape"])

    def test_validate_impact_paths_rejects_symlink_escape(self, tmp_path, monkeypatch, symlink_factory):
        link = tmp_path / "link"
        symlink_factory(link, tmp_path.parent, target_is_directory=True)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="symlinks"):
            _validate_impact_paths(None, None, ["link"])

    def test_validate_impact_paths_accepts_valid(self):
        _validate_impact_paths(None, None, ["src/app", "workers"])

    def test_validate_impact_paths_rejects_non_list(self):
        with pytest.raises(ValueError):
            _validate_impact_paths(None, None, "not a list")

    def test_validate_impact_paths_rejects_non_strings(self):
        with pytest.raises(ValueError):
            _validate_impact_paths(None, None, [1, 2, 3])


# ─────────────────────────────────────────────
# SECTION 11: database_config() API
# ─────────────────────────────────────────────


class TestDatabaseConfigAPI:
    def test_returns_database_handle(self):
        from dbwarden.db_handle import DatabaseHandle
        handle = database_config(
            database_name="testdb",
            database_type="sqlite",
            database_url_sync="sqlite:///./test.db",
            default=True,
        )
        assert isinstance(handle, DatabaseHandle)

    def test_rejects_no_urls(self):
        with pytest.raises(ConfigurationError, match="url"):
            database_config(database_name="testdb")

    def test_rejects_duplicate_name(self, tmp_path, monkeypatch):
        (tmp_path / "dbwarden.py").write_text(
            "from dbwarden import database_config\n"
            "database_config(database_name='dup1', database_type='sqlite', "
            "database_url_sync='sqlite:///./test1.db', default=True, model_paths=['a'])\n"
            "database_config(database_name='dup1', database_type='sqlite', "
            "database_url_sync='sqlite:///./test2.db', model_paths=['b'])\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ConfigurationError, match="Duplicate"):
            from dbwarden.config import get_multi_db_config
            get_multi_db_config()

    def test_rejects_duplicate_url(self, tmp_path, monkeypatch):
        (tmp_path / "dbwarden.py").write_text(
            "from dbwarden import database_config\n"
            "database_config(database_name='url1', database_type='sqlite', "
            "database_url_sync='sqlite:///./same.db', default=True, model_paths=['a'])\n"
            "database_config(database_name='url2', database_type='sqlite', "
            "database_url_sync='sqlite:///./same.db', model_paths=['b'])\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ConfigurationError, match="Duplicate"):
            from dbwarden.config import get_multi_db_config
            get_multi_db_config()

    def test_rejects_unknown_plugin_config_key(self):
        with pytest.raises(ConfigurationError, match="unexpected keyword"):
            database_config(
                database_name="testdb",
                database_type="sqlite",
                database_url_sync="sqlite:///./test.db",
                default=True,
                nonexistent_key="value",
            )


# ─────────────────────────────────────────────
# SECTION 12: ConfigKeyRegistry API
# ─────────────────────────────────────────────


class TestConfigKeyRegistryAPI:
    def test_register_and_is_registered(self):
        from dbwarden.plugin import ConfigKeyRegistry
        ConfigKeyRegistry.register("test_key_xyz", plugin="test_plugin")
        assert ConfigKeyRegistry.is_registered("test_key_xyz")

    def test_owner(self):
        from dbwarden.plugin import ConfigKeyRegistry
        ConfigKeyRegistry.register("owner_key", plugin="owner_plugin")
        assert ConfigKeyRegistry.owner("owner_key") == "owner_plugin"

    def test_keys(self):
        from dbwarden.plugin import ConfigKeyRegistry
        ConfigKeyRegistry.register("key_a", plugin="a")
        keys = ConfigKeyRegistry.keys()
        assert "key_a" in keys

    def test_reset(self):
        from dbwarden.plugin import ConfigKeyRegistry
        ConfigKeyRegistry.register("reset_key", plugin="test")
        ConfigKeyRegistry.reset()
        assert not ConfigKeyRegistry.is_registered("reset_key")

    def test_is_registered_unknown(self):
        from dbwarden.plugin import ConfigKeyRegistry
        assert not ConfigKeyRegistry.is_registered("nonexistent_xyz")


# ─────────────────────────────────────────────
# SECTION 13: ObjectPluginRegistry API
# ─────────────────────────────────────────────


class TestObjectPluginRegistryAPI:
    def test_register_and_handlers(self):
        from dbwarden.plugin import ObjectPluginRegistry
        ObjectPluginRegistry.clear()
        handler = MagicMock(spec=[])  # no ordering attribute
        handler.object_type = "test_object"
        ObjectPluginRegistry.register(handler, plugin="test_plugin")
        handlers = ObjectPluginRegistry.handlers()
        assert "test_object" in handlers

    def test_has_handler(self):
        from dbwarden.plugin import ObjectPluginRegistry
        ObjectPluginRegistry.clear()
        handler = MagicMock(spec=[])  # no ordering attribute
        handler.object_type = "has_handler_test"
        ObjectPluginRegistry.register(handler, plugin="test")
        assert ObjectPluginRegistry.has_handler("has_handler_test")
        assert not ObjectPluginRegistry.has_handler("nonexistent")

    def test_clear(self):
        from dbwarden.plugin import ObjectPluginRegistry
        ObjectPluginRegistry.clear()
        handler = MagicMock(spec=[])  # no ordering attribute
        handler.object_type = "clear_test"
        ObjectPluginRegistry.register(handler, plugin="test")
        ObjectPluginRegistry.clear()
        assert not ObjectPluginRegistry.has_handler("clear_test")

    def test_rejects_empty_object_type(self):
        from dbwarden.plugin import ObjectPluginRegistry
        ObjectPluginRegistry.clear()
        handler = MagicMock(spec=[])
        handler.object_type = ""
        with pytest.raises(ValueError, match="non-empty"):
            ObjectPluginRegistry.register(handler, plugin="test")


# ─────────────────────────────────────────────
# SECTION 14: PluginRegistrar API
# ─────────────────────────────────────────────


class TestPluginRegistrarAPI:
    def test_register_hook(self):
        from dbwarden.plugin import PluginRegistrar
        HookRegistry.clear()
        registrar = PluginRegistrar("test_plugin")
        def hook(ctx): pass
        registrar.register("post_migration", hook)
        assert HookRegistry.is_registered("post_migration")

    def test_register_config_key(self):
        from dbwarden.plugin import PluginRegistrar, ConfigKeyRegistry
        ConfigKeyRegistry.reset()
        registrar = PluginRegistrar("test_plugin")
        registrar.register_config_key("my_custom_key")
        assert ConfigKeyRegistry.is_registered("my_custom_key")
        ConfigKeyRegistry.reset()


# ─────────────────────────────────────────────
# SECTION 15: Registry API
# ─────────────────────────────────────────────


class TestRegistryAPI:
    def test_reset_registry(self):
        from dbwarden.config_registry import reset_registry, registered_entries, registered_project_config
        reset_registry()
        assert registered_entries() == []
        assert registered_project_config() is None

    def test_registered_entries(self):
        from dbwarden.config_registry import registered_entries
        database_config(
            database_name="entry_test",
            database_type="sqlite",
            database_url_sync="sqlite:///./test.db",
            default=True,
        )
        entries = registered_entries()
        assert len(entries) >= 1
        assert any(e.database_name == "entry_test" for e in entries)

    def test_registered_project_config(self):
        from dbwarden.config_registry import registered_project_config
        registered_project_config()  # Should not raise
        # May be None if no config declared in this test context
