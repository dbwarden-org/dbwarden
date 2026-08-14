from __future__ import annotations

import pytest

from dbwarden.commands.check import check_cmd
from dbwarden.commands.snapshot import snapshot_cmd
from dbwarden.exceptions import DBDisconnectedError


@pytest.mark.parametrize(
    "command, patch_target, kwargs",
    [
        (check_cmd, "dbwarden.commands.check.load_issues", {"database": "primary"}),
        (snapshot_cmd, "dbwarden.commands.snapshot.get_db_connection", {"table_name": "users", "database": "primary"}),
    ],
)
def test_explicit_live_command_does_not_turn_disconnect_into_success(
    monkeypatch, command, patch_target, kwargs
):
    if command is check_cmd:
        monkeypatch.setattr(patch_target, lambda **_kwargs: (_ for _ in ()).throw(DBDisconnectedError("offline")))
    else:
        from contextlib import contextmanager
        from types import SimpleNamespace

        monkeypatch.setattr(
            "dbwarden.commands.snapshot.get_database",
            lambda _database: SimpleNamespace(database_type="sqlite"),
        )

        @contextmanager
        def fail_connection(_database):
            raise DBDisconnectedError("offline")
            yield

        monkeypatch.setattr(patch_target, fail_connection)
    with pytest.raises(DBDisconnectedError):
        command(**kwargs)
