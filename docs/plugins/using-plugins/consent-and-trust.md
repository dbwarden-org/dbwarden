---
description: Understand plugin consent, trust tiers, and loading safety.
---

# Consent And Trust

dbwarden classifies every plugin entry point **before importing any plugin code**. Classification is by distribution name, so a plugin's tier is known without running it.

## The Three Tiers

| Tier | Loads automatically? | Basis of trust |
|------|----------------------|----------------|
| Official | Yes | Organization-owned name + provenance verified at install. |
| Verified | Yes, when version ≥ verified minimum | Community review against the test standard. |
| Community | No, consent required | Your explicit, version-specific consent. |

### Official

Official plugins are dbwarden-maintained. `plugin add` verifies provenance and **fails closed** when verification is unavailable or invalid. They load without consent.

### Verified

Verified plugins are community-maintained but reviewed against the dbwarden plugin standard. They load **without consent** only when the installed version is at or above the verified minimum recorded in `dbwarden/_verified.py`. If the installed version is below the floor, dbwarden logs a warning and treats the plugin as **Community** (consent required).

### Community

Community plugins require explicit consent before loading. In an interactive terminal, dbwarden prompts on first use:

```text
Enable community plugin 'dbwarden-example' version 0.1.0? [y/N]:
```

Accepting records consent. Non-interactive runs (CI, scripts) never auto-consent: an unconsented community plugin is skipped with a warning:

```text
Skipping community plugin 'dbwarden-example' (not consented).
Run `dbwarden plugin trust dbwarden-example` to enable it.
```

## Consent Commands

```bash
dbwarden plugin trust dbwarden-example      # record consent for the installed version
dbwarden plugin untrust dbwarden-example    # revoke consent
```

## The Consent File

Consent is stored per-project in `.dbwarden/consent.toml`:

```toml
[consent."dbwarden-example"]
version = "0.1.0"
consented_at = "2026-07-22T14:03:11.482915+00:00"
```

Consent is bound to the exact `version`. If you upgrade the plugin, the recorded version no longer matches and the plugin is treated as unconsented until you trust the new version. Commit this file to share consent decisions with your team.

## Security Boundary

Classify-before-load ensures untrusted community plugin code is **not imported** automatically: the security decision happens before `import`.

!!! warning "Not a sandbox"
    Trust gates *whether* a plugin loads, not *what it can do*. Once a plugin is loaded it runs with **full Python process privileges**: it can read files, open network connections, and call any API your process can. Consent is a statement that you have reviewed and trust the code, not a containment mechanism. Approval is likewise a review, **not a security audit**.

Verified and Official tiers exist to reduce how often you must make that trust decision manually, not to make loading arbitrary code safe.
