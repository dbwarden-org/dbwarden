---
description: Find official, verified, and community dbwarden plugins.
---

# Finding Plugins

dbwarden plugins are standard Python packages that expose a `dbwarden.plugins` entry point. dbwarden classifies each discovered distribution into a [trust tier](consent-and-trust.md) before importing it, by matching its distribution name against the curated Official and Verified lists in core.

## Search PyPI

All plugins are distributed as:

```text
dbwarden-<name>
```

Search PyPI for `dbwarden-` to find them. The name alone does not confer trust: Official plugins are the dbwarden-owned packages listed in `dbwarden/_official.py` (for example `dbwarden-fastapi`, `dbwarden-pgsql-extensions`); every other `dbwarden-*` package is Community until it reaches the Verified list.

## Official Plugins

Official plugins are built and maintained by the dbwarden organization under the [`dbwarden` GitHub organization](https://github.com/dbwarden). They are enumerated in core (`dbwarden/_official.py`) and are eligible for provenance verification during `dbwarden plugin add`.

| Package | Repository | Purpose |
|---------|------------|---------|
| `dbwarden-fastapi` | [dbwarden-org/dbwarden-fastapi](https://github.com/dbwarden-org/dbwarden-fastapi) | FastAPI sessions, health/migration routes, lifespan. |
| `dbwarden-pgsql-extensions` | [dbwarden-org/dbwarden-pgsql-extensions](https://github.com/dbwarden-org/dbwarden-pgsql-extensions) | PostgreSQL `CREATE EXTENSION` diffing. |
| `dbwarden-pgsql-rbac` | [dbwarden-org/dbwarden-pgsql-rbac](https://github.com/dbwarden-org/dbwarden-pgsql-rbac) | PostgreSQL roles, grants, policies. |
| `dbwarden-pgsql-types` | [dbwarden-org/dbwarden-pgsql-types](https://github.com/dbwarden-org/dbwarden-pgsql-types) | PostgreSQL enum/domain/composite types. |
| `dbwarden-ch-rbac` | [dbwarden-org/dbwarden-ch-rbac](https://github.com/dbwarden-org/dbwarden-ch-rbac) | ClickHouse roles and grants. |
| `dbwarden-seeds` | [dbwarden-org/dbwarden-seeds](https://github.com/dbwarden-org/dbwarden-seeds) | Seed command implementations. |
| `dbwarden-sandbox` | [dbwarden-org/dbwarden-sandbox](https://github.com/dbwarden-org/dbwarden-sandbox) | Isolated model/config module loading. |

The authoritative list is always `OFFICIAL_PLUGINS` in `dbwarden/_official.py`.

## Verified Plugins

Verified plugins are community-maintained packages that passed dbwarden's [plugin test standard](../developing/verified-standard.md) and manual review. They load without consent once the installed version meets the verified minimum.

| Package | Minimum verified version | Description |
|---------|--------------------------|-------------|
| _None yet_ – feel free to [be the first](../developing/verified-standard.md#submit-for-verification)! | – | Verified entries are listed in `dbwarden/_verified.py` and this table as the ecosystem grows. |

Verification is not a security audit. See [consent and trust](consent-and-trust.md).

## Community Plugins

Any other package with a `dbwarden.plugins` entry point is treated as **Community**. Community plugins require explicit consent before loading. A community-curated index may be maintained as a Markdown page in the dbwarden repository; until then, discover them via PyPI search and review the source before trusting.
