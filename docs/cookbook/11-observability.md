---
seo:
  title: 11. Observability - DBWarden Documentation
  canonical: https://dbwarden.emiliano-go.com/cookbook/11-observability
  robots: index,follow
  og:
    type: website
    title: 11. Observability - DBWarden Documentation
    description: What You'll Learn
    url: https://dbwarden.emiliano-go.com/cookbook/11-observability
    image: https://dbwarden.emiliano-go.com/assets/images/og-image.png
    image:width: 1376
    image:height: 768
    image:alt: DBWarden documentation
    site_name: DBWarden Documentation
    locale: en_US
  twitter:
    card: summary_large_image
    title: 11. Observability - DBWarden Documentation
    description: What You'll Learn
    image: https://dbwarden.emiliano-go.com/assets/images/og-image.png
    image:alt: DBWarden documentation
    site: '@emiliano_go_'
  description: What You'll Learn
  schema_jsonld:
  - '@context': https://schema.org
    '@type': WebPage
    name: 11. Observability - DBWarden Documentation
    url: https://dbwarden.emiliano-go.com/cookbook/11-observability
    description: What You'll Learn
    image: https://dbwarden.emiliano-go.com/assets/images/og-image.png
    publisher:
      '@type': Organization
      name: Emiliano Gandini Outeda
      logo: https://dbwarden.emiliano-go.com/assets/images/og-image.png
  - '@context': https://schema.org
    '@type': BreadcrumbList
    itemListElement:
    - '@type': ListItem
      position: 1
      name: Cookbook & Examples
      item: https://dbwarden.emiliano-go.com/cookbook
    - '@type': ListItem
      position: 2
      name: 11 Observability
      item: https://dbwarden.emiliano-go.com/cookbook/11-observability
seo_html: "<title>11. Observability - DBWarden Documentation</title>\n<meta name=\"\
  description\" content=\"What You&#x27;ll Learn\">\n<link rel=\"canonical\" href=\"\
  https://dbwarden.emiliano-go.com/cookbook/11-observability\">\n<meta name=\"robots\"\
  \ content=\"index,follow\">\n<meta property=\"og:type\" content=\"website\">\n<meta\
  \ property=\"og:title\" content=\"11. Observability - DBWarden Documentation\">\n\
  <meta property=\"og:description\" content=\"What You&#x27;ll Learn\">\n<meta property=\"\
  og:url\" content=\"https://dbwarden.emiliano-go.com/cookbook/11-observability\"\
  >\n<meta property=\"og:image\" content=\"https://dbwarden.emiliano-go.com/assets/images/og-image.png\"\
  >\n<meta property=\"og:image:width\" content=\"1376\">\n<meta property=\"og:image:height\"\
  \ content=\"768\">\n<meta property=\"og:image:alt\" content=\"DBWarden documentation\"\
  >\n<meta property=\"og:site_name\" content=\"DBWarden Documentation\">\n<meta property=\"\
  og:locale\" content=\"en_US\">\n<meta name=\"twitter:card\" content=\"summary_large_image\"\
  >\n<meta name=\"twitter:title\" content=\"11. Observability - DBWarden Documentation\"\
  >\n<meta name=\"twitter:description\" content=\"What You&#x27;ll Learn\">\n<meta\
  \ name=\"twitter:image\" content=\"https://dbwarden.emiliano-go.com/assets/images/og-image.png\"\
  >\n<meta name=\"twitter:image:alt\" content=\"DBWarden documentation\">\n<meta name=\"\
  twitter:site\" content=\"@emiliano_go_\">\n<script type=\"application/ld+json\"\
  >\n[\n  {\n    \"@context\": \"https://schema.org\",\n    \"@type\": \"WebPage\"\
  ,\n    \"name\": \"11. Observability - DBWarden Documentation\",\n    \"url\": \"\
  https://dbwarden.emiliano-go.com/cookbook/11-observability\",\n    \"description\"\
  : \"What You'll Learn\",\n    \"image\": \"https://dbwarden.emiliano-go.com/assets/images/og-image.png\"\
  ,\n    \"publisher\": {\n      \"@type\": \"Organization\",\n      \"name\": \"\
  Emiliano Gandini Outeda\",\n      \"logo\": \"https://dbwarden.emiliano-go.com/assets/images/og-image.png\"\
  \n    }\n  },\n  {\n    \"@context\": \"https://schema.org\",\n    \"@type\": \"\
  BreadcrumbList\",\n    \"itemListElement\": [\n      {\n        \"@type\": \"ListItem\"\
  ,\n        \"position\": 1,\n        \"name\": \"Cookbook & Examples\",\n      \
  \  \"item\": \"https://dbwarden.emiliano-go.com/cookbook\"\n      },\n      {\n\
  \        \"@type\": \"ListItem\",\n        \"position\": 2,\n        \"name\": \"\
  11 Observability\",\n        \"item\": \"https://dbwarden.emiliano-go.com/cookbook/11-observability\"\
  \n      }\n    ]\n  }\n]\n</script>\n"
---

# 11. Observability

## What You'll Learn

- How to enable Prometheus metrics for DBWarden
- How to use structured JSON logging
- How to add query tracing middleware to FastAPI
- How to monitor connection pool health

## Prerequisites

- `examples/observability/` directory
- Docker (for optional Prometheus + Grafana)

## Step 1: Enable Prometheus Metrics

Install with metrics support:

```bash
uv add "dbwarden[metrics]"
dbwarden plugin add dbwarden-fastapi
```

DBWarden exposes six Prometheus metric families:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `dbwarden_migrations_total` | Counter | `database`, `status` | Migration count |
| `dbwarden_migration_duration_seconds` | Histogram | `database` | Execution time |
| `dbwarden_schema_version` | Gauge | `database` | Current version |
| `dbwarden_pending_migrations` | Gauge | `database` | Pending count |
| `dbwarden_errors_total` | Counter | `database`, `error_type` | Error count |
| `dbwarden_seed_version` | Gauge | `database` | Current seed version |

## Step 2: Add Metrics to FastAPI

```python
from dbwarden.extensions.fastapi import MetricsMiddleware, MetricsRouter

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

## Step 4: Query Tracing

```python
from dbwarden.extensions.fastapi import QueryTracingMiddleware

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
from dbwarden.extensions.fastapi import PoolMetricsCollector
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
from dbwarden.extensions.fastapi import (
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
    title="DBWarden Observability Example",
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
- `DBWARDEN_LOG_JSON=1` switches to structured JSON logging
- `QueryTracingMiddleware` logs every SQL query with duration
- `PoolMetricsCollector` monitors connection pool health
- Metrics are compatible with standard Prometheus + Grafana setup

## Related Documentation

- [Observability Guide](../observability.md)
- [FastAPI Metrics](../fastapi/tutorial/first-steps.md) (see FastAPI tutorial)
