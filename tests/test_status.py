from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestStatus:
    @pytest.fixture(autouse=True)
    def severity_config(self):
        from types import SimpleNamespace
        with patch("dbwarden.config.get_database", return_value=SimpleNamespace(max_severity="CRITICAL")):
            yield
    @patch("dbwarden.commands.status.get_migrations_directory")
    @patch("dbwarden.output.console.print")
    def test_status_single_no_migrations_dir(self, mock_print, mock_get_dir):
        mock_get_dir.side_effect = Exception("No dir")

        from dbwarden.commands.status import status_single

        status_single("test_db")
        mock_print.assert_called_once()

    @patch("dbwarden.commands.status.get_migrations_directory")
    @patch("dbwarden.commands.status.migrations_table_exists")
    @patch("dbwarden.commands.status.get_migrated_versions")
    @patch("dbwarden.engine.version.get_migration_filepaths_by_version")
    @patch("dbwarden.output.console.print")
    def test_status_single_applied(
        self, mock_print, mock_get_files, mock_get_versions, mock_table_exists, mock_get_dir
    ):
        mock_get_dir.return_value = "/tmp/migrations"
        mock_table_exists.return_value = True
        mock_get_versions.return_value = ["0001"]
        mock_get_files.return_value = {"0001": "/tmp/migrations/t__0001_a.sql"}

        from dbwarden.commands.status import status_single

        status_single("test_db")

    @patch("dbwarden.commands.status.get_migrations_directory")
    @patch("dbwarden.commands.status.migrations_table_exists")
    @patch("dbwarden.commands.status.get_migrated_versions")
    @patch("dbwarden.engine.version.get_migration_filepaths_by_version")
    def test_status_single_mixed(
        self, mock_get_files, mock_get_versions, mock_table_exists, mock_get_dir
    ):
        mock_get_dir.return_value = "/tmp/migrations"
        mock_table_exists.return_value = True
        mock_get_versions.return_value = ["0001"]
        mock_get_files.return_value = {
            "0001": "/tmp/migrations/t__0001_a.sql",
            "0002": "/tmp/migrations/t__0002_b.sql",
        }

        from dbwarden.commands.status import status_single

        with patch("dbwarden.output.console.print"):
            status_single("test_db")

    @patch("dbwarden.commands.status.get_migrations_directory")
    def test_status_single_empty_dir(self, mock_get_dir):
        mock_get_dir.return_value = "/tmp/migrations"

        from dbwarden.commands.status import status_single

        with (
            patch("dbwarden.commands.status.migrations_table_exists", return_value=False),
            patch("dbwarden.engine.version.get_migration_filepaths_by_version", return_value={}),
            patch("dbwarden.output.console.print"),
        ):
            status_single("test_db")
