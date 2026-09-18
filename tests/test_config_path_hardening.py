from __future__ import annotations

from pathlib import Path

from dbwarden.config.resolve import _discover_dbwarden_files, _full_scan_database_config_calls


def test_config_discovery_rejects_symlinked_dbwarden_file(tmp_path: Path):
    outside = tmp_path / "outside.py"
    outside.write_text("from dbwarden import database_config\n", encoding="utf-8")
    link = tmp_path / "dbwarden.py"
    link.symlink_to(outside)
    assert _discover_dbwarden_files(tmp_path) == []


def test_config_full_scan_rejects_symlinked_python_file(tmp_path: Path):
    outside = tmp_path / "outside.py"
    outside.write_text("from dbwarden import database_config\n", encoding="utf-8")
    link = tmp_path / "config.py"
    link.symlink_to(outside)
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
