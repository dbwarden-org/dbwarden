# Multi-Database: PostgreSQL + ClickHouse

Demonstrates managing two databases in a single project: PostgreSQL for transactional data and ClickHouse for analytics.

## Prerequisites

- Docker (for PostgreSQL and ClickHouse containers)
- Python 3.12+
- `uv add dbwarden sqlalchemy clickhouse-connect`

## Quick Start

```bash
# Run everything with one command
bash run.sh

# Or step by step:
docker compose up -d
dbwarden init
dbwarden make-migrations "add user table" --database primary
dbwarden make-migrations "add events table" --database analytics
dbwarden migrate --all
dbwarden status --all
```

## Key Concepts

- Each declarative database class gets its own migration directory under `migrations/` (the equivalent `database_config()` function form is also supported)
- `--database primary` / `--database analytics` targets a specific database
- `--all` targets every configured database
- Each database has independent migration history and lock table
- `model_paths` must be non-overlapping (or use `overlap_models=True`)
