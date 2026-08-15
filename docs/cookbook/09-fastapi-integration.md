# 9. FastAPI Integration

## What You'll Learn

- How to wire dbwarden into a FastAPI application lifecycle
- How to use `primary.async_session` as a dependency injection
- How to expose health check and migration endpoints
- How to validate schema on startup

## Prerequisites

- Docker (for PostgreSQL)
- `examples/fastapi-app/` directory

## Step 1: Configuration with Session Handles

```python
from dbwarden import DbwardenDatabase

class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://user:password@localhost:5432/myapp"
    database_url_async = "postgresql+asyncpg://user:password@localhost:5432/myapp"
    model_paths = ["app.models"]
```

`Primary.handle` is a `DatabaseHandle`. It exposes `Primary.handle.async_session` and `Primary.handle.sync_session` as FastAPI-compatible dependency annotations; no separate dependency module needed. The equivalent `database_config(...)` function API returns the same handle.

## Step 2: Lifespan Hook

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from dbwarden_fastapi import dbwarden_lifespan


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with dbwarden_lifespan(app, mode="check"):
        yield


app = FastAPI(lifespan=lifespan)
```

`dbwarden_lifespan` runs on every startup:

1. **Schema validation** (mode `"check"`): verifies all pending migrations exist and the database is in a known state
2. **Readiness gate**: the app won't accept traffic until validation passes
3. **Connection pool warmup**: pre-connects to the database
4. **On shutdown**: disposes all engine pools and ClickHouse clients

Available modes:
- `"check"`: validate schema, fail on pending migrations (recommended for production)
- `"migrate"`: apply pending migrations automatically on startup
- `"skip"`: no startup checks

## Step 3: Session Dependency in Routes

```python
from config import primary
from app.models import User
from app.schemas import UserResponse


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, session: primary.async_session):
    result = await session.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
```

`primary.async_session` is a type alias for `Annotated[AsyncSession, Depends(...)]`. FastAPI resolves it to an actual database session using the engine configured by `Primary` (or the equivalent `database_config()` function).

The session is automatically:
- Opened when the route handler starts
- Committed (or rolled back on exception) when the handler finishes
- Closed and returned to the pool

## Step 4: Health Endpoints

```python
from dbwarden_fastapi import DBWardenHealthRouter

app.include_router(DBWardenHealthRouter(), prefix="/health")
```

This adds:

| Endpoint | Description |
|----------|-------------|
| `GET /health/` | Overall health status across all databases |
| `GET /health/liveness` | Is the app alive? (lightweight) |
| `GET /health/readiness` | Is the app ready for traffic? (checks DB connectivity) |
| `GET /health/{database_name}` | Per-database health status |

Sample response:

```json
{
  "status": "ok",
  "databases": {
    "primary": {
      "status": "ok",
      "connected": true,
      "pending_migrations": 0,
      "applied_migrations": 5,
      "lock_active": false
    }
  }
}
```

## Step 5: Migration Endpoints

```python
from dbwarden_fastapi import DBWardenRouter

app.include_router(DBWardenRouter(), prefix="/db")
```

| Endpoint | Description |
|----------|-------------|
| `GET /db/status` | JSON representation of `dbwarden status` |
| `POST /db/migrate` | Trigger migration execution at runtime |

These endpoints are useful for management UIs or automated deployment tooling.

## Step 6: The Complete App

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from dbwarden_fastapi import (
    DBWardenHealthRouter,
    DBWardenRouter,
    dbwarden_lifespan,
)
from app.routes import users


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with dbwarden_lifespan(app, mode="check"):
        yield


app = FastAPI(
    title="dbwarden FastAPI Example",
    lifespan=lifespan,
)

app.include_router(users.router, prefix="/api/v1")
app.include_router(DBWardenHealthRouter(), prefix="/health")
app.include_router(DBWardenRouter(), prefix="/db")
```

## Step 7: Run and Test

```bash
# Install dependencies
uv add dbwarden sqlalchemy fastapi uvicorn asyncpg
dbwarden plugin add dbwarden-fastapi

# Start PostgreSQL
docker run -d --name pg -e POSTGRES_USER=user \
    -e POSTGRES_PASSWORD=password -e POSTGRES_DB=myapp \
    -p 5432:5432 postgres:16

# Initialize and migrate
$ dbwarden init
$ dbwarden make-migrations "create users table"
$ dbwarden migrate

# Start the app
uvicorn app.main:app --reload
```

```bash
# Health check
curl http://localhost:8000/health/

# Create a user
curl -X POST http://localhost:8000/api/v1/users/ \
    -H "Content-Type: application/json" \
    -d '{"email": "alice@example.com", "username": "alice"}'

# Migration status
curl http://localhost:8000/db/status
```

## Key Takeaways

- `Primary.handle` is a `DatabaseHandle` with built-in FastAPI dependencies; `database_config()` returns the equivalent handle
- `dbwarden_lifespan` integrates schema validation into the app lifecycle
- `primary.async_session` works directly as a route parameter type annotation
- `DBWardenHealthRouter` exposes liveness, readiness, and per-database health
- `DBWardenRouter` exposes migration status and execution as HTTP endpoints

## Related Documentation

FastAPI support is not part of core. It ships as the `dbwarden-fastapi` plugin, which owns the session dependencies, health endpoints, migration routes, and metrics middleware shown above. Install it with `dbwarden plugin add dbwarden-fastapi`.

Full reference and tutorials live in the plugin's repository: [dbwarden-fastapi](https://github.com/dbwarden-org/dbwarden-fastapi).

## Next

[Section 10: Auto Schemas](10-auto-schemas.md)
