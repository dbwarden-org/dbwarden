from __future__ import annotations

from dataclasses import dataclass, field


# Community plugins that passed dbwarden's verified-plugin review standard.
# Values are minimum verified versions. Future releases may extend this schema
# with upper bounds or explicit exclusions if verification needs tighter pinning.
#
# This stays a plain name -> version map because it is the trust decision, read
# on every plugin load by `verified_allows`. Everything a reviewer records that
# is *not* part of that decision lives in VERIFIED_METADATA below.
VERIFIED_PLUGINS: dict[str, str] = {}


@dataclass(frozen=True)
class VerifiedMetadata:
    """What verification recorded about a plugin beyond its version floor.

    ``deep_imports`` is the plugin's declared use of dbwarden modules outside the
    stable surface (`dbwarden.plugin`, `dbwarden.exceptions`,
    `dbwarden.engine.core`), taken from the verification issue. Plugins may import
    anything, so this is not a restriction: it is the record that makes the
    coupling visible, so a refactor can ask who it is about to break instead of
    finding out from a bug report. See ``plugins_depending_on``.
    """

    repository: str
    deep_imports: tuple[str, ...] = field(default_factory=tuple)


# Keyed by distribution name. Every key must also appear in VERIFIED_PLUGINS.
VERIFIED_METADATA: dict[str, VerifiedMetadata] = {}


def plugins_depending_on(module: str) -> list[str]:
    """Verified plugins that declared an import of ``module`` or a submodule.

    Call this before moving or renaming anything under ``dbwarden``: it answers
    "who is relying on this?" from the declarations made at verification time.

    Matching is on the package boundary, so ``dbwarden.output`` reports a plugin
    that declared ``dbwarden.output.tables`` but not one that declared
    ``dbwarden.outputs``.
    """
    hits: list[str] = []
    for dist_name, metadata in VERIFIED_METADATA.items():
        for declared in metadata.deep_imports:
            if declared == module or declared.startswith(module + "."):
                hits.append(dist_name)
                break
    return sorted(hits)


def declared_deep_imports() -> dict[str, tuple[str, ...]]:
    """Every declared deep import, keyed by distribution name."""
    return {
        dist_name: metadata.deep_imports
        for dist_name, metadata in sorted(VERIFIED_METADATA.items())
        if metadata.deep_imports
    }
