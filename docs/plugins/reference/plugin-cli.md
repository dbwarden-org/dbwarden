---
description: Reference for dbwarden plugin CLI commands.
---

# Plugin CLI

All commands live under `dbwarden plugin`. Unlike other dbwarden commands, `dbwarden plugin` does not auto-load plugins first, so it can inspect and manage plugins without importing them. Management and inspection do not import plugins by default. `categories` loads trusted plugins without prompting; `list` and `info` do so only with `--load`.

## Subcommands

| Command | Purpose |
|---------|---------|
| `list` | Show discovered plugins as a table or JSON. |
| `info <name>` | Show one plugin's metadata and provenance. |
| `add <name>` | Install a plugin (provenance-verified for official). |
| `remove <name>` | Uninstall a plugin and clean up its consent/lock state. |
| `trust <name>` | Record consent for a community plugin's installed version. |
| `untrust <name>` | Revoke consent for a community plugin. |
| `categories` | List built-in and loaded plugin migration groups, order, and owner. |

## Flags

| Command | Flag | Effect |
|---------|------|--------|
| `list`, `info`, `categories` | `--format`, `-f` `table\|json` | Output format (default `table`). |
| `list`, `info` | `--load` | Load trusted plugins without prompting to inspect registrations. |
| `add` | `--uv` | Install with `uv add` instead of pip. |
| `add` | `--version <v>` | Pin an exact version (`dist==<v>`). |
| `add` | `--dry-run` | Print the install plan without installing. |
| `remove` | `--uv` | Uninstall with `uv remove` instead of pip. |
| `remove` | `--dry-run` | Print the uninstall plan without uninstalling. |

## list

```bash
dbwarden plugin list
dbwarden plugin list --format json
```

Columns: Distribution, Version, Tier, Trusted, State, Hooks, Objects, Lock.

```text
                                     dbwarden Plugins
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Distribution     ┃ Version ┃ Tier     ┃ Trusted ┃ State  ┃ Hooks           ┃ Objects ┃ Lock       ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━┩
│ dbwarden-fastapi │ 0.1.0   │ official │ yes     │ loaded │ health_routes,  │ -       │ provenance │
│                  │         │          │         │        │ session_factory │         │            │
└──────────────────┴─────────┴──────────┴─────────┴────────┴─────────────────┴─────────┴────────────┘
```

## info

```bash
dbwarden plugin info dbwarden-fastapi
dbwarden plugin info dbwarden-fastapi --format json
```

Shows entry point, tier, trust/load state, registered hooks and object handlers, official repository, verified minimum version, and lockfile provenance fields when present. Exits `1` if the plugin is not found.

## add

```bash
dbwarden plugin add dbwarden-fastapi
dbwarden plugin add dbwarden-fastapi --version 0.2.0 --uv
dbwarden plugin add dbwarden-example --dry-run
```

Official plugin installation verifies provenance and fails closed when verification is unavailable. Community plugins are installed but not trusted; the command prints the follow-up `trust` hint. `--dry-run` prints tier, installer command, and post-install behavior without installing.

## remove

```bash
dbwarden plugin remove dbwarden-example
dbwarden plugin remove dbwarden-example --dry-run --uv
```

Uninstalls the distribution and removes its consent entry and lockfile record.

## trust / untrust

```bash
dbwarden plugin trust dbwarden-example
dbwarden plugin untrust dbwarden-example
```

`trust` records consent for the currently installed version in `.dbwarden/consent.toml`. `untrust` removes it.

## categories

```bash
dbwarden plugin categories --format json
dbwarden plugin info dbwarden-example --load --format json
```

`list` and `info` JSON include `migration_categories`, containing each registered category's `name`, `order`, and `plugin`. Use `--load` to populate these registrations in a fresh CLI process. `info` also displays category names in its table.

Categories are named migration groups; safety levels remain SAFE/INFO/WARN/CRITICAL. See [object plugin categories](../developing/object-plugins.md#safety-and-migration-categories).

## File Formats

### Consent file: `.dbwarden/consent.toml`

Written by `trust` and by accepting the interactive prompt. Consent is version-specific.

```toml
[consent."dbwarden-example"]
version = "0.1.0"
consented_at = "2026-07-22T14:03:11.482915+00:00"
```

### Lockfile: `.dbwarden/plugins.lock`

Written by `plugin add` for official (provenance-verified) installs. The section key is the distribution name with the `dbwarden-` prefix stripped and dashes replaced by underscores (for example `dbwarden-pgsql-extensions` becomes `pgsql_extensions`).

```toml
[pgsql_extensions]
distribution = "dbwarden-pgsql-extensions"
version = "0.3.0"
filename = "dbwarden_pgsql_extensions-0.3.0-py3-none-any.whl"
sha256 = "abc123..."
tier = "official"
verified = "provenance"
identity = "https://github.com/dbwarden-org/dbwarden-pgsql-extensions"
installed_at = "2026-07-22T14:05:40.001122+00:00"
```

Commit both files so teammates and CI inherit your trust and provenance decisions.
