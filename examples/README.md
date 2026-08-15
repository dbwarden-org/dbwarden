# dbwarden Examples

Runnable example projects demonstrating dbwarden workflows.
Each directory maps to one or more sections in the cookbook docs
at [`docs/cookbook/`](../docs/cookbook/index.md).

## Getting Started

The quickest path through all core concepts:

```bash
cd core
uv add dbwarden sqlalchemy
bash scripts/01-setup.sh
bash scripts/02-models-migrations.sh
bash scripts/03-apply-inspect.sh
```

## Example Index

| Directory | Sections | What It Covers | Requires |
|-----------|----------|----------------|----------|
| `core/` | 1–7 | Full SQL workflow: setup, models, migrations, apply, rollback, inspect, offline, safety, seeds | Nothing (SQLite) |
| `multi-database/` | 8 | PostgreSQL + ClickHouse in one project with separate model paths and migration directories | Docker |
| `fastapi-app/` | 9–10 | FastAPI integration + auto-generated Pydantic schemas via `@auto_schema` (requires `dbwarden-fastapi` plugin) | Docker (PostgreSQL) |
| `observability/` | 11 | Prometheus metrics, structured JSON logging, query tracing middleware | Docker |

## How These Examples Work

Each example is self-contained:

1. **`dbwarden.py`**: configuration file that registers database targets.
   The CLI discovers it automatically when you run commands from that directory.

2. **`app/models.py`**: SQLAlchemy model definitions with optional
   `class Meta(TableMeta)` annotations for comments, indexes, and checks.

3. **Shell scripts**: runnable command sequences that demonstrate the
   CLI workflow step by step.  Each script includes detailed comments
   explaining what each command does behind the scenes.

4. **Docker-based examples** (`multi-database`, `fastapi-app`, `observability`)
   include a `docker-compose.yml` or `run.sh` to start the required services.

## Detailed Walkthroughs

See [`docs/cookbook/index.md`](../docs/cookbook/index.md) for the full
chapter-by-chapter guide with annotated SQL output, CLI flags, and
expected output.
