from __future__ import annotations

from pathlib import Path

from dbwarden.plugin import PluginLockEntry, _write_plugin_lock, record_consent


def test_plugin_lock_write_is_atomic(tmp_path: Path):
    path = tmp_path / "plugins.toml"
    entry = PluginLockEntry(
        distribution="dbwarden-test",
        version="1.0.0",
        filename="dbwarden_test-1.0.0.whl",
        sha256="abc",
        tier="community",
        verified=False,
        identity="",
        installed_at="now",
    )
    _write_plugin_lock({"dbwarden_test": entry}, path)
    assert "dbwarden-test" in path.read_text(encoding="utf-8")
    assert list(tmp_path.glob(".plugins.toml.*.tmp")) == []


def test_consent_write_is_atomic(tmp_path: Path):
    path = tmp_path / "consent.toml"
    record_consent("dbwarden-test", "1.0.0", path=path)
    assert "dbwarden-test" in path.read_text(encoding="utf-8")
    assert list(tmp_path.glob(".consent.toml.*.tmp")) == []
