---
seo:
  title: 9. FastAPI Integration - DBWarden Documentation
  canonical: https://dbwarden.emiliano-go.com/cookbook/09-fastapi-integration
  robots: index,follow
  og:
    type: website
    title: 9. FastAPI Integration - DBWarden Documentation
    description: What You'll Learn
    url: https://dbwarden.emiliano-go.com/cookbook/09-fastapi-integration
    image: https://dbwarden.emiliano-go.com/assets/images/og-image.png
    image:width: 1376
    image:height: 768
    image:alt: DBWarden documentation
    site_name: DBWarden Documentation
    locale: en_US
  twitter:
    card: summary_large_image
    title: 9. FastAPI Integration - DBWarden Documentation
    description: What You'll Learn
    image: https://dbwarden.emiliano-go.com/assets/images/og-image.png
    image:alt: DBWarden documentation
    site: '@emiliano_go_'
  description: What You'll Learn
  schema_jsonld:
  - '@context': https://schema.org
    '@type': WebPage
    name: 9. FastAPI Integration - DBWarden Documentation
    url: https://dbwarden.emiliano-go.com/cookbook/09-fastapi-integration
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
      name: 09 Fastapi Integration
      item: https://dbwarden.emiliano-go.com/cookbook/09-fastapi-integration
seo_html: "<title>9. FastAPI Integration - DBWarden Documentation</title>\n<meta name=\"\
  description\" content=\"What You&#x27;ll Learn\">\n<link rel=\"canonical\" href=\"\
  https://dbwarden.emiliano-go.com/cookbook/09-fastapi-integration\">\n<meta name=\"\
  robots\" content=\"index,follow\">\n<meta property=\"og:type\" content=\"website\"\
  >\n<meta property=\"og:title\" content=\"9. FastAPI Integration - DBWarden Documentation\"\
  >\n<meta property=\"og:description\" content=\"What You&#x27;ll Learn\">\n<meta\
  \ property=\"og:url\" content=\"https://dbwarden.emiliano-go.com/cookbook/09-fastapi-integration\"\
  >\n<meta property=\"og:image\" content=\"https://dbwarden.emiliano-go.com/assets/images/og-image.png\"\
  >\n<meta property=\"og:image:width\" content=\"1376\">\n<meta property=\"og:image:height\"\
  \ content=\"768\">\n<meta property=\"og:image:alt\" content=\"DBWarden documentation\"\
  >\n<meta property=\"og:site_name\" content=\"DBWarden Documentation\">\n<meta property=\"\
  og:locale\" content=\"en_US\">\n<meta name=\"twitter:card\" content=\"summary_large_image\"\
  >\n<meta name=\"twitter:title\" content=\"9. FastAPI Integration - DBWarden Documentation\"\
  >\n<meta name=\"twitter:description\" content=\"What You&#x27;ll Learn\">\n<meta\
  \ name=\"twitter:image\" content=\"https://dbwarden.emiliano-go.com/assets/images/og-image.png\"\
  >\n<meta name=\"twitter:image:alt\" content=\"DBWarden documentation\">\n<meta name=\"\
  twitter:site\" content=\"@emiliano_go_\">\n<script type=\"application/ld+json\"\
  >\n[\n  {\n    \"@context\": \"https://schema.org\",\n    \"@type\": \"WebPage\"\
  ,\n    \"name\": \"9. FastAPI Integration - DBWarden Documentation\",\n    \"url\"\
  : \"https://dbwarden.emiliano-go.com/cookbook/09-fastapi-integration\",\n    \"\
  description\": \"What You'll Learn\",\n    \"image\": \"https://dbwarden.emiliano-go.com/assets/images/og-image.png\"\
  ,\n    \"publisher\": {\n      \"@type\": \"Organization\",\n      \"name\": \"\
  Emiliano Gandini Outeda\",\n      \"logo\": \"https://dbwarden.emiliano-go.com/assets/images/og-image.png\"\
  \n    }\n  },\n  {\n    \"@context\": \"https://schema.org\",\n    \"@type\": \"\
  BreadcrumbList\",\n    \"itemListElement\": [\n      {\n        \"@type\": \"ListItem\"\
  ,\n        \"position\": 1,\n        \"name\": \"Cookbook & Examples\",\n      \
  \  \"item\": \"https://dbwarden.emiliano-go.com/cookbook\"\n      },\n      {\n\
  \        \"@type\": \"ListItem\",\n        \"position\": 2,\n        \"name\": \"\
  09 Fastapi Integration\",\n        \"item\": \"https://dbwarden.emiliano-go.com/cookbook/09-fastapi-integration\"\
  \n      }\n    ]\n  }\n]\n</script>\n"
---

# 9. FastAPI Integration

## What You'll Learn

- How to wire DBWarden into a FastAPI application lifecycle
- How to use `primary.async_session` as a dependency injection
- How to expose health check and migration endpoints
- How to validate schema on startup

## Prerequisites

- Docker (for PostgreSQL)
- `examples/fastapi-app/` directory

## Step 1: Configuration with Session Handles

```python
from dbwarden import database_config

primary = database_config(
    database_name="primary",
    default=True,
    database_type="postgresql",
    database_url_sync="postgresql://user:password@localhost:5432/myapp",
    database_url_async="postgresql+asyncpg://user:password@localhost:5432/myapp",
    model_paths=["app.models"],
)
```

The `primary` object is a `DatabaseHandle`. It exposes `primary.async_session` and `primary.sync_session` as FastAPI-compatible dependency annotations; no separate dependency module needed.

## Step 2: Lifespan Hook

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from dbwarden.extensions.fastapi import dbwarden_lifespan


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

`primary.async_session` is a type alias for `Annotated[AsyncSession, Depends(...)]`. FastAPI resolves it to an actual database session using the engine configured in `database_config()`.

The session is automatically:
- Opened when the route handler starts
- Committed (or rolled back on exception) when the handler finishes
- Closed and returned to the pool

## Step 4: Health Endpoints

```python
from dbwarden.extensions.fastapi import DBWardenHealthRouter

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
from dbwarden.extensions.fastapi import DBWardenRouter

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
from dbwarden.extensions.fastapi import (
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
    title="DBWarden FastAPI Example",
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

- `database_config()` returns a `DatabaseHandle` with built-in FastAPI dependencies
- `dbwarden_lifespan` integrates schema validation into the app lifecycle
- `primary.async_session` works directly as a route parameter type annotation
- `DBWardenHealthRouter` exposes liveness, readiness, and per-database health
- `DBWardenRouter` exposes migration status and execution as HTTP endpoints

## Related Documentation

- [FastAPI Integration Overview](../fastapi/index.md)
- [FastAPI Tutorial: First Steps](../fastapi/tutorial/first-steps.md)
- [FastAPI Tutorial: Complete Application](../fastapi/tutorial/complete-application.md)
- [Session Dependency](../fastapi/tutorial/session-dependency.md)
- [Health Endpoints](../fastapi/tutorial/health-endpoints.md)

## Next

[Section 10: Auto Schemas](10-auto-schemas.md)
