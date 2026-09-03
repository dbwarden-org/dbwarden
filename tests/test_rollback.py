from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dbwarden.lock import LockAcquisition


class TestRollback:
    def test_rollback_count_and_version_mutual_exclusion(self):
        from dbwarden.commands.rollback import rollback_cmd

        with (
            patch("dbwarden.commands.rollback.get_database") as mock_get_db,
            patch("dbwarden.commands.rollback.get_migrations_directory") as mock_mig_dir,
            patch("dbwarden.commands.rollback.create_migrations_table_if_not_exists"),
            patch("dbwarden.commands.rollback.create_lock_table_if_not_exists"),
        ):
            mock_get_db.return_value = MagicMock()
            mock_get_db.return_value.sqlalchemy_url = "sqlite:///test.db"
            mock_get_db.return_value.database_type = "sqlite"
            mock_mig_dir.return_value = "/tmp/migrations"

            with pytest.raises(ValueError, match="Cannot specify both"):
                rollback_cmd(count=2, to_version="0003")

    def test_rollback_nothing_to_rollback(self):
        from dbwarden.commands.rollback import rollback_cmd

        mock_conn = MagicMock()
        with (
            patch("dbwarden.commands.rollback.get_database") as mock_get_db,
            patch("dbwarden.commands.rollback.get_migrations_directory"),
            patch("dbwarden.commands.rollback.create_migrations_table_if_not_exists"),
            patch("dbwarden.commands.rollback.create_lock_table_if_not_exists"),
            patch("dbwarden.commands.rollback.check_lock", return_value=False),
            patch(
                "dbwarden.commands.rollback.acquire_lock",
                return_value=LockAcquisition(
                    acquired=True,
                    execution_id="test-exec",
                    owner_id="test-owner",
                    strategy=MagicMock(),
                    connection=mock_conn,
                ),
            ),
            patch("dbwarden.commands.rollback.release_lock", return_value=True),
            patch("dbwarden.commands.rollback.get_latest_versions", return_value=[]),
            patch("dbwarden.output.console.print") as mock_print,
        ):
            mock_get_db.return_value = MagicMock()
            mock_get_db.return_value.sqlalchemy_url = "sqlite:///test.db"
            mock_get_db.return_value.database_type = "sqlite"

            rollback_cmd()
            mock_print.assert_any_call("Nothing to rollback.", style="cyan")

    def test_rollback_default_count_is_one(self):
        from dbwarden.commands.rollback import rollback_cmd

        mock_conn = MagicMock()
        with (
            patch("dbwarden.commands.rollback.get_database") as mock_get_db,
            patch("dbwarden.commands.rollback.get_migrations_directory", return_value="/tmp/migrations"),
            patch("dbwarden.commands.rollback.create_migrations_table_if_not_exists"),
            patch("dbwarden.commands.rollback.create_lock_table_if_not_exists"),
            patch("dbwarden.commands.rollback.check_lock", return_value=False),
            patch(
                "dbwarden.commands.rollback.acquire_lock",
                return_value=LockAcquisition(
                    acquired=True,
                    execution_id="test-exec",
                    owner_id="test-owner",
                    strategy=MagicMock(),
                    connection=mock_conn,
                ),
            ),
            patch("dbwarden.commands.rollback.release_lock", return_value=True),
            patch("dbwarden.commands.rollback.get_latest_versions", return_value=["0002"]),
            patch("dbwarden.commands.rollback._get_versions_to_rollback", return_value={"0002": "/tmp/migrations/test__0002_roll.sql"}),
            patch("dbwarden.commands.rollback.parse_rollback_statements", return_value=["DROP TABLE users"]),
            patch("dbwarden.commands.rollback.run_migration"),
            patch("dbwarden.output.console.print"),
        ):
            mock_get_db.return_value = MagicMock()
            mock_get_db.return_value.sqlalchemy_url = "sqlite:///test.db"
            mock_get_db.return_value.database_type = "sqlite"

            rollback_cmd()

    def test_get_versions_to_rollback_empty(self):
        from dbwarden.commands.rollback import _get_versions_to_rollback

        result = _get_versions_to_rollback([], "/tmp/migs")
        assert result == {}

    def test_get_versions_to_rollback_with_versions(self):
        from dbwarden.commands.rollback import _get_versions_to_rollback

        with patch(
            "dbwarden.engine.version.get_migration_filepaths_by_version",
            return_value={"0001": "/tmp/migs/test__0001_a.sql", "0002": "/tmp/migs/test__0002_b.sql"},
        ):
            result = _get_versions_to_rollback(["0002"], "/tmp/migs")
            assert len(result) == 1
            assert list(result.keys()) == ["0002"]
