---
description: dbwarden plugins extend core with runtime integrations and schema object support.
---

# dbwarden Plugins

Plugins let dbwarden keep a clean core while still supporting framework integrations, backend-specific schema objects, and community extensions. They are normal Python packages distributed through PyPI (or another package source) and discovered through the `dbwarden.plugins` entry point group.

dbwarden supports two plugin kinds:

- **Value plugins** supply values at named hook points: session factories, FastAPI routes, lifespans, seed commands, and module loaders.
- **Object plugins** add database object types to the schema diff pipeline: PostgreSQL extensions, roles, grants, policies, triggers, and other backend-specific objects.

Before plugin code is imported, dbwarden classifies the package by distribution name. Community plugin code is not imported until you explicitly consent to that exact version.

## Trust Tiers

| Tier | Meaning | Trust guarantee |
|------|---------|-----------------|
| Official | Built and maintained by the dbwarden organization. Published under organization-owned package names with provenance verified at install time. | Cryptographic and organizational. |
| Verified | Community-maintained, but passed the dbwarden plugin test standard and manual review. | Community review and technical compliance. |
| Community | Any `dbwarden.plugins` entry point not listed as Official or Verified. | Explicit consent only. |

Verification is not a security audit. Once loaded, a plugin runs with normal Python process privileges (it is not sandboxed). See [consent and trust](using-plugins/consent-and-trust.md).

## Philosophy

- Core defines stable contracts and migration semantics.
- Plugins use standard Python packaging and entry points.
- dbwarden classifies plugins before loading them (classify-before-load).
- Plugins register through `setup(registry)`, not import-time side effects.
- Object plugins use public ordering anchors, not private statement-order integers.

## Example

```bash
dbwarden plugin add dbwarden-fastapi
dbwarden plugin list
```

Then use the integration exposed by the plugin according to its documentation.

## Next Steps

- Start with the [quickstart](quickstart.md).
- Learn how to [install plugins](using-plugins/installing.md).
- Read the [trust model](using-plugins/consent-and-trust.md).
- Build your first plugin with the [developer overview](developing/overview.md).
