from __future__ import annotations

import ast
import os
import pprint
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from dbwarden.logging import get_component_logger

from .ir import canonical_value


class FrozenDataArtifactImportError(ImportError):
    pass


_allow_frozen = ContextVar("dbwarden_allow_frozen_data_imports", default=False)
_logger = get_component_logger("data.frozen")


@contextmanager
def allow_frozen_data_imports():
    token = _allow_frozen.set(True)
    _logger.warning("Frozen data artifact import override enabled for scoped audit")
    try:
        yield
    finally:
        _allow_frozen.reset(token)


def guard_frozen_data_artifact(migration_id: str) -> None:
    scoped = _allow_frozen.get()
    environment = os.environ.get("DBWARDEN_ALLOW_FROZEN_DATA_IMPORT") == "1"
    if scoped or environment:
        _logger.warning(
            "Frozen data artifact import allowed for %s via %s override",
            migration_id,
            "scoped" if scoped else "environment",
        )
        return
    raise FrozenDataArtifactImportError(
        f"{migration_id}.data.py is a frozen DBWarden audit artifact; "
        "use static audit tooling or allow_frozen_data_imports()"
    )


def render_frozen(data_spec: dict, migration_id: str) -> str:
    if not isinstance(migration_id, str) or not migration_id:
        raise ValueError("migration_id must be a non-empty string")
    literal = pprint.pformat(canonical_value(data_spec), width=100, sort_dicts=True)
    return (
        "from dbwarden.data.frozen import guard_frozen_data_artifact\n\n"
        f"guard_frozen_data_artifact({migration_id!r})\n\n"
        f"DATA_SPEC = {literal}\n"
    )


def _read_frozen_record(path) -> tuple[str, dict]:
    path = Path(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ValueError(f"Invalid frozen data artifact: {path}") from exc
    imported = False
    guarded = False
    migration_id = None
    data_spec = None
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "dbwarden.data.frozen":
            imported = any(
                alias.name == "guard_frozen_data_artifact" and alias.asname is None
                for alias in node.names
            )
            if not imported:
                raise ValueError("Frozen artifact guard import is invalid")
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if (
                not isinstance(call.func, ast.Name)
                or call.func.id != "guard_frozen_data_artifact"
                or not imported
                or guarded
            ):
                raise ValueError("Frozen artifact contains executable code")
            if (
                len(call.args) != 1
                or call.keywords
                or not isinstance(call.args[0], ast.Constant)
                or not isinstance(call.args[0].value, str)
            ):
                raise ValueError("Frozen artifact guard is invalid")
            migration_id = call.args[0].value
            guarded = True
            continue
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "DATA_SPEC"
        ):
            if not guarded or data_spec is not None:
                raise ValueError("Frozen artifact DATA_SPEC is not guarded")
            try:
                data_spec = ast.literal_eval(node.value)
            except (ValueError, TypeError) as exc:
                raise ValueError("Frozen artifact DATA_SPEC must be a literal") from exc
            continue
        raise ValueError("Frozen artifact contains unsupported statements")
    if not imported or not guarded or not isinstance(data_spec, dict):
        raise ValueError("Frozen artifact requires its guard and a literal DATA_SPEC")
    return migration_id, data_spec


def read_frozen(path) -> dict:
    return _read_frozen_record(path)[1]
