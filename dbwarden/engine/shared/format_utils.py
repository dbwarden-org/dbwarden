from __future__ import annotations

import keyword
import re
from typing import Any


def _sanitize_identifier(name: str) -> str:
    """Convert an arbitrary SQL identifier into a valid Python identifier."""
    safe = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    if not safe:
        safe = "_"
    if safe[0].isdigit():
        safe = "_" + safe
    if keyword.iskeyword(safe):
        safe = safe + "_"
    return safe


def _format_meta_value(value: Any, indent: str = "        ") -> list[str]:
    if isinstance(value, str):
        return [f"{indent}{value!r}"]
    if isinstance(value, list):
        if not value:
            return [f"{indent}[]"]
        lines = [f"{indent}["]
        for item in value:
            if isinstance(item, dict):
                lines.append(f"{indent}    {item!r},")
            else:
                lines.append(f"{indent}    {item!r},")
        lines.append(f"{indent}]")
        return lines
    return [f"{indent}{value!r}"]
