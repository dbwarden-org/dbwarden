from __future__ import annotations

from pathlib import Path

import pytest

from dbwarden.plugin import PluginLockEntry, _write_plugin_lock, load_consent, record_consent


def test_consent_toml_escapes_structural_characters(tmp_path: Path):
    path = tmp_path / "consent.toml"
    record_consent('plugin"name', '1.0\nmalicious = true', path=path)
    loaded = load_consent(path)
    assert loaded['plugin"name']["version"] == '1.0\nmalicious = true'


def test_plugin_lock_toml_escapes_values(tmp_path: Path):
    path = tmp_path / "plugins.toml"
    entry = PluginLockEntry(
        distribution="plugin\"name",
        version="1.0\nvalue",
        filename="file.whl",
        sha256="hash",
        tier="community",
        verified=False,
        identity="identity",
        installed_at="now",
    )
    _write_plugin_lock({"plugin_name": entry}, path)
    assert "version = \"1.0\\nvalue\"" in path.read_text(encoding="utf-8")


def test_plugin_lock_rejects_structural_key(tmp_path: Path):
    entry = PluginLockEntry("dist", "1", "file", "hash", "community", False, "", "now")
    with pytest.raises(ValueError, match="Invalid plugin lock key"):
        _write_plugin_lock({"bad.key": entry}, tmp_path / "plugins.toml")
