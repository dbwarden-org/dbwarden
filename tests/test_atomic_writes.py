from __future__ import annotations

from pathlib import Path

import pytest

from dbwarden.files import atomic_write_text


def test_atomic_write_text_replaces_destination(tmp_path: Path):
    target = tmp_path / "state.json"
    target.write_text('{"old": true}', encoding="utf-8")

    atomic_write_text(target, '{"new": true}\n')

    assert target.read_text(encoding="utf-8") == '{"new": true}\n'
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_atomic_write_text_preserves_existing_file_on_replace_failure(monkeypatch, tmp_path: Path):
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("replace failed")

    monkeypatch.setattr("dbwarden.files.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".state.json.*.tmp")) == []
