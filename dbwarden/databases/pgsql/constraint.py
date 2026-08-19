from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ExcludeSpec:
    name: str | None = None
    using: str = "gist"
    where: str | None = None
    elements: list[dict[str, Any]] | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a plain dict in the same shape as :func:`exclude`."""
        d: dict[str, Any] = {"using": self.using}
        if self.name is not None:
            d["name"] = self.name
        if self.where is not None:
            d["where"] = self.where
        if self.elements is not None:
            d["elements"] = list(self.elements)
        return d


def exclude(
    name: str,
    *,
    using: str = "gist",
    where: str | None = None,
    elements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    d: dict[str, Any] = {"name": name, "using": using}
    if where is not None:
        d["where"] = where
    if elements is not None:
        d["elements"] = list(elements)
    return d


def normalize_exclude_spec(spec: Any) -> Any:
    """Return a plain dict for an ``ExcludeSpec``, passing dicts through unchanged."""
    return spec.as_dict() if isinstance(spec, ExcludeSpec) else spec
