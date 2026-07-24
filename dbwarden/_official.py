from __future__ import annotations

from dataclasses import dataclass

from dbwarden._verified import VERIFIED_PLUGINS


@dataclass(frozen=True)
class OfficialSpec:
    """Trusted-publishing identity for an official plugin.

    ``repo_slug`` and ``workflow`` are matched against the PEP 740 attestation
    PyPI records at publish time (see ``verify_official_provenance``). They must
    stay in sync with each repository's actual GitHub Actions Trusted Publishing
    config: ``workflow`` is the publishing workflow's filename (e.g.
    ``publish.yml``), matched by basename. If a repo renames its workflow or
    moves to a different owner, update the spec here or ``dbwarden plugin add``
    will fail closed for that plugin.
    """

    pypi: str
    repo_slug: str
    workflow: str = "publish.yml"
    min_version: str | None = None
    pinned_version: str | None = None
    sha256: str | None = None

    @property
    def repository(self) -> str:
        return f"https://github.com/{self.repo_slug}"


OFFICIAL_PLUGINS: dict[str, OfficialSpec] = {
    "dbwarden-ch-rbac": OfficialSpec(
        pypi="dbwarden-ch-rbac",
        repo_slug="dbwarden-org/dbwarden-ch-rbac",
    ),
    "dbwarden-fastapi": OfficialSpec(
        pypi="dbwarden-fastapi",
        repo_slug="dbwarden-org/dbwarden-fastapi",
    ),
    "dbwarden-pgsql-extensions": OfficialSpec(
        pypi="dbwarden-pgsql-extensions",
        repo_slug="dbwarden-org/dbwarden-pgsql-extensions",
    ),
    "dbwarden-pgsql-rbac": OfficialSpec(
        pypi="dbwarden-pgsql-rbac",
        repo_slug="dbwarden-org/dbwarden-pgsql-rbac",
    ),
    "dbwarden-pgsql-types": OfficialSpec(
        pypi="dbwarden-pgsql-types",
        repo_slug="dbwarden-org/dbwarden-pgsql-types",
    ),
    "dbwarden-sandbox": OfficialSpec(
        pypi="dbwarden-sandbox",
        repo_slug="dbwarden-org/dbwarden-sandbox",
    ),
    "dbwarden-seeds": OfficialSpec(
        pypi="dbwarden-seeds",
        repo_slug="dbwarden-org/dbwarden-seeds",
    ),
}


def classify(dist_name: str) -> str:
    if dist_name in OFFICIAL_PLUGINS:
        return "official"
    if dist_name in VERIFIED_PLUGINS:
        return "verified"
    return "community"
