# FastAPI + dbwarden Example

A complete FastAPI application using dbwarden for database migrations, health checks, and session management.

## Prerequisites

- Docker (for PostgreSQL)
- Python 3.12+

## Quick Start

```bash
uv add dbwarden sqlalchemy fastapi uvicorn asyncpg

# Start PostgreSQL
docker run -d --name pg \
    -e POSTGRES_USER=user \
    -e POSTGRES_PASSWORD=password \
    -e POSTGRES_DB=myapp \
    -p 5432:5432 \
    postgres:16

# Initialize and migrate
dbwarden init
dbwarden make-migrations "create users table"
dbwarden migrate

# Start the app
uvicorn app.main:app --reload
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Root info |
| `GET /health/` | Health check (liveness + readiness) |
| `GET /db/status` | Migration status |
| `POST /db/migrate` | Trigger migration |
| `GET /api/v1/users/` | List users |
| `POST /api/v1/users/` | Create user |
| `GET /api/v1/users/{id}` | Get user |
| `PATCH /api/v1/users/{id}` | Update user |
| `DELETE /api/v1/users/{id}` | Delete user |

## Testing

```bash
pytest tests/
```
