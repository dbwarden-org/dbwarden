from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from dbwarden.engine.lock import is_locked, migration_lock
from dbwarden.exceptions import LockError


class TestLockRepo:
    """Tests for repositories/lock_repo.py functions."""

    def _mock_config(self):
        return patch("dbwarden.connection.queries.get_database", return_value=MagicMock(
            database_type="sqlite", migration_table="_dbwarden_migrations"
        ))

    def test_acquire_lock_success(self):
        with self._mock_config(), patch("dbwarden.repositories.lock_repo.get_db_connection") as mock_conn:
            mock_conn.return_value.__enter__.return_value.execute.return_value.rowcount = 1
            from dbwarden.repositories.lock_repo import acquire_lock
            result = acquire_lock("test_db", "owner-1")
            assert result is True
            mock_conn.assert_called_once_with("test_db")

    def test_acquire_lock_failure(self):
        with self._mock_config(), patch("dbwarden.repositories.lock_repo.get_db_connection") as mock_conn:
            mock_conn.side_effect = RuntimeError("Connection failed")
            from dbwarden.repositories.lock_repo import acquire_lock
            result = acquire_lock()
            assert result is False

    def test_release_lock_success(self):
        with self._mock_config(), patch("dbwarden.repositories.lock_repo.get_db_connection") as mock_conn:
            mock_conn.return_value.__enter__.return_value.execute.return_value.rowcount = 1
            from dbwarden.repositories.lock_repo import release_lock
            result = release_lock("test_db", "owner-1")
            assert result is True
            mock_conn.assert_called_once_with("test_db")

    def test_release_lock_requires_owner(self):
        with patch("dbwarden.repositories.lock_repo.get_db_connection") as mock_conn:
            from dbwarden.repositories.lock_repo import release_lock

            assert release_lock("test_db") is False
            mock_conn.assert_not_called()

    def test_acquire_lock_returns_false_when_update_did_not_claim_row(self):
        with patch("dbwarden.repositories.lock_repo.get_db_connection") as mock_conn:
            mock_conn.return_value.__enter__.return_value.execute.return_value.rowcount = 0
            from dbwarden.repositories.lock_repo import acquire_lock

            assert acquire_lock("test_db", "owner-1") is False

    def test_force_release_lock_success(self):
        with self._mock_config(), patch("dbwarden.repositories.lock_repo.get_db_connection") as mock_conn:
            mock_conn.return_value.__enter__.return_value.execute.return_value.rowcount = 1
            from dbwarden.repositories.lock_repo import force_release_lock

            assert force_release_lock("test_db") is True

    def test_release_lock_failure(self):
        with self._mock_config(), patch("dbwarden.repositories.lock_repo.get_db_connection") as mock_conn:
            mock_conn.side_effect = RuntimeError("Release failed")
            from dbwarden.repositories.lock_repo import release_lock
            result = release_lock()
            assert result is False

    def test_check_lock_held(self):
        with self._mock_config(), patch("dbwarden.repositories.lock_repo.get_db_connection") as mock_conn:
            mock_execute = MagicMock()
            mock_execute.scalar_one_or_none.return_value = True
            mock_conn.return_value.__enter__.return_value.execute.return_value = mock_execute
            from dbwarden.repositories.lock_repo import check_lock
            result = check_lock("test_db")
            assert result is True

    def test_check_lock_not_held(self):
        with self._mock_config(), patch("dbwarden.repositories.lock_repo.get_db_connection") as mock_conn:
            mock_execute = MagicMock()
            mock_execute.scalar_one_or_none.return_value = False
            mock_conn.return_value.__enter__.return_value.execute.return_value = mock_execute
            from dbwarden.repositories.lock_repo import check_lock
            result = check_lock("test_db")
            assert result is False

    def test_check_lock_table_missing(self):
        with self._mock_config(), patch("dbwarden.repositories.lock_repo.get_db_connection") as mock_conn:
            mock_conn.side_effect = RuntimeError("No such table")
            from dbwarden.repositories.lock_repo import check_lock
            result = check_lock()
            assert result is False

    def test_create_lock_table(self):
        with self._mock_config(), patch("dbwarden.repositories.lock_repo.get_db_connection") as mock_conn:
            from dbwarden.repositories.lock_repo import create_lock_table_if_not_exists
            create_lock_table_if_not_exists("test_db")
            mock_conn.assert_called_once_with("test_db")


class TestMigrationLock:
    """Tests for engine/lock.py context manager."""

    def test_migration_lock_acquire_and_release(self):
        with (
            patch("dbwarden.engine.lock.get_config"),
            patch("dbwarden.engine.lock.acquire_lock", return_value=True) as mock_acquire,
            patch("dbwarden.engine.lock.release_lock") as mock_release,
        ):
            with migration_lock(timeout=5):
                pass
            mock_acquire.assert_called_once()
            mock_release.assert_called_once()

    def test_migration_lock_already_held(self):
        with (
            patch("dbwarden.engine.lock.get_config"),
            patch("dbwarden.engine.lock.acquire_lock", return_value=False),
            patch("dbwarden.engine.lock.time.sleep"),
        ):
            with pytest.raises(LockError, match="Could not acquire migration lock"):
                with migration_lock(timeout=5):
                    pass

    def test_migration_lock_timeout(self):
        with (
            patch("dbwarden.engine.lock.get_config"),
            patch("dbwarden.engine.lock.acquire_lock", return_value=False),
            patch("dbwarden.engine.lock.time.sleep"),
        ):
            with pytest.raises(LockError, match="Could not acquire migration lock"):
                with migration_lock(timeout=2):
                    pass

    def test_migration_lock_releases_on_exception(self):
        with (
            patch("dbwarden.engine.lock.get_config"),
            patch("dbwarden.engine.lock.acquire_lock", return_value=True),
            patch("dbwarden.engine.lock.release_lock") as mock_release,
        ):
            with pytest.raises(ValueError, match="something went wrong"):
                with migration_lock(timeout=5):
                    raise ValueError("something went wrong")
            mock_release.assert_called_once()

    def test_is_locked_true(self):
        with patch("dbwarden.engine.lock.check_lock", return_value=True):
            assert is_locked() is True

    def test_is_locked_false(self):
        with patch("dbwarden.engine.lock.check_lock", return_value=False):
            assert is_locked() is False
