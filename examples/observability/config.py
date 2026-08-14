import os
from dbwarden import DbwardenDatabase

# Same pattern as the fastapi-app config, but with environment
# variable overrides so the config can adapt to Docker networking.
# When running via docker-compose, set DATABASE_URL to point at
# the postgres service hostname.
class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = os.getenv(
        "DATABASE_URL",
        "postgresql://user:password@localhost:5432/myapp",
    )
    database_url_async = os.getenv(
        "DATABASE_URL_ASYNC",
        "postgresql+asyncpg://user:password@localhost:5432/myapp",
    )
    model_paths = ["app.models"]
