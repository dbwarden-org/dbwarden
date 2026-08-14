from __future__ import annotations

from pathlib import Path

import pytest

from dbwarden.commands.init import _atomic_write


def test_init_atomic_write_preserves_backup_and_original_on_failure(monkeypatch, tmp_path: Path):
    path = tmp_path / "dbwarden.py"
    path.write_text("old", encoding="utf-8")
    _atomic_write(path, "new")
    assert path.read_text(encoding="utf-8") == "new"
    assert path.with_suffix(".py.bak").read_text(encoding="utf-8") == "old"

    monkeypatch.setattr("dbwarden.files.os.replace", lambda *_args: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(OSError, match="replace failed"):
        _atomic_write(path, "newer")
    assert path.read_text(encoding="utf-8") == "new"
