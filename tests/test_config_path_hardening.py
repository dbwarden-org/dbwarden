from __future__ import annotations

from pathlib import Path

import pytest

from dbwarden.config.resolve import (
    _discover_dbwarden_files,
    _full_scan_database_config_calls,
)
from dbwarden.config_schema import _validate_impact_paths


def test_impact_paths_reject_sibling_with_same_prefix(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(Path, "resolve", lambda path: tmp_path / "project-extra" if str(path) == "linked" else path)
    with pytest.raises(ValueError, match="escape the project root"):
        _validate_impact_paths(None, None, ["linked"])


def test_config_discovery_rejects_symlinked_dbwarden_file(tmp_path: Path, symlink_factory):
    outside = tmp_path / "outside.py"
    outside.write_text("from dbwarden import database_config\n", encoding="utf-8")
    link = tmp_path / "dbwarden.py"
    symlink_factory(link, outside)
    assert _discover_dbwarden_files(tmp_path) == []


def test_config_full_scan_rejects_symlinked_python_file(tmp_path: Path, symlink_factory):
    outside = tmp_path / "outside.py"
    outside.write_text("from dbwarden import database_config\n", encoding="utf-8")
    link = tmp_path / "config.py"
    symlink_factory(link, outside)
    assert _full_scan_database_config_calls(tmp_path) == []


def test_config_full_scan_skips_framework_package(tmp_path: Path):
    pkg = tmp_path / "dbwarden"
    pkg.mkdir()
    framework_source = (
        "from dbwarden import DbwardenDatabase\n"
        "class Primary(DbwardenDatabase):\n"
        "    database_name = 'primary'\n"
        "    database_url_sync = 'sqlite:///primary.db'\n"
    )
    (pkg / "models.py").write_text(framework_source, encoding="utf-8")
    app = tmp_path / "app.py"
    app.write_text(framework_source, encoding="utf-8")
    assert _full_scan_database_config_calls(tmp_path) == [app]
