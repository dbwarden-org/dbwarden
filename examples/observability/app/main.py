from contextlib import asynccontextmanager
from fastapi import FastAPI
from dbwarden.fastapi import (
    DBWardenHealthRouter,
    dbwarden_lifespan,
    MetricsMiddleware,
    MetricsRouter,
    PoolMetricsCollector,
    QueryTracingMiddleware,
)

from app.models import Base


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with dbwarden_lifespan(app, mode="check"):
        yield


app = FastAPI(
    title="dbwarden Observability Example",
    lifespan=lifespan,
)

# ── Middleware order matters ────────────────────────────────────
# QueryTracingMiddleware logs every SQL query with its duration.
#   Output: {"event": "query", "duration_ms": 3, "database": "primary", ...}
# Useful for identifying slow queries, N+1 patterns, and building
# a query performance baseline.
#
# MetricsMiddleware (registered last, runs first in FastAPI's
# middleware stack) captures request duration and request count
# for Prometheus.  Together they give you both per-request and
# per-query observability.
app.add_middleware(QueryTracingMiddleware)
app.add_middleware(MetricsMiddleware)

# ── Metrics endpoint ──────────────────────────────────────────
# Exposes a /metrics endpoint in Prometheus text format, with
# six metric families:
#   dbwarden_migrations_total        Counter  (database, status)
#   dbwarden_migration_duration_seconds  Histogram  (database)
#   dbwarden_schema_version          Gauge    (database)
#   dbwarden_pending_migrations      Gauge    (database)
#   dbwarden_errors_total            Counter  (database, error_type)
#   dbwarden_seed_version            Gauge    (database)
app.include_router(MetricsRouter(), prefix="/metrics")

# ── Health endpoint ────────────────────────────────────────────
# Provides /health/liveness, /health/readiness, and per-database
# health status with connectivity and migration state.
app.include_router(DBWardenHealthRouter(), prefix="/health")


@app.get("/")
async def root():
    return {"message": "dbwarden Observability Example"}
