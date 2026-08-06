"""Coverage for `dbwarden settings show`.

This command had no tests, which is how it shipped broken: it read a
DatabaseEntry as if it were a resolved DatabaseConfig, so every invocation
raised AttributeError on `sqlalchemy_url`, and `display_value` was called with
one argument instead of three.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from dbwarden.commands.settings import handle_settings_show

CONFIG = (
    "from dbwarden import database_config\n\n"
    "database_config(\n"
    "    database_name='primary', default=True, database_type='postgresql',\n"
    "    database_url_sync='postgresql://user:pw@host/db', model_paths=['models'],\n"
    ")\n"
)


@pytest.fixture
def project_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            Path("dbwarden.py").write_text(CONFIG, encoding="utf-8")
            yield Path(tmpdir)
        finally:
            os.chdir(old_cwd)


def test_settings_show_runs(project_dir, capsys):
    handle_settings_show()
    out = capsys.readouterr().out
    assert "PRIMARY" in out
    assert "PostgreSQL" in out


def test_settings_show_reports_resolved_fields(project_dir, capsys):
    """Fields must come from the resolved DatabaseConfig, not the raw entry."""
    handle_settings_show()
    out = capsys.readouterr().out
    # sqlalchemy_url and migrations_dir only exist on the resolved config.
    assert "postgresql://user:pw@host/db" in out
    assert "migrations" in out
    assert "_dbwarden_migrations" in out


def test_settings_show_named_database(project_dir, capsys):
    handle_settings_show(database="primary")
    assert "PRIMARY" in capsys.readouterr().out
