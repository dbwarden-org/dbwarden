"""Engine-related exceptions."""
from __future__ import annotations

from .core import DBWardenError


class OrderingError(DBWardenError, ValueError):
    """Raised when plugin ordering constraints cannot be satisfied."""

    pass


class RollbackContractError(DBWardenError, ValueError):
    """Raised when generated rollback SQL violates the rollback contract."""

    pass
