from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestExtraCommands:
    @patch("dbwarden.lock.get_lock_status")
    @patch("dbwarden.lock.check_lock")
    @patch("dbwarden.output.console.print")
    def test_lock_status_locked(self, mock_print, mock_check, mock_status):
        mock_check.return_value = True
        mock_status.return_value = {
            "state": "RUNNING",
            "execution_id": "abc123",
            "host": "localhost",
            "pid": 1234,
            "migration_version": "V042",
            "acquired_at": "2026-08-22 12:00:00",
            "last_heartbeat_at": "2026-08-22 12:00:15",
        }

        from dbwarden.commands.extra import lock_status_cmd

        lock_status_cmd("test")
        mock_print.assert_called()

    @patch("dbwarden.lock.get_lock_status")
    @patch("dbwarden.lock.check_lock")
    @patch("dbwarden.output.console.print")
    def test_lock_status_unlocked(self, mock_print, mock_check, mock_status):
        mock_check.return_value = False
        mock_status.return_value = None

        from dbwarden.commands.extra import lock_status_cmd

        lock_status_cmd("test")
        mock_print.assert_called()

    @patch("dbwarden.lock.get_lock_status")
    @patch("dbwarden.lock.force_release_lock")
    @patch("dbwarden.lock.check_lock")
    @patch("dbwarden.output.console.print")
    def test_unlock_success(self, mock_print, mock_check, mock_release, mock_status):
        mock_check.return_value = True
        mock_release.return_value = True
        mock_status.return_value = {
            "state": "RUNNING",
            "execution_id": "abc123",
            "host": "localhost",
            "pid": 1234,
            "migration_version": "V042",
            "acquired_at": "2026-08-22 12:00:00",
            "last_heartbeat_at": "2026-08-22 12:00:15",
        }

        from dbwarden.commands.extra import unlock_cmd

        unlock_cmd("test", force=True)
        mock_print.assert_called_once()

    @patch("dbwarden.lock.get_lock_status")
    @patch("dbwarden.lock.force_release_lock")
    @patch("dbwarden.lock.check_lock")
    @patch("dbwarden.output.console.print")
    def test_unlock_not_held(self, mock_print, mock_check, mock_release, mock_status):
        mock_check.return_value = False

        from dbwarden.commands.extra import unlock_cmd

        unlock_cmd("test")
        mock_print.assert_called_once()

    @patch("dbwarden.lock.get_lock_status")
    @patch("dbwarden.lock.force_release_lock")
    @patch("dbwarden.lock.check_lock")
    @patch("dbwarden.output.console.print")
    def test_unlock_failure(self, mock_print, mock_check, mock_release, mock_status):
        mock_check.return_value = True
        mock_release.return_value = False
        mock_status.return_value = {
            "state": "RUNNING",
            "execution_id": "abc123",
            "host": "localhost",
            "pid": 1234,
        }

        from dbwarden.commands.extra import unlock_cmd

        unlock_cmd("test", force=True)
        mock_print.assert_called_once()
