---
description: Publish a DBWarden plugin package.
---

# Publishing Plugins

## Starting From The Template

Use the [`dbwarden-plugin-template`](https://github.com/dbwarden-org/dbwarden-plugin-template) GitHub template repository: click **Use this template** to create your own repo, then rename the placeholders:

```bash
python bootstrap.py dbwarden-yourname
```

`bootstrap.py` applies the naming rule below, renames the package and test, and removes itself. The template is a working value plugin with a passing test and CI; adapt it to your hooks.

Every plugin uses the same shape: a setuptools `src/` layout, `setup(registrar)` defined at the bottom of the package `__init__.py`, and a single `dbwarden.plugins` entry point.

```text
dbwarden-example/
├── pyproject.toml
├── LICENSE
├── README.md
├── .gitignore
├── .github/workflows/test.yml
├── src/dbwarden_example/
│   └── __init__.py        # hook functions + setup(registrar)
└── tests/
    └── test_example_plugin.py
```

Object plugins add a `handler.py` next to `__init__.py` and import it lazily inside `setup()`. The official plugins are the reference implementations: [`dbwarden-fastapi`](https://github.com/dbwarden-org/dbwarden-fastapi) (value), [`dbwarden-pgsql-extensions`](https://github.com/dbwarden-org/dbwarden-pgsql-extensions) (object).

## Naming

All plugins, community and official alike, are distributed as `dbwarden-<name>`. The import package and entry-point key derive from the distribution name by one rule:

```
slug        = distribution_name.removeprefix("dbwarden-").replace("-", "_")
import pkg  = "dbwarden_" + slug
entry key   = slug
```

| Role | Convention | Example (`dbwarden-audit`) |
|------|------------|---------------------------|
| PyPI project | `dbwarden-<name>` | `dbwarden-audit` |
| Import package | `dbwarden_<slug>` | `dbwarden_audit` |
| Entry-point key | `<slug>` | `audit` |

The `dbwarden-` prefix is shared by every plugin. A plugin's trust tier is **not** inferred from its name; it comes from the curated Official and Verified lists in core (`dbwarden/_official.py`, `dbwarden/_verified.py`), and everything else is Community. Use DBWarden-owned names such as `dbwarden-fastapi` only for packages published by the DBWarden organization.

## PyPI Metadata

This mirrors what the official plugins ship (setuptools, `src/` layout, one entry point):

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "dbwarden-example"
version = "0.2.0"
description = "Example DBWarden plugin"
readme = "README.md"
requires-python = ">=3.12.7"
dependencies = ["dbwarden>=0.15.0"]

[project.entry-points."dbwarden.plugins"]
example = "dbwarden_example:setup"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

The entry point is `dbwarden_example:setup` because `setup` is defined in the package's `__init__.py`; there is no separate `plugin.py`. Add `[project.urls]`, `license`, and `keywords` if you want them; the official plugins keep their metadata minimal.

## Compatibility

Depend on DBWarden with a lower bound, as the official plugins do:

```toml
dependencies = ["dbwarden>=0.15.0"]
```

The plugin API is stable within the `0.x` series. To guarantee a core update never silently breaks your plugin, you can also cap the upper bound (`"dbwarden>=0.15.0,<1.0"`), though the official plugins currently pin only the lower bound.

## CI/CD Example

`.github/workflows/test.yml`:

```yaml
name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e . pytest
      - run: pytest -q
```

Link a green run of this workflow when you [submit for verification](verified-standard.md#submit-for-verification).

## Trusted Publishing

Official DBWarden plugins use provenance-backed publishing (PyPI Trusted Publishing via GitHub Actions OIDC), which is what `dbwarden plugin add` verifies at install time. Community plugins can (and should) adopt Trusted Publishing too, but on its own it does not make a plugin Official: the Official tier is the curated list in `dbwarden/_official.py`.
