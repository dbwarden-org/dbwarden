from __future__ import annotations

import pytest

from dbwarden.engine.file_parser import get_description_from_filename
from dbwarden.engine.core.snapshot_io import write_snapshot


@pytest.mark.parametrize("filename", ["", ".sql", "/tmp/.sql"])
def test_empty_migration_filename_description_is_safe(filename):
    assert get_description_from_filename(filename) == ""


def test_migration_filename_description_uses_basename():
    assert get_description_from_filename("/tmp/0001_create_users.sql") == "create users"


@pytest.mark.parametrize("migration_id", ["../escape", "nested/id", ""])
def test_snapshot_rejects_path_traversal_migration_id(tmp_path, monkeypatch, migration_id):
    with pytest.raises(ValueError, match="migration_id"):
        write_snapshot({}, database="primary", migration_id=migration_id)
