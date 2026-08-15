# Core dbwarden Workflow

A progressive example demonstrating the core dbwarden migration workflow.

## Prerequisites

- Python 3.12+
- `uv add dbwarden sqlalchemy psycopg2-binary`
- A running PostgreSQL instance (update `database_url_sync` in `dbwarden.py`)

## Quick Start

```bash
uv add dbwarden sqlalchemy psycopg2-binary
bash scripts/01-setup.sh
bash scripts/02-models-migrations.sh
bash scripts/03-apply-inspect.sh
```

## Script Index

| Script | Commands Demonstrated |
|--------|----------------------|
| `01-setup.sh` | `dbwarden init`, `dbwarden config` |
| `02-models-migrations.sh` | `make-migrations`, `new`, `make-rollback` |
| `03-apply-inspect.sh` | `migrate`, `rollback`, `downgrade`, `history`, `status`, `check`, `check-db` |
| `04-offline-ci.sh` | `export-models`, `make-migrations --offline` |
| `05-schema-inspection.sh` | `diff`, `snapshot`, `generate-models` |
| `06-safety-impact.sh` | `check`, `check-impact` |
| `07-seeds.sh` | `seed create`, `seed apply`, `seed rollback`, `seed list` |

Run scripts in order: each builds on the state left by the previous one.
