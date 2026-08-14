from __future__ import annotations

from pathlib import Path

from dbwarden.engine.impact import _get_py_files


def test_impact_scan_does_not_follow_symlinked_directory(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.py").write_text("secret = True", encoding="utf-8")
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "linked").symlink_to(outside, target_is_directory=True)

    assert _get_py_files(str(scan)) == []
