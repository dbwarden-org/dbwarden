from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from dbwarden.cli.main import app
from dbwarden.logging import Verbosity, get_logger, reset_logger


@pytest.fixture(autouse=True)
def isolated_logger():
    """Start each test with a fresh global logger singleton."""
    reset_logger()
    yield
    reset_logger()


class TestCliDebugFlag:
    def test_debug_flag_sets_logger_to_debug_level(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--debug", "version"])
        assert result.exit_code == 0
        assert get_logger().debug_level == 10
        assert get_logger().debug_enabled is True

    def test_debug_flag_does_not_change_verbosity(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--debug", "version"])
        assert result.exit_code == 0
        assert get_logger().verbosity == get_logger().Verbosity.NORMAL

    def test_debug_level_option_sets_exact_level(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--debug-level", "WARNING", "version"])
        assert result.exit_code == 0
        assert get_logger().debug_level == 30

    def test_trace_level_option_sets_trace_level(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--debug-level", "trace", "version"])
        assert result.exit_code == 0
        assert get_logger().debug_level == 5

    def test_trace_numeric_level_option(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--debug-level", "5", "version"])
        assert result.exit_code == 0
        assert get_logger().debug_level == 5

    def test_debug_level_option_accepts_numeric(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--debug-level", "10", "version"])
        assert result.exit_code == 0
        assert get_logger().debug_level == 10

    def test_debug_level_takes_precedence_over_debug(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--debug", "--debug-level", "ERROR", "version"])
        assert result.exit_code == 0
        assert get_logger().debug_level == 40

    def test_invalid_debug_level_exits_with_error(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--debug-level", "bogus", "version"])
        assert result.exit_code == 2
        assert "Invalid debug level" in result.output

    def test_no_debug_flags_leave_default_level(self):
        runner = CliRunner()
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert get_logger().debug_level == 20
        assert get_logger().debug_enabled is False

    def test_debug_and_verbose_compose_on_make_migrations(self):
        runner = CliRunner()
        with patch("dbwarden.cli.main.handle_make_migrations") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(app, [
                    "--debug",
                    "make-migrations", "add column",
                    "--verbose",
                ])
            assert result.exit_code == 0
        _, kwargs = mock.call_args
        assert kwargs["verbose"] is True
        assert get_logger().debug_level == 10

    def test_debug_level_with_make_migrations_passes_verbose(self):
        runner = CliRunner()
        with patch("dbwarden.cli.main.handle_make_migrations") as mock:
            with patch("dbwarden.cli.main.validate_directory"):
                result = runner.invoke(app, [
                    "--debug-level", "warning",
                    "make-migrations", "add column",
                    "--verbose",
                ])
            assert result.exit_code == 0
        _, kwargs = mock.call_args
        assert kwargs["verbose"] is True
        assert get_logger().debug_level == 30


class TestMakeMigrationsVerboseForwarding:
    def test_make_migrations_cmd_forwards_verbose_to_logger(self):
        from dbwarden.commands.make_migrations import make_migrations_cmd

        with patch("dbwarden.commands.make_migrations._run_offline_migrations"):
            make_migrations_cmd(verbose=True, offline=True)

        assert get_logger().verbosity == Verbosity.VERBOSE

    def test_make_migrations_cmd_without_verbose_keeps_normal(self):
        from dbwarden.commands.make_migrations import make_migrations_cmd

        with patch("dbwarden.commands.make_migrations._run_offline_migrations"):
            make_migrations_cmd(verbose=False, offline=True)

        assert get_logger().verbosity == Verbosity.NORMAL
