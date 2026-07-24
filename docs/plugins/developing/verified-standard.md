---
description: Verified plugin test standard and review process.
---

# Verified Plugin Standard

The **Verified** tier earns a plugin the **Verified** badge: it loads without per-user consent once its installed version meets the verified minimum. To qualify, the plugin must pass a focused, automatable conformance suite that proves it respects the DBWarden contract, does not compromise the user's environment, and (for object plugins) integrates correctly with the diff pipeline.

The suite is deliberately minimal. It does **not** review business logic, performance, or security; it ensures the plugin is a well-behaved citizen.

!!! warning "Verification is not a security audit"
    The Verified badge is a statement of contract adherence, not fitness for purpose. It says the plugin respects DBWarden's rules, will not break core, and will not execute code before you consent. It does **not** certify the plugin is free of vulnerabilities or malicious behavior, and it grants no sandboxing. A loaded plugin runs with full process privileges. Vet the source yourself for anything sensitive.

## The Conformance Harness

DBWarden ships the checks as `dbwarden.plugin_conformance`, so verification is reproducible rather than a one-time manual event. Each function raises `ConformanceError` with an explanation on failure. The [plugin template](publishing.md#starting-from-the-template) already wires them into `tests/test_conformance.py`; copy that file and adjust the distribution/package names.

## Required Tests

| # | Test | Harness call | Protects against |
|---|------|--------------|------------------|
| 1 | `test_entry_point_is_declared` | `assert_entry_point_declared(dist)` | Misconfigured packaging, missing/unresolvable entry point, a plugin that can never be discovered. |
| 2 | `test_import_has_no_side_effects` | `assert_import_has_no_side_effects(pkg)` | The #1 security-model violation: registering at import time bypasses the consent gate. Enforces classify-before-load. |
| 3 | `test_setup_registers_hooks` | `assert_setup_registers(setup, value_hooks=...)` | A broken `setup()` (wrong signature, swallowed exception, forgot to register) that installs but does nothing. |
| 4 | `test_hook_signature_compliance` | `assert_hook_signatures(setup)` | Runtime `TypeError` when core calls a hook with the documented arguments; caught at build time instead of migration time. |
| 5 | `test_core_imports_resolve` | `assert_core_imports_resolve(pkg)` | A plugin importing a DBWarden module the installed core does not have, which would otherwise fail on a user's machine instead of in CI. |
| 6 | `test_api_version_is_declared` | `assert_api_version_declared(pkg)` | A plugin with no declared contract version, which silently keeps loading after the contract changes under it. |
| 7 | `test_object_handler_conformance` (object plugins) | `assert_object_handler_conformance(handler, config=...)` | Handlers that crash the diff pipeline or emit malformed statements: `extract`/`canonicalize`/`diff`/`emit` must return the right types. |
| 8 | `test_ordering_constraint_satisfiable` (object plugins) | `assert_ordering_constraint_satisfiable(handler)` | Ordering constraints that can never be satisfied (impossible anchor pair, unknown/cyclic object references) and crash generation. |
| 9 | `test_idempotent_setup` (recommended) | `assert_idempotent_setup(setup)` | Double-loading in reloads/tests/interactive sessions: calling `setup()` twice must not raise or add new registrations. |

Tests 1 to 6 apply to every plugin. Tests 7 and 8 apply to object plugins; value-only plugins mark them not applicable and say so. Test 9 is recommended and may become required.

### What the harness checks for test 5

**Plugins may import anything from DBWarden core.** There is no allowlist. A plugin that needs `dbwarden.output` for consistent CLI rendering, or `dbwarden.repositories.*` to read tracking tables, should just import them.

What test 5 verifies is that every `dbwarden` module you import actually exists in the core you are built against. That catches the failure that actually bites users: a plugin pinned to `dbwarden>=0.15` importing a module that moved in 0.16, discovered at load time on their machine rather than in your CI.

Three surfaces are documented as stable and change only on a major version:

- `dbwarden.plugin` (`PluginRegistrar`, the registries, hook errors)
- `dbwarden.exceptions`
- `dbwarden.engine.core`, including `dbwarden.engine.core.plugin_api`

Building on those means less to revisit when core releases. Anything deeper is fair game, but pin your `dbwarden` dependency accordingly and keep CI running against the versions you claim to support. `plugin_conformance.core_imports_outside_stable_api(pkg)` lists your deeper imports so you know what to re-check on an upgrade; it reports, it does not fail.

## What This Does Not Cover

- **Business logic**: whether the emitted SQL does what the user wants (their own tests).
- **Performance**: no benchmark required.
- **Security audit**: no static analysis for malicious code beyond the import restriction.
- **Inter-plugin interaction**: the resolver's job.

## Submit For Verification

Open an issue in the DBWarden repository using the **Plugin Verification** template. It asks for the repository, the distribution name and requested minimum version, what the plugin provides, the core versions you test against, a link to a green conformance run, and two things worth explaining here.

### The plugin API version

The plugin contract is versioned separately from DBWarden's release version, as `dbwarden.plugin.PLUGIN_API_VERSION`. Declare the one you target on your package:

```python
DBWARDEN_PLUGIN_API = 1
```

Core refuses to load a plugin declaring a version it does not provide, reporting it as `incompatible` with both versions named, rather than letting it register and generate migrations under assumptions that no longer hold. Declaring nothing means "version 1", so plugins written before the contract was versioned keep working; the conformance suite still requires the declaration, because a plugin that never declares gets no protection when the contract moves.

The version changes only when a plugin could otherwise be *wrong* rather than loudly broken: a hook signature change, different semantics for a registered handler, a rename on the stable surface. Adding a hook or a new `plugin_api` helper does not change it.

### Declaring deep imports

The template asks you to paste the output of:

```bash
python -c "from dbwarden.plugin_conformance import core_imports_outside_stable_api as f; print('\n'.join(f('your_package')) or 'none')"
```

and justify each line. This is a review conversation, not a test: no automated check can tell a good reason from a bad one, which is why it belongs to a reviewer.

It also runs in your favour. A justification that generalises is an argument that the stable surface is missing something, and the outcome is usually that the helper becomes public in `dbwarden.engine.core.plugin_api` rather than that you are told to stop. That is exactly how `plugin_api` acquired `quote_pg`, `qualified_name`, the grant and policy builders, and `emit_with_cluster`: official plugins needed them, so they stopped being internal. If you would switch to a public equivalent, say so.

### Declaring object type overrides

A plugin handler that claims an `object_type` core already handles **replaces** core for that type: migration DDL for those objects comes from the plugin. DBWarden logs a warning when this happens, but a reviewer needs to know up front. Declare any overrides and explain why replacing core is right rather than adding a new type. Overriding is legitimate (it is how official plugins supersede built-in fallbacks) and it raises the review bar.

## CI Enforcement

To be Verified, the plugin must run the conformance suite in public CI (GitHub Actions or similar) on every push, with a visible status badge. The template's `.github/workflows/test.yml` does this. The reviewer confirms CI is green and that `tests/test_conformance.py` matches the standard, then does a quick sanity pass for obviously unsafe behavior (malware, core monkey-patching, network exfiltration). On verification, the distribution name and minimum version are added to `dbwarden/_verified.py` and ship in the next DBWarden release.

## Version Re-Verification

The verified minimum is a **floor**: patch and minor releases above it load automatically. A **new major version** (or any release that changes hooks, signatures, or object semantics) must request re-verification, and the floor in `dbwarden/_verified.py` is updated accordingly. Until then, a version below the recorded floor is treated as **Community** and requires consent.
