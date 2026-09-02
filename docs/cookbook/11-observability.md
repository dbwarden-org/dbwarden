# 11. Observability

## What You'll Learn

- How to enable Prometheus metrics for dbwarden
- How to use structured JSON logging
- How to add query tracing middleware to FastAPI
- How to monitor connection pool health

## Prerequisites

- `examples/observability/` directory
- Docker (for optional Prometheus + Grafana)

## Step 1: Enable Prometheus Metrics

Install with metrics support:

```bash
pip install prometheus-client
dbwarden plugin add dbwarden-fastapi
```

dbwarden exposes Prometheus metric families:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `dbwarden_migrations_total` | Counter | `database`, `version`, `success` | Migration count |
| `dbwarden_migration_duration_seconds` | Histogram | `database`, `version` | Execution time |
| `dbwarden_schema_version` | Gauge | `database` | Current version |
| `dbwarden_migrations_pending` | Gauge | `database` | Pending count |
| `dbwarden_migration_errors_total` | Counter | `database` | Error count |
| `dbwarden_seed_version` | Gauge | `database` | Current seed version |

## Step 2: Add Metrics to FastAPI

```python
from dbwarden_fastapi import MetricsMiddleware, MetricsRouter

# Middleware captures request duration and counts
app.add_middleware(MetricsMiddleware)

# Router exposes /metrics endpoint
app.include_router(MetricsRouter(), prefix="/metrics")
```

```bash
curl http://localhost:8000/metrics
```

Output:

```
# HELP dbwarden_migrations_total Total number of migrations
# TYPE dbwarden_migrations_total counter
dbwarden_migrations_total{database="primary",status="applied"} 5

# HELP dbwarden_pending_migrations Number of pending migrations
# TYPE dbwarden_pending_migrations gauge
dbwarden_pending_migrations{database="primary"} 0
```

## Step 3: Structured Logging

```python
import os
os.environ["DBWARDEN_LOG_JSON"] = "1"
```

Or via environment variable:

```bash
DBWARDEN_LOG_JSON=1 uvicorn app.main:app
```

This switches from colored human-readable output to JSON:

```json
{"timestamp": "2025-01-15T10:30:00Z", "level": "INFO", "event": "migration_applied", "database": "primary", "duration_ms": 42, "version": "0005"}
```

JSON logs are easier to ingest into ELK, Datadog, or other log aggregators.

For CLI runs, the global `--json` flag produces the same JSON-formatted logs and
also switches display commands to structured output:

```bash
dbwarden --json status --database primary
dbwarden --json migrate --database primary
```

### TRACE logging

`--debug-level trace` logs every SQL statement executed by `migrate`,
`rollback`, and `downgrade`:

```bash
dbwarden --debug-level trace migrate --database primary
```

Each statement is logged as `SQL (2.1ms): CREATE TABLE ...`.

### Migration phase timing

Phase timings (lock acquisition, snapshot write, model state write) are logged
at INFO by default. Add `--perf` for a per-SQL-statement breakdown:

```bash
dbwarden migrate --perf --database primary
```

## Step 4: Query Tracing

```python
from dbwarden_fastapi import QueryTracingMiddleware

app.add_middleware(QueryTracingMiddleware)
```

This logs every SQL query with its duration:

```json
{"event": "query", "duration_ms": 3, "database": "primary", "statement": "SELECT ..."}
```

Useful for:
- Identifying slow queries in development
- Building a query performance baseline
- Debugging N+1 query patterns

## Step 5: Pool Metrics Collector

```python
from dbwarden_fastapi import PoolMetricsCollector
```

This monitors SQLAlchemy connection pool health and exposes:

- Pool size (current/total)
- Connections in use
- Connections overflow
- Pool timeouts

## Step 6: Full Setup

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from dbwarden_fastapi import (
    DBWardenHealthRouter,
    dbwarden_lifespan,
    MetricsMiddleware,
    MetricsRouter,
    QueryTracingMiddleware,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with dbwarden_lifespan(app, mode="check"):
        yield


app = FastAPI(
    title="dbwarden Observability Example",
    lifespan=lifespan,
)

app.add_middleware(QueryTracingMiddleware)
app.add_middleware(MetricsMiddleware)
app.include_router(MetricsRouter(), prefix="/metrics")
app.include_router(DBWardenHealthRouter(), prefix="/health")
```

## Step 7: Prometheus + Grafana (Optional)

```yaml
services:
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
```

```bash
docker compose up -d
```

- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000

In Grafana, add Prometheus data source (`http://prometheus:9090`) and create dashboards using the `dbwarden_*` metrics.

## Key Takeaways

- Metrics are opt-in via `uv add "dbwarden[metrics]"`
- Six metric families cover migration, schema, and error tracking
- `DBWARDEN_LOG_JSON=1` (or the global `--json` flag) switches to structured JSON logging
- `--debug-level trace` logs every SQL statement during migration commands
- `--perf` adds per-statement timing breakdowns to migration commands
- `QueryTracingMiddleware` logs every SQL query with duration
- `PoolMetricsCollector` monitors connection pool health
- Metrics are compatible with standard Prometheus + Grafana setup

## Related Documentation

- [Observability Guide](../observability.md)

The FastAPI metrics middleware and routers are part of the `dbwarden-fastapi` plugin, documented in its own repository: [dbwarden-fastapi](https://github.com/dbwarden-org/dbwarden-fastapi).
