from __future__ import annotations

import ast
import json
import os
import re
from typing import Any


def parse_plan(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _affected_operations(plan: dict, verbose: bool = False) -> list[dict]:
    ops = plan.get("operations", [])
    if verbose:
        return ops
    return [op for op in ops if op.get("severity", "INFO") in ("WARNING", "CRITICAL")]


def _extract_targets(ops: list[dict]) -> list[str]:
    targets: set[str] = set()
    for op in ops:
        table = op.get("table") or op.get("old_table") or op.get("new_table")
        if table:
            targets.add(table)
        column = op.get("target") or op.get("column")
        if column:
            targets.add(column)
        old = op.get("old_table")
        new = op.get("new_table")
        if old:
            targets.add(old)
        if new:
            targets.add(new)
    return sorted(targets, key=len, reverse=True)


_FILE_CACHE: dict[str, list[str]] = {}


def _get_py_files(scan_path: str) -> list[str]:
    if scan_path in _FILE_CACHE:
        return _FILE_CACHE[scan_path]
    files: list[str] = []
    for root, dirs, fnames in os.walk(scan_path, followlinks=False):
        dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(root, name))]
        for fn in fnames:
            filepath = os.path.join(root, fn)
            if fn.endswith(".py") and not fn.startswith(".") and not os.path.islink(filepath):
                files.append(filepath)
    files.sort()
    _FILE_CACHE[scan_path] = files
    return files


def scan_file_grep(filepath: str, targets: list[str]) -> list[dict]:
    results: list[dict] = []
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, IOError):
        return results

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//", "--", '"""', "'''")):
            continue
        lower = stripped.lower()
        for target in targets:
            if target.lower() in lower:
                idx = lower.index(target.lower())
                start = max(0, idx - 30)
                end = min(len(stripped), idx + len(target) + 30)
                snippet = stripped[start:end].strip()
                results.append({
                    "file": filepath,
                    "line": lineno,
                    "snippet": snippet,
                    "kind": "grep",
                })
                break
    return results


def scan_file_ast(filepath: str, targets: list[str]) -> list[dict]:
    results: list[dict] = []
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            source = f.read()
    except (OSError, IOError):
        return results

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return results

    target_set = set(targets)
    target_lower = {t.lower() for t in targets}

    class _Visitor(ast.NodeVisitor):
        def visit_Attribute(self, node: ast.Attribute) -> None:
            if isinstance(node.attr, str) and node.attr.lower() in target_lower:
                results.append({
                    "file": filepath,
                    "line": node.lineno,
                    "snippet": ast.unparse(node) if hasattr(ast, "unparse") else f".{node.attr}",
                    "kind": "attribute_access",
                })
            self.generic_visit(node)

        def visit_Str(self, node: ast.Str) -> None:
            if node.s in target_set:
                results.append({
                    "file": filepath,
                    "line": node.lineno,
                    "snippet": repr(node.s),
                    "kind": "string_literal",
                })
            self.generic_visit(node)

        def visit_Constant(self, node: ast.Constant) -> None:
            if isinstance(node.value, str) and node.value in target_set:
                results.append({
                    "file": filepath,
                    "line": node.lineno,
                    "snippet": repr(node.value),
                    "kind": "string_literal",
                })
            self.generic_visit(node)

    _Visitor().visit(tree)
    return results


def scan_deep(targets: list[str]) -> list[dict]:
    results: list[dict] = []
    for target in targets:
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", target):
            continue
        try:
            import importlib
            mod = importlib.import_module(target)
            results.append({
                "file": getattr(mod, "__file__", f"<module {target}>"),
                "line": 0,
                "snippet": f"import {target}",
                "kind": "deep_import",
            })
        except ImportError:
            pass
    return results


def analyze_impact(
    plan_path: str,
    scan_path: str = ".",
    deep: bool = False,
    verbose: bool = False,
) -> dict:
    plan = parse_plan(plan_path)
    ops = _affected_operations(plan, verbose=verbose)
    targets = _extract_targets(ops)

    if not targets:
        plan["impact"] = []
        return plan

    py_files = _get_py_files(scan_path)
    refs: list[dict] = []

    for fpath in py_files:
        refs.extend(scan_file_ast(fpath, targets))

    found_targets = {r["snippet"] for r in refs}

    for fpath in py_files:
        remaining = [t for t in targets if t.lower() not in {r.lower() for r in found_targets}]
        if not remaining:
            break
        grep_results = scan_file_grep(fpath, remaining)
        for gr in grep_results:
            found_targets.add(gr["snippet"])
        refs.extend(gr for gr in grep_results if gr["snippet"] not in found_targets)

    if deep:
        refs.extend(scan_deep(targets))

    impact_by_op: list[dict] = []
    for op in ops:
        op_type = op.get("type", "")
        table = op.get("table") or op.get("old_table") or ""
        column = op.get("target") or op.get("column") or ""
        op_refs = []
        for r in refs:
            if column and column.lower() in r.get("snippet", "").lower():
                op_refs.append(r)
            elif table and table.lower() in r.get("snippet", "").lower():
                op_refs.append(r)
        if op_refs:
            impact_by_op.append({
                "operation_type": op_type,
                "table": table,
                "column": column,
                "references": op_refs,
            })

    plan["impact"] = impact_by_op
    return plan
