from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CheckSpec:
    """Typed spec for a check constraint in ``class Meta``.

    Example::

        class Meta(TableMeta):
            checks = [
                check("ck_users_age", "age >= 0"),
            ]
    """
    expression: str
    name: str | None = None
    no_inherit: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Return a plain dict in the same shape as :func:`check`."""
        d: dict[str, Any] = {"expression": self.expression}
        if self.name is not None:
            d["name"] = self.name
        if self.no_inherit:
            d["no_inherit"] = True
        return d


def check(
    name: str,
    expression: str,
    *,
    no_inherit: bool = False,
) -> dict[str, Any]:
    """Build a check constraint dict for ``class Meta``."""
    d: dict[str, Any] = {
        "name": name,
        "expression": expression,
    }
    if no_inherit:
        d["no_inherit"] = True
    return d


@dataclass
class UniqueSpec:
    """Typed spec for a unique constraint in ``class Meta``.

    Example::

        class Meta(PGTableMeta):
            pg_uniques = [
                unique("uq_users_email", ["email"], nulls_not_distinct=True),
            ]
    """
    columns: list[str]
    name: str | None = None
    nulls_not_distinct: bool = False
    deferrable: bool = False
    initially_deferred: bool = False
    include: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a plain dict in the same shape as :func:`unique`."""
        d: dict[str, Any] = {"columns": list(self.columns)}
        if self.name is not None:
            d["name"] = self.name
        if self.nulls_not_distinct:
            d["nulls_not_distinct"] = True
        if self.deferrable:
            d["deferrable"] = True
        if self.initially_deferred:
            d["initially_deferred"] = True
        if self.include is not None:
            d["include"] = list(self.include)
        return d


def unique(
    name: str,
    columns: list[str],
    *,
    nulls_not_distinct: bool = False,
    deferrable: bool = False,
    initially_deferred: bool = False,
    include: list[str] | None = None,
) -> dict[str, Any]:
    """Build a unique constraint dict for ``class Meta``."""
    d: dict[str, Any] = {
        "name": name,
        "columns": list(columns),
    }
    if nulls_not_distinct:
        d["nulls_not_distinct"] = True
    if deferrable:
        d["deferrable"] = True
    if initially_deferred:
        d["initially_deferred"] = True
    if include is not None:
        d["include"] = list(include)
    return d


def normalize_unique_spec(spec: Any) -> Any:
    """Return a plain dict for a ``UniqueSpec``, passing dicts through unchanged.

    Model discovery and snapshot code always work with plain constraint dicts,
    so typed specs declared in ``class Meta`` are normalized as early as
    possible to avoid ``AttributeError: 'UniqueSpec' object has no attribute
    'get'`` downstream.
    """
    return spec.as_dict() if isinstance(spec, UniqueSpec) else spec


def normalize_check_spec(spec: Any) -> Any:
    """Return a plain dict for a ``CheckSpec``, passing dicts through unchanged."""
    return spec.as_dict() if isinstance(spec, CheckSpec) else spec
