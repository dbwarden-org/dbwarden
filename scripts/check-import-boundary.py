#!/usr/bin/env python3
"""Enforce dbwarden import-boundary rules.

Rule 1: engine/core/ must never import from engine/backends/
         or any backend-specific package.

Rule 2: engine/shared/ files must not contain backend names
         in function bodies (clickhouse, ch_, postgresql, pg_,
         mysql, my_, mariadb, md_, sqlite, sq_).

Rule 3: Handlers live ONLY in their own backend package.
         3a: backends/<name>/handlers/ must define only <name>'s handlers.
         3b: No package outside backends/ may define handler classes.

Note: engine/model_discovery/ is intentionally exempt; it translates
      models into backend-specific SQL and legitimately imports from
      engine/backends/ for type mapping, rendering, and code generation.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_NAMES = {"clickhouse", "postgresql", "mysql", "mariadb", "sqlite"}
BACKEND_PREFIXES = {"ch_", "pg_", "my_", "md_", "sq_"}
PREFIX_TO_BACKEND = {
    "ch_": "clickhouse", "pg_": "postgresql",
    "my_": "mysql", "md_": "mariadb", "sq_": "sqlite",
}


def _is_backend_import(node: ast.Alias) -> bool:
    parts = node.name.split(".")
    for i, part in enumerate(parts):
        if part in BACKEND_NAMES:
            if i == 0 or parts[i - 1] == "backends":
                return True
    return False


def _has_backend_name_in_body(body_text: str) -> bool:
    lower = body_text.lower()
    for name in BACKEND_NAMES:
        if name in lower:
            return True
    for prefix in BACKEND_PREFIXES:
        if prefix in lower:
            return True
    return False


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    rel = path.relative_to(REPO_ROOT)
    text = path.read_text()

    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        errors.append(f"{rel}: syntax error: {e}")
        return errors

    is_core = "core" in rel.parts
    is_shared = "shared" in rel.parts

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            mod_parts = node.module.split(".")
            for alias in node.names:
                if _is_backend_import(alias):
                    errors.append(
                        f"{rel}:{node.lineno}: imports backend ({alias.name})"
                    )

            if is_core and rel.name != "plugin_api.py":
                for part in mod_parts:
                    if part in BACKEND_NAMES:
                        errors.append(
                            f"{rel}:{node.lineno}: core imports backend ({node.module})"
                        )
                        break
                    if part == "backends":
                        errors.append(
                            f"{rel}:{node.lineno}: core imports from backends/"
                        )
                        break


        if is_shared and isinstance(node, ast.FunctionDef):
            func_text = text[node.lineno : node.end_lineno]
            if _has_backend_name_in_body(func_text):
                errors.append(
                    f"{rel}:{node.lineno}: shared function '{node.name}' "
                    f"contains backend-specific name"
                )

    if is_shared:
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            for name in BACKEND_NAMES:
                if name in stripped.lower():
                    errors.append(
                        f"{rel}:{i}: shared file contains backend name '{name}'"
                    )
                    break

    # Rule 3a: backends/<name>/handlers/ must contain only <name>'s handlers
    if "backends" in rel.parts and "handlers" in rel.parts:
        parts = rel.parts
        backend_idx = parts.index("backends")
        if backend_idx + 1 < len(parts):
            expected_backend = parts[backend_idx + 1]
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    name_lower = node.name.lower()
                    for prefix, mapped_backend in PREFIX_TO_BACKEND.items():
                        if name_lower.startswith(prefix):
                            if mapped_backend != expected_backend:
                                errors.append(
                                    f"{rel}:{node.lineno}: handler '{node.name}' "
                                    f"uses '{prefix}' prefix but lives in "
                                    f"backends/{expected_backend}/"
                                )
                    for other in BACKEND_NAMES:
                        if other != expected_backend and other in name_lower:
                            errors.append(
                                f"{rel}:{node.lineno}: handler '{node.name}' "
                                f"references backend '{other}' but lives in "
                                f"backends/{expected_backend}/"
                            )

    # Rule 3b: no handler classes outside backends/
    if "backends" not in rel.parts:
        # Core protocol defines ObjectHandler which is a Protocol: allow it
        if rel.name == "protocol.py" and "core" in rel.parts:
            pass
        else:
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name.endswith("Handler"):
                    errors.append(
                        f"{rel}:{node.lineno}: handler class '{node.name}' "
                        f"defined outside backends/"
                    )

    return errors


def main() -> int:
    all_errors: list[str] = []
    engine_dir = REPO_ROOT / "dbwarden" / "engine"

    for path in sorted(engine_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        errors = check_file(path)
        all_errors.extend(errors)

    if all_errors:
        print("Import-boundary violations found:\n")
        for err in all_errors:
            print(f"  {err}")
        return 1

    print("All import-boundary checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
