---
description: Install and inspect your first DBWarden plugin.
---

# Quickstart: Add FastAPI Support

This quickstart walks through installing the official `dbwarden-fastapi` plugin, handling the consent prompt for community plugins, listing what DBWarden discovered, and using a session dependency in an endpoint.

## 1. Install The Plugin

Use DBWarden's plugin installer:

```bash
dbwarden plugin add dbwarden-fastapi
```

`dbwarden-fastapi` is an **Official** plugin, so `plugin add` verifies its provenance before installing and records the result in `.dbwarden/plugins.lock`.

You can also install directly with your package manager:

```bash
uv add dbwarden-fastapi
# or
pip install dbwarden-fastapi
```

To route `plugin add` through `uv` instead of pip, pass `--uv`:

```bash
dbwarden plugin add dbwarden-fastapi --uv
```

Preview the plan without touching your environment:

```bash
dbwarden plugin add dbwarden-fastapi --dry-run
```

```text
              Plugin Add (dry run): dbwarden-fastapi
  Tier            official
  Installer       python -m pip install dbwarden-fastapi
  Version         latest
  After install   provenance-verified, lockfile updated
```

## 2. Consent For Community Plugins

Official and Verified plugins load automatically. A **Community** plugin, any distribution not listed in core, is discovered but not imported until you consent to the exact installed version. When you run a DBWarden command in an interactive terminal, you are prompted:

```text
Enable community plugin 'dbwarden-example' version 0.1.0? [y/N]:
```

Answering `y` records consent in `.dbwarden/consent.toml` and loads the plugin. You can also consent ahead of time:

```bash
dbwarden plugin trust dbwarden-example
```

## 3. List Plugins

```bash
dbwarden plugin list
```

```text
                                     DBWarden Plugins
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Distribution     ┃ Version ┃ Tier     ┃ Trusted ┃ State  ┃ Hooks           ┃ Objects ┃ Lock       ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━┩
│ dbwarden-fastapi │ 0.1.0   │ official │ yes     │ loaded │ health_routes,  │ -       │ provenance │
│                  │         │          │         │        │ session_factory │         │            │
└──────────────────┴─────────┴──────────┴─────────┴────────┴─────────────────┴─────────┴────────────┘
```

For machine-readable output, add `--format json`:

```bash
dbwarden plugin list --format json
```

## 4. Use The Integration

The FastAPI integration exposes session dependencies through DBWarden's FastAPI API. A minimal endpoint that uses the async session for the `primary` database:

```python
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from dbwarden_fastapi import get_session

app = FastAPI()


@app.get("/users")
async def users(session: AsyncSession = Depends(get_session("primary"))):
    result = await session.execute(...)
    return {"users": result.scalars().all()}
```

`get_session` resolves the dependency through the plugin's `session_factory` hook. This import path is provided by the `dbwarden-fastapi` plugin package, not by DBWarden core. See the plugin's README for configuration.

## Next Steps

- [Installing plugins](using-plugins/installing.md)
- [Consent and trust](using-plugins/consent-and-trust.md)
- [Plugin CLI reference](reference/plugin-cli.md)
