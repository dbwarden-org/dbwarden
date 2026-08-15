from contextlib import asynccontextmanager
from fastapi import FastAPI
from dbwarden.fastapi import (
    DBWardenHealthRouter,
    DBWardenRouter,
    dbwarden_lifespan,
)

from app.routes import users


# ── Lifespan ───────────────────────────────────────────────────
# The lifespan context manager hooks into FastAPI's startup and
# shutdown events.  dbwarden_lifespan handles:
#
#   On startup (mode="check"):
#     - Validates that all migrations are applied
#     - Opens a readiness gate (app won't accept traffic until
#       schema validation passes)
#     - Warms up connection pools
#
#   On shutdown:
#     - Disposes all SQLAlchemy engine pools
#     - Closes ClickHouse clients
#
# Other modes:
#   mode="migrate": auto-apply pending migrations on startup
#   mode="skip"   : no startup checks

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with dbwarden_lifespan(app, mode="check"):
        yield


app = FastAPI(
    title="dbwarden FastAPI Example",
    description="Demonstrates FastAPI integration with dbwarden",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Routers ────────────────────────────────────────────────────
# /api/v1/users : CRUD routes using primary.async_session injection
# /health/*     : liveness, readiness, per-database health status
# /db/*         : migration status (GET /db/status) and execution
#                  (POST /db/migrate) as HTTP endpoints
app.include_router(users.router, prefix="/api/v1")
app.include_router(DBWardenHealthRouter(), prefix="/health")
app.include_router(DBWardenRouter(), prefix="/db")


@app.get("/")
async def root():
    return {
        "message": "dbwarden FastAPI Example",
        "docs": "/docs",
        "health": "/health/",
    }
