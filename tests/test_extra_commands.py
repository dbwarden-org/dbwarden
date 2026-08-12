from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestExtraCommands:
    @patch("dbwarden.repositories.check_lock")
    @patch("dbwarden.output.console.print")
    def test_lock_status_locked(self, mock_print, mock_check):
        mock_check.return_value = True

        from dbwarden.commands.extra import lock_status_cmd

        lock_status_cmd("test")
        mock_print.assert_called()

    @patch("dbwarden.repositories.check_lock")
    @patch("dbwarden.output.console.print")
    def test_lock_status_unlocked(self, mock_print, mock_check):
        mock_check.return_value = False

        from dbwarden.commands.extra import lock_status_cmd

        lock_status_cmd("test")
        mock_print.assert_called()

    @patch("dbwarden.repositories.force_release_lock")
    @patch("dbwarden.repositories.check_lock")
    @patch("dbwarden.output.console.print")
    def test_unlock_success(self, mock_print, mock_check, mock_release):
        mock_check.return_value = True
        mock_release.return_value = True

        from dbwarden.commands.extra import unlock_cmd

        unlock_cmd("test")
        mock_print.assert_called_once()

    @patch("dbwarden.repositories.force_release_lock")
    @patch("dbwarden.repositories.check_lock")
    @patch("dbwarden.output.console.print")
    def test_unlock_not_held(self, mock_print, mock_check, mock_release):
        mock_check.return_value = False

        from dbwarden.commands.extra import unlock_cmd

        unlock_cmd("test")
        mock_print.assert_called_once()

    @patch("dbwarden.repositories.force_release_lock")
    @patch("dbwarden.repositories.check_lock")
    @patch("dbwarden.output.console.print")
    def test_unlock_failure(self, mock_print, mock_check, mock_release):
        mock_check.return_value = True
        mock_release.return_value = False

        from dbwarden.commands.extra import unlock_cmd

        unlock_cmd("test")
        mock_print.assert_called_once()
