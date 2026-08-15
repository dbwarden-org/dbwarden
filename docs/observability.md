# Observability

dbwarden provides Prometheus metrics and structured JSON logging for monitoring and debugging.

## Prometheus metrics

### Installation

Install the optional metrics dependency:

```bash
uv add "dbwarden[metrics]"
```

This installs `prometheus-client` which is required for metric collection and exposition.

### Enabling metrics

Set the `DBWARDEN_METRICS` environment variable to `true`:

```bash
export DBWARDEN_METRICS=true
```

When enabled, dbwarden instruments the `migrate` and `seed apply` commands with Prometheus metric recording. When disabled (or when `prometheus_client` is not installed), all metric functions are safe no-ops.

### Available metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `dbwarden_migrations_total` | Counter | `database`, `version` | Total migrations applied |
| `dbwarden_migration_duration_seconds` | Histogram | `database` | Duration of migration operations |
| `dbwarden_schema_version` | Gauge | `database` | Current schema version |
| `dbwarden_seed_version` | Gauge | `database` | Current seed version |
| `dbwarden_pending_migrations` | Gauge | `database` | Number of pending migrations |
| `dbwarden_migration_errors_total` | Counter | `database` | Total migration errors |

### FastAPI metrics endpoint

The `MetricsRouter` exposes a `GET /metrics` endpoint in Prometheus text format:

```python
from fastapi import FastAPI
from dbwarden_fastapi import MetricsRouter

app = FastAPI()
app.include_router(MetricsRouter(), prefix="/metrics")
```

The endpoint returns:

```
# HELP dbwarden_pending_migrations Number of pending migrations
# TYPE dbwarden_pending_migrations gauge
dbwarden_pending_migrations{database="primary"} 0
# HELP dbwarden_schema_version Current schema version
# TYPE dbwarden_schema_version gauge
dbwarden_schema_version{database="primary"} 5.0
```

Only active when `prometheus_client` is installed and `DBWARDEN_METRICS=true` is set. Returns 404 when disabled.

### MetricsMiddleware

The `MetricsMiddleware` is an ASGI middleware that refreshes pending-migration gauges on each HTTP request:

```python
from fastapi import FastAPI
from dbwarden_fastapi import MetricsMiddleware, MetricsRouter

app = FastAPI()
app.add_middleware(MetricsMiddleware)
app.include_router(MetricsRouter(), prefix="/metrics")
```

The middleware also records HTTP request duration via the migration duration histogram.

## JSON logging

dbwarden supports structured JSON logging for integration with log aggregation systems (ELK, Loki, Datadog, etc.).

### Enabling JSON logging

Set the `DBWARDEN_LOG_JSON` environment variable to `true`:

```bash
export DBWARDEN_LOG_JSON=true
```

For one-off CLI runs, the global `--json` flag has the same effect on the
command's log output (and also switches display commands to JSON):

```bash
dbwarden --json migrate --database primary
```

When enabled, all dbwarden log output uses newline-delimited JSON format:

```json
{"timestamp": "2025-06-01T10:00:00.123456", "level": "INFO", "logger": "dbwarden", "message": "Applying migration 0003", "db_name": "primary", "db_type": "postgresql"}
{"timestamp": "2025-06-01T10:00:01.234567", "level": "INFO", "logger": "dbwarden", "message": "Migration 0003 applied successfully", "db_name": "primary", "db_type": "postgresql"}
```

### JSON log fields

| Field | Description |
|-------|-------------|
| `timestamp` | ISO-8601 timestamp with microseconds |
| `level` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `logger` | Logger name |
| `message` | Log message text |
| `db_name` | Database name (when applicable) |
| `db_type` | Database type (when applicable) |
| `exception` | Exception traceback (when applicable) |

## TRACE logging

The `trace` level (numeric `5`, below `debug`) is enabled with
`--debug-level trace` and logs every SQL statement as it executes during
`migrate`, `rollback`, and `downgrade`:

```bash
dbwarden --debug-level trace migrate --database primary
```

Each statement is logged as `SQL (2.1ms): CREATE TABLE ...` and respects the
current log format (plain text or JSON).

## Migration phase timing

Migration commands always log phase timings at INFO level: lock acquisition,
schema snapshot write, and model state write. Add `--perf` to also log each
individual SQL statement with its duration:

```bash
dbwarden migrate --perf --database primary
```

```bash
dbwarden make-migrations "add column" --perf
```

## Environment variables reference

| Variable | Value | Effect |
|----------|-------|--------|
| `DBWARDEN_METRICS` | `true` | Enable Prometheus metric recording and exposition |
| `DBWARDEN_LOG_JSON` | `true` | Enable JSON-formatted log output |
| `DBWARDEN_MIGRATE_AUTH` | API key string | Require `X-API-Key` header for `POST /migrate` endpoint |
| `DBWARDEN_HEALTH_AUTH` | API key string | Require `X-API-Key` header for health endpoints |

See also: [Cookbook: Observability](../cookbook/11-observability.md)
