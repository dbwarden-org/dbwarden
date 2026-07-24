---
description: Install DBWarden plugins with the CLI, uv, or pip.
---

# Installing Plugins

## DBWarden CLI

```bash
dbwarden plugin add <distribution-name>
```

What `plugin add` does depends on the [trust tier](consent-and-trust.md) of the distribution:

- **Official**: DBWarden verifies provenance first. On success it installs the package and writes an entry to `.dbwarden/plugins.lock` recording the version, filename, SHA-256, and verifying identity. If provenance cannot be verified, installation aborts and nothing is installed (fail-closed).
- **Verified**: installed like any package; it will load automatically once the installed version meets the verified minimum.
- **Community**: the package is installed but **not** trusted. You must run `dbwarden plugin trust <name>` (or accept the interactive consent prompt) before DBWarden loads it.

```text
              Plugin Installed
  Installed community plugin 'dbwarden-example'.
Run `dbwarden plugin trust dbwarden-example` to allow it to load.
```

### Flags

| Flag | Applies to | Effect |
|------|------------|--------|
| `--uv` | `add`, `remove` | Use `uv add` / `uv remove` instead of pip. |
| `--version <v>` | `add` | Pin an exact version to install (`dist==<v>`). |
| `--dry-run` | `add`, `remove` | Print the plan (tier, installer command, post-install behavior) without changing anything. |

```bash
dbwarden plugin add dbwarden-fastapi --version 0.2.0 --uv
dbwarden plugin add dbwarden-fastapi --dry-run
```

## uv Or pip

You can install plugins directly without the CLI:

```bash
uv add dbwarden-fastapi
```

```bash
pip install dbwarden-fastapi
```

DBWarden still applies its trust model when loading. Community plugins installed this way still require consent, and Official plugins installed this way are **not** provenance-locked (no `.dbwarden/plugins.lock` entry is written unless you use `plugin add`).

## Version Pinning

Pin versions in your project dependencies for repeatable environments:

```bash
uv add "dbwarden-fastapi==0.2.0"
```

Community consent is version-specific: upgrading a community plugin invalidates prior consent and requires consent for the new version.

## What Happens During Official Install

Provenance verification uses PyPI's [PEP 740](https://peps.python.org/pep-0740/) attestations. DBWarden:

1. Looks up the distribution in `OFFICIAL_PLUGINS` for its expected GitHub repository and publishing workflow.
2. Resolves the target version on PyPI (the pinned/`--version` release, or the highest stable release) and selects its distribution file and SHA-256.
3. Fetches that file's attestation from PyPI's Integrity API and checks that the recorded Trusted-Publishing publisher is the expected GitHub repository and workflow, and that the attestation covers the file's exact digest.
4. On success, installs that **exact** version and writes a lockfile entry with `verified = "provenance"`, the identity, filename, and SHA-256.

Any missing attestation, publisher mismatch, digest mismatch, or network error makes the install **fail closed**: nothing is installed. Trust is anchored in PyPI's server-side attestation verification and TLS, the same root pip relies on.

## Updating Plugins

Update with your package manager, then reconcile DBWarden's view:

```bash
uv add "dbwarden-fastapi@latest"
dbwarden plugin list
dbwarden plugin info dbwarden-fastapi
```

For community plugins, re-run `dbwarden plugin trust <name>` after upgrading so consent matches the new version.

## Removing Plugins

```bash
dbwarden plugin remove <distribution-name>
```

This uninstalls the distribution and removes its consent entry and lockfile record. Use `--dry-run` to preview or `--uv` to uninstall via `uv remove`.
