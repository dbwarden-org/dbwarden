from __future__ import annotations

from dbwarden.engine.impact import scan_deep


def test_deep_scan_rejects_non_module_targets():
    assert scan_deep(["os.system('echo unsafe')", "../outside"]) == []
