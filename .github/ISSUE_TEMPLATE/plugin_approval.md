---
name: Plugin Approval
about: Request the Approved (Verified) tier for a dbwarden plugin
labels: plugin-approval
---

<!--
Approved plugins load without per-user consent once the installed version meets
the approved minimum, so this is a request for users to trust your code by
default. Fill in every section; a reviewer works from this issue.

Standard: https://docs.dbwarden.org/plugins/developing/approved-standard/
-->

## Plugin repository

<!-- Public URL. The source must be readable without an account. -->

## Distribution name and requested minimum version

<!-- e.g. dbwarden-audit-log, minimum 0.2.0 -->

## What it provides

<!-- Value hooks and/or object types, one per line. -->

- Value hooks:
- Object types:

## Core versions tested

<!-- The `dbwarden` range your dependency declares AND your CI actually runs. -->

## Conformance evidence

<!-- Link a green CI run of tests/test_conformance.py. -->

## Core imports outside the stable API

Plugins may import anything from `dbwarden`. Three surfaces are stable and change
only on a major version: `dbwarden.plugin`, `dbwarden.exceptions`, and
`dbwarden.engine.core` (including `dbwarden.engine.core.plugin_api`). Anything
deeper moves faster, so a reviewer needs to see what you depend on.

Run this against your installed plugin and paste the output:

```bash
python -c "from dbwarden.plugin_conformance import core_imports_outside_stable_api as f; print('\n'.join(f('your_package')) or 'none')"
```

<!-- Paste output here. Then justify each line: why the stable surface did not
     cover it. "Needs dbwarden.output so command output matches the CLI" is a
     good answer. If a justification is good and general, we would rather add it
     to dbwarden.engine.core.plugin_api than have you keep reaching past it, so
     say if you would use a public equivalent. -->

| Import | Why | Would a public equivalent work? |
|---|---|---|
| | | |

## Object type overrides

<!-- Does any handler claim an object_type that dbwarden core already handles?
     A plugin handler overrides core for that type, so DDL for it comes from you
     instead. If yes, list the types and explain why replacing core is correct
     rather than adding a new type. If no, write "none". -->

## Maintenance

<!-- Who maintains this, and how do users report problems? -->

## Checklist

- [ ] Source is public and readable
- [ ] `tests/test_conformance.py` matches the standard and passes in public CI
- [ ] CI runs against every `dbwarden` version the dependency claims to support
- [ ] Deep-import table above is complete and justified
- [ ] Object type overrides declared (or "none")
- [ ] No network access at import or registration time
- [ ] No monkey-patching of `dbwarden` modules
