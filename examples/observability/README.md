# dbwarden Observability

Demonstrates Prometheus metrics, structured logging, and query tracing with dbwarden.

## Prerequisites

- Docker (for PostgreSQL, Prometheus, Grafana)
- Python 3.12+

## Quick Start

```bash
uv add "dbwarden[metrics]" sqlalchemy fastapi uvicorn asyncpg

# Start infrastructure (PostgreSQL, Prometheus, Grafana)
docker compose up -d

# Initialize DB, generate and apply migrations
bash run.sh

# Start the app
uvicorn app.main:app --reload
```

## What's Included

### Prometheus Metrics

- `dbwarden_migrations_total`: Migration count by status
- `dbwarden_migration_duration_seconds`: Migration execution time
- `dbwarden_schema_version`: Current schema version
- `dbwarden_pending_migrations`: Number of pending migrations
- `dbwarden_errors_total`: Error count by type

Available at `http://localhost:8000/metrics`.

### Query Tracing

The `QueryTracingMiddleware` logs every SQL query with duration:

```json
{"event": "query", "duration_ms": 42, "database": "primary"}
```

### Structured Logging

```bash
DBWARDEN_LOG_JSON=1 uvicorn app.main:app
```

Produces JSON-formatted logs for log aggregators (ELK, Datadog, etc.).

### Grafana

Open http://localhost:3000, add Prometheus data source (http://prometheus:9090), and import the dbwarden dashboard.
