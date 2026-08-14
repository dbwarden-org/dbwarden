from __future__ import annotations

from pathlib import Path

from dbwarden.files import atomic_write_text


def test_atomic_state_write_handles_nested_destination(tmp_path: Path):
    target = tmp_path / ".dbwarden" / "model_state.json"
    atomic_write_text(target, '{"tables": {}}\n')
    assert target.read_text(encoding="utf-8") == '{"tables": {}}\n'
