from typer.testing import CliRunner

from dbwarden.cli.main import app


class TestCliHelpFlags:
    def test_help_flag(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        assert "dbwarden" in result.output.lower()

    def test_h_alias(self):
        runner = CliRunner()
        result = runner.invoke(app, ["-h"])

        assert result.exit_code == 0
        assert "dbwarden" in result.output.lower()

    def test_h_alias_matches_help_output(self):
        runner = CliRunner()
        help_result = runner.invoke(app, ["--help"])
        h_result = runner.invoke(app, ["-h"])

        assert help_result.output == h_result.output
