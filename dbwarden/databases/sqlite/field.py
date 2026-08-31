from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SqFieldSpec:
    generated: str | None = None
    generated_mode: str = "STORED"
    collate: str | None = None

    def to_col_info(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.generated is not None:
            d["sq_generated"] = self.generated
        if self.generated_mode != "STORED":
            d["sq_generated_mode"] = self.generated_mode
        if self.collate is not None:
            d["sq_collate"] = self.collate
        return d


def field(
    *,
    generated: str | None = None,
    generated_mode: str = "STORED",
    collate: str | None = None,
) -> SqFieldSpec:
    return SqFieldSpec(
        generated=generated,
        generated_mode=generated_mode,
        collate=collate,
    )
