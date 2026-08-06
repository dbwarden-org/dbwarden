# Codebase Organization

## Top-Level Layout

```
dbwarden/        # The package itself
tests/           # Test suite (~40 modules)
docs/            # Documentation site (MkDocs)
examples/        # Runnable example projects
scripts/         # Development and CI tooling
assets/          # Images, icons, branding
site/            # Built documentation output (gitignored)
```

## Package Layout (`dbwarden/`)

| Directory / Module | Responsibility |
|---|---|
| `cli/` | Typer CLI definitions, argument parsing |
| `commands/` | Command orchestration (migrate, generate-models, check, etc.) |
| `engine/` | Core logic: model discovery, snapshot extraction, diff, offline migration, safety checks |
| `database/` | Connection management, SQL queries by dialect |
| `databases/` | Concrete backend specs: ClickHouse, MySQL, PostgreSQL, MariaDB, SQLite |
| `schema/` | Dialect-agnostic metadata layer: table/column/field metadata classes |
| `repositories/` | Migration and lock metadata persistence |
| `fastapi/` | FastAPI integration (lifespan, health checks) |
| `config*.py` | Configuration loading and resolution |
| `constants.py` | Shared constants |
| `exceptions.py` | Exception hierarchy |
| `seed.py` | Seed data infrastructure |
| `sandbox.py` | Module loading sandbox for user model files |

## The `schema/` vs `databases/` Boundary

The `dbwarden/schema/` package is the abstract metadata layer. It defines dialect-agnostic constructs that make no assumptions about the target database:

- `TableMeta` and `*ColumnMeta` classes (e.g. `PGColumnMeta`, `CHColumnMeta`, `MyColumnMeta`)
- `DBWardenMeta`: the runtime metadata container attached to each model
- `_MetaValidator`: metaclass that validates `class Meta` attribute names at import time
- `IndexSpec`, `CheckSpec`, `UniqueSpec`: cross-database object specs
- `_meta_reader.py`: logic that reads `class Meta` from user models and populates `DBWardenMeta`

The `dbwarden/databases/` package is the concrete backend layer. It contains dialect-specific specs and helpers:

- `clickhouse/`: `ChEngineSpec`, `ProjectionSpec`, `ChIndexSpec`, `ChTableSpec`, merge-tree helpers, `ChFieldSpec`
- `mysql/`: `MyFieldSpec`, `MyTableSpec`
- `pgsql/`: `PgFieldSpec`, `PgIndexSpec`, `PgTableSpec`, exclude/partition helpers
- `mariadb/`: `MdbFieldSpec`, `MdbTableSpec`
- `sqlite/`: `SqFieldSpec`, `SqTableSpec`

### The Import Contract

The single most important rule in the codebase is:

> **`schema/` must never import from `databases/`.**

This keeps the metadata layer database-agnostic. `databases/` may import from `schema/` (and does, for `TableMeta`, `DBWardenMeta`, `IndexSpec`, etc.), but the reverse dependency is forbidden.

Consequences of this boundary:

- **`ChEngineSpec` and `ProjectionSpec` live in `databases/clickhouse/`**, not `schema/`. They are ClickHouse-specific types, not abstract schema concepts.
- Backend specs (`ChTableSpec`, `MyTableSpec`, etc.) are defined per-database, not in `schema/`.
- The `schema/__init__.py` only re-exports classes from `schema/` submodules. It does not re-export backend-specific types from `databases/`.
- Users import backend types through `from dbwarden.databases.clickhouse import ChEngineSpec` or the top-level `from dbwarden import ChEngineSpec`.

### What Changed in the v0.13.0 Refactor

The refactor tightened this boundary. Previously, `ChEngineSpec`, `ProjectionSpec`, and the `*FieldMeta` hierarchy lived in `schema/`. They were moved to their correct locations:

- `ChEngineSpec`, `_split_engine_args`: now in `databases/clickhouse/engine.py`
- `ProjectionSpec`: now in `databases/clickhouse/projection.py`
- `*FieldMeta` classes (`PGFieldMeta`, `CHFieldMeta`, etc.): deleted; fields inlined directly into `*ColumnMeta` in `table_meta.py`

The orphan `__pycache__` directories under `schema/{clickhouse,mysql,pgsql,mariadb,sqlite}/` were removed.

## Contribution Guidelines

### Before submitting a PR

1. Ensure your changes respect the `schema/` vs `databases/` import boundary (see above).
2. Run the full test suite before pushing:
   ```
   python -m pytest tests/ -x -q
   ```
3. If you add or remove a public export, update the corresponding `__all__` list in the module's `__init__.py`.
4. If you introduce a new top-level directory, add it to the table in this document.

### Code style

- No comments in production code unless the logic is genuinely subtle.
- Mimic existing patterns: same typing style, same docstring conventions, same import organization.
- Prefer `from __future__ import annotations` at the top of every module.
- Use `metaclass=_MetaValidator` for any new `class Meta`-like user-facing configuration class.

### Adding a new database backend

1. Create a new subpackage under `databases/<name>/` with `__init__.py`, `field.py`, and any backend-specific specs.
2. Define a `*TableSpec` dataclass and a `*FieldSpec` dataclass matching the existing backends.
3. Register the backend in `databases/__init__.py` and add the shortcut import (`sq`, `my`, etc.).
4. If the backend needs no column-level `Meta` attributes (like SQLite), add no `*ColumnMeta` class.
5. Do not touch files in `schema/` unless you are adding cross-database metadata fields.
