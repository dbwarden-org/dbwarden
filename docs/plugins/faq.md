---
description: Frequently asked questions about dbwarden plugins.
---

# Plugin FAQ

## Can I Use A Plugin Without The CLI?

Yes. Install it with `uv add` or `pip install`. dbwarden still applies trust rules when loading it, so community plugins installed this way still require consent (`dbwarden plugin trust <name>` or the interactive prompt).

## What Happens If An Official Plugin's Provenance Fails?

Installation aborts and nothing is installed. Official plugin install is fail-closed: if provenance cannot be verified, dbwarden refuses rather than installing unverified code.

## How Do I Know A Community Plugin Is Safe?

There is no automatic guarantee. Review the source, prefer Verified plugins, and only consent to plugins you have vetted yourself. Trust gates whether a plugin loads, not what it can do once loaded.

## Can Two Plugins Provide The Same Hook?

For **multi** hooks (`health_routes`, `migration_routes`), yes: core collects all providers. For **single** hooks, no: a second provider causes a `HookConflictError` when the hook runs. Two plugins registering the same object handler `object_type` raise `ObjectHandlerConflictError`.

## Can Plugins Depend On Each Other?

Yes. Use normal Python package dependencies. Object handlers from different plugins are ordered relative to each other with `OrderingConstraint` (`after_object` / `before_object`).

## Do Plugins Work In CI?

Yes. Install them as project dependencies. Use `--format json` on `plugin list` and `plugin info` for machine-readable output; Rich tables degrade gracefully when output is captured. Note that non-interactive runs never auto-consent, so trust community plugins ahead of time (commit `.dbwarden/consent.toml`).

## How Do I Get My Plugin Verified?

Follow the [Verified Plugin Standard](developing/verified-standard.md): pass the seven mandatory tests and open a review issue in the dbwarden repository.

## What Happens When I Upgrade A Community Plugin?

Consent is version-specific. After upgrading, the recorded consent no longer matches the new version, so the plugin is treated as unconsented until you run `dbwarden plugin trust <name>` again.
