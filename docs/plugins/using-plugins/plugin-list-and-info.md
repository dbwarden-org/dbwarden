---
description: Inspect discovered dbwarden plugins and registered hooks.
---

# Plugin List And Info

## List Plugins

```bash
dbwarden plugin list
```

The Rich table shows every discovered distribution with its version, tier, trust state, load state, the hooks and object handlers it registered, and its lockfile verification status.

```text
                                     dbwarden Plugins
┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Distribution           ┃ Version ┃ Tier      ┃ Trusted ┃ State   ┃ Hooks           ┃ Objects      ┃ Lock       ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ dbwarden-fastapi       │ 0.1.0   │ official  │ yes     │ loaded  │ health_routes,  │ -            │ provenance │
│                        │         │           │         │         │ session_factory │              │            │
│ dbwarden-pgsql-exten…  │ 0.3.0   │ official  │ yes     │ loaded  │ -               │ pg_extension │ provenance │
│ dbwarden-acme          │ 1.0.0   │ community │ no      │ skipped │ -               │ -            │ -          │
└────────────────────────┴─────────┴───────────┴─────────┴─────────┴─────────────────┴──────────────┴────────────┘
```

Column meanings:

- **Trusted**: `yes` if the plugin is allowed to load (official, verified-and-current, or consented community).
- **State**: `loaded`, `skipped` (untrusted community), `failed` (raised during `setup`), `incompatible` (built against a different plugin API version), or `discovered`.

An `incompatible` plugin is not broken: it targets a different version of the plugin contract than this dbwarden provides, so it was refused rather than allowed to register and produce migrations under the wrong assumptions. The **Error** row names both versions. Upgrade the plugin, or pin dbwarden to a release that provides the version it wants.
- **Hooks / Objects**: the value hooks and object handler types the plugin registered. A discovered-but-skipped plugin registers nothing, so these stay empty.
- **Lock**: the lockfile `verified` value (e.g. `provenance`) for provenance-locked installs.

### JSON Output

For CI or scripting, use `--format json`:

```bash
dbwarden plugin list --format json
```

```json
[
  {
    "distribution": "dbwarden-fastapi",
    "version": "0.1.0",
    "tier": "official",
    "trusted": true,
    "state": "loaded",
    "hooks": ["health_routes", "session_factory"],
    "object_handlers": [],
    "error": null,
    "lock": {"verified": "provenance", "identity": "https://github.com/dbwarden-org/dbwarden-fastapi", "...": "..."}
  }
]
```

## Plugin Details

```bash
dbwarden plugin info dbwarden-fastapi
```

```text
Plugin Info
  Distribution       dbwarden-fastapi
  Version            0.1.0
  Entry point        fastapi = dbwarden_fastapi:setup
  Tier               official
  Trusted            yes
  State              loaded
  Hooks              health_routes, session_factory
  Object handlers    -
  Error
  Repository         https://github.com/dbwarden-org/dbwarden-fastapi
  Verified minimum
  Lock verified      provenance
  Lock identity      https://github.com/dbwarden-org/dbwarden-fastapi
```

- For **Official** plugins, details include the repository and lockfile provenance fields.
- For **Verified** plugins, the **Verified minimum** row shows the version floor.
- For **Community** plugins, trust reflects consent state; `Repository` and `Verified minimum` are empty.

`plugin info` also supports `--format json`.

## Checking Registered Hooks

Both `plugin list` and `plugin info` report the hooks and object handlers registered **after load**. If a plugin is discovered but skipped (untrusted) or failed during `setup`, those columns are empty and, for failures, the **Error** row explains why.
