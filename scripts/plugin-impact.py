#!/usr/bin/env python3
"""Ask which plugins depend on a DBWarden module before you change it.

Usage:
    python scripts/plugin-impact.py dbwarden.output
    python scripts/plugin-impact.py --all

Reads the deep-import declarations recorded at plugin verification
(``dbwarden/_verified.py``). Official plugins are checked directly from their
source when they are installed, since core owns them and their coupling is not
recorded in the approval manifest.

This is a refactoring aid, not a gate. A module with no dependants is safe to
move; one with dependants needs a deprecation shim or a coordinated release.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dbwarden._verified import (  # noqa: E402
    VERIFIED_METADATA,
    declared_deep_imports,
    plugins_depending_on,
)
from dbwarden._official import OFFICIAL_PLUGINS  # noqa: E402

STABLE = ("dbwarden.plugin", "dbwarden.exceptions", "dbwarden.engine.core")


def _official_deep_imports() -> dict[str, list[str]]:
    """Deep imports of official plugins, read from their installed source."""
    from dbwarden.plugin_conformance import core_imports_outside_stable_api

    found: dict[str, list[str]] = {}
    for dist_name in sorted(OFFICIAL_PLUGINS):
        package = dist_name.replace("-", "_")
        try:
            reported = core_imports_outside_stable_api(package)
        except Exception:
            continue  # not installed in this environment
        if reported:
            found[dist_name] = reported
    return found


def _report_all() -> int:
    verified = declared_deep_imports()
    official = _official_deep_imports()

    if not verified and not official:
        print("No deep imports declared or detected.")
        print(f"Stable surface: {', '.join(STABLE)}")
        return 0

    if official:
        print("Official plugins (read from installed source):")
        for dist_name, entries in official.items():
            print(f"  {dist_name}")
            for entry in entries:
                print(f"    {entry}")
        print()

    if verified:
        print("Verified plugins (declared at verification):")
        for dist_name, modules in verified.items():
            print(f"  {dist_name}: {', '.join(modules)}")
    else:
        print("Verified plugins: none have declared deep imports.")
    return 0


def _report_module(module: str) -> int:
    if any(module == s or module.startswith(s + ".") for s in STABLE):
        print(f"'{module}' is on the stable surface. Treat any change as breaking.")

    dependants = plugins_depending_on(module)
    if dependants:
        print(f"Verified plugins depending on '{module}':")
        for dist_name in dependants:
            print(f"  {dist_name}  ({VERIFIED_METADATA[dist_name].repository})")
    else:
        print(f"No verified plugin declared a dependency on '{module}'.")

    official_hits = {
        dist_name: [e for e in entries if module in e]
        for dist_name, entries in _official_deep_imports().items()
    }
    official_hits = {k: v for k, v in official_hits.items() if v}
    if official_hits:
        print(f"\nOfficial plugins importing '{module}':")
        for dist_name, entries in official_hits.items():
            print(f"  {dist_name}")
            for entry in entries:
                print(f"    {entry}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 1
    if argv[0] == "--all":
        return _report_all()
    return _report_module(argv[0])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
