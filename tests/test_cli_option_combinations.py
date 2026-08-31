"""Parametrized tests for CLI option combinations.

Tests that every command handles every combination of global and
subcommand flags without crashing.  Uses mocks so no real database
is needed.
"""

import itertools
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from dbwarden.cli.main import app


runner = CliRunner()


# ---------------------------------------------------------------------------
# Global flags that apply to every command
# ---------------------------------------------------------------------------

GLOBAL_FLAGS = {
    "dev": ["--dev"],
    "strict_translation": ["--strict-translation"],
    "json": ["--json"],
    "debug": ["--debug"],
    "log_level_snapshot_debug": ["--log-level", "snapshot:debug"],
    "log_level_plugin_warning": ["--log-level", "plugin:warning"],
}


def _global_flag_combinations():
    """Yield representative subsets of global flags.

    Full combinatorial (2^5 = 32) is excessive for every command.
    Instead we test:
    - each flag alone
    - pairwise combos of the most commonly composed flags
    - the full set
    """
    singles = list(GLOBAL_FLAGS.values())
    pairs = list(itertools.combinations(GLOBAL_FLAGS.values(), 2))
    full = list(itertools.chain.from_iterable(GLOBAL_FLAGS.values()))
    return singles + [list(itertools.chain(*p)) for p in pairs] + [full]


# ---------------------------------------------------------------------------
# version command (simplest, no subcommand args)
# ---------------------------------------------------------------------------


class TestVersionOptionCombinations:
    @pytest.mark.parametrize("global_flags", _global_flag_combinations())
    def test_version_with_global_flags(self, global_flags):
        result = runner.invoke(app, [*global_flags, "version"])
        assert result.exit_code == 0, (
            f"version failed with flags {global_flags}: {result.output}"
        )


# ---------------------------------------------------------------------------
# make-migrations command
# ---------------------------------------------------------------------------


class TestMakeMigrationsOptionCombinations:
    @pytest.mark.parametrize("global_flags", _global_flag_combinations())
    def test_make_migrations_with_global_flags(self, global_flags):
        with patch("dbwarden.cli.main.handle_make_migrations") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(
                    app, [*global_flags, "make-migrations", "test migration"]
                )
        assert result.exit_code == 0, (
            f"make-migrations failed with flags {global_flags}: {result.output}"
        )

    def test_make_migrations_verbose_and_database_combined(self):
        with patch("dbwarden.cli.main.handle_make_migrations") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(app, [
                    "--dev", "--json",
                    "make-migrations", "add column",
                    "--verbose",
                    "--database", "primary",
                ])
        assert result.exit_code == 0
        _, kwargs = mock.call_args
        assert kwargs["verbose"] is True
        assert kwargs["database"] == "primary"

    def test_make_migrations_debug_and_log_level_combined(self):
        with patch("dbwarden.cli.main.handle_make_migrations") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(app, [
                    "--debug",
                    "--log-level", "snapshot:debug",
                    "--log-level", "plugin:warning",
                    "make-migrations", "test",
                ])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# migrate command
# ---------------------------------------------------------------------------


class TestMigrateOptionCombinations:
    @pytest.mark.parametrize("global_flags", _global_flag_combinations())
    def test_migrate_with_global_flags(self, global_flags):
        with patch("dbwarden.cli.main.handle_migrate") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(app, [*global_flags, "migrate"])
        assert result.exit_code == 0, (
            f"migrate failed with flags {global_flags}: {result.output}"
        )

    def test_migrate_verbose_database_dry_run(self):
        with patch("dbwarden.cli.main.handle_migrate") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(app, [
                    "--dev",
                    "migrate",
                    "--verbose",
                    "--database", "primary",
                    "--dry-run",
                ])
        assert result.exit_code == 0
        _, kwargs = mock.call_args
        assert kwargs["verbose"] is True
        assert kwargs["database"] == "primary"
        assert kwargs["dry_run"] is True

    def test_migrate_all_flags_combined(self):
        with patch("dbwarden.cli.main.handle_migrate") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(app, [
                    "--dev", "--strict-translation", "--json", "--debug",
                    "--log-level", "snapshot:debug",
                    "migrate",
                    "--verbose",
                    "--database", "secondary",
                    "--count", "3",
                    "--dry-run",
                    "--with-backup",
                ])
        assert result.exit_code == 0
        _, kwargs = mock.call_args
        assert kwargs["verbose"] is True
        assert kwargs["database"] == "secondary"
        assert kwargs["count"] == 3
        assert kwargs["dry_run"] is True
        assert kwargs["with_backup"] is True

    def test_migrate_defer_snapshots_and_perf(self):
        with patch("dbwarden.cli.main.handle_migrate") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(app, [
                    "migrate",
                    "--defer-snapshots",
                    "--perf",
                    "--verbose",
                ])
        assert result.exit_code == 0
        _, kwargs = mock.call_args
        assert kwargs["defer_snapshots"] is True
        assert kwargs["perf"] is True


# ---------------------------------------------------------------------------
# check command
# ---------------------------------------------------------------------------


class TestCheckOptionCombinations:
    @pytest.mark.parametrize("global_flags", _global_flag_combinations())
    def test_check_with_global_flags(self, global_flags):
        with patch("dbwarden.cli.main.handle_check") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(app, [*global_flags, "check"])
        assert result.exit_code == 0, (
            f"check failed with flags {global_flags}: {result.output}"
        )

    def test_check_force_and_database(self):
        with patch("dbwarden.cli.main.handle_check") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(app, [
                    "--dev",
                    "check",
                    "--force",
                    "--database", "primary",
                ])
        assert result.exit_code == 0
        _, kwargs = mock.call_args
        assert kwargs["force"] is True
        assert kwargs["database"] == "primary"


# ---------------------------------------------------------------------------
# rollback command
# ---------------------------------------------------------------------------


class TestRollbackOptionCombinations:
    @pytest.mark.parametrize("global_flags", _global_flag_combinations())
    def test_rollback_with_global_flags(self, global_flags):
        with patch("dbwarden.cli.main.handle_rollback") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(app, [*global_flags, "rollback"])
        assert result.exit_code == 0, (
            f"rollback failed with flags {global_flags}: {result.output}"
        )

    def test_rollback_count_and_database(self):
        with patch("dbwarden.cli.main.handle_rollback") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(app, [
                    "--dev",
                    "rollback",
                    "--count", "2",
                    "--database", "primary",
                    "--verbose",
                ])
        assert result.exit_code == 0
        _, kwargs = mock.call_args
        assert kwargs["count"] == 2
        assert kwargs["database"] == "primary"
        assert kwargs["verbose"] is True


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------


class TestStatusOptionCombinations:
    @pytest.mark.parametrize("global_flags", _global_flag_combinations())
    def test_status_with_global_flags(self, global_flags):
        with patch("dbwarden.cli.main.handle_status") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(app, [*global_flags, "status"])
        assert result.exit_code == 0, (
            f"status failed with flags {global_flags}: {result.output}"
        )


# ---------------------------------------------------------------------------
# log-level error handling
# ---------------------------------------------------------------------------


class TestLogLevelErrorHandling:
    def test_invalid_log_level_spec_exits_with_error(self):
        result = runner.invoke(app, ["--log-level", "badformat", "version"])
        assert result.exit_code != 0

    def test_invalid_level_name_exits_with_error(self):
        result = runner.invoke(app, ["--log-level", "snapshot:banana", "version"])
        assert result.exit_code != 0

    def test_empty_component_exits_with_error(self):
        result = runner.invoke(app, ["--log-level", ":debug", "version"])
        assert result.exit_code != 0
