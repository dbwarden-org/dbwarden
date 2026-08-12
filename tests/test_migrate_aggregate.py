from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dbwarden.commands.migrate import migrate_cmd


def test_migrate_all_attempts_every_database_and_raises_on_failure():
    calls: list[str] = []

    def run_one(*, db_name, **kwargs):
        calls.append(db_name)
        if db_name == "broken":
            raise RuntimeError("connection refused")

    config = SimpleNamespace(databases={"primary": object(), "broken": object()})
    with (
        patch("dbwarden.config.get_multi_db_config", return_value=config),
        patch("dbwarden.commands.migrate.migrate_single", side_effect=run_one),
    ):
        with pytest.raises(RuntimeError, match=r"Migration failed for 1 database\(s\)"):
            migrate_cmd(all_databases=True)

    assert calls == ["primary", "broken"]
