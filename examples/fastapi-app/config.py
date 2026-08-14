# This config registers one database target (primary) with both
# a sync URL (for migrations) and an async URL (for FastAPI routes).
#
# The Primary.handle attribute is a DatabaseHandle object that
# exposes:
#   Primary.handle.sync_session  : sync session for scripts / background tasks
#   Primary.handle.async_session : async session for FastAPI route handlers
#
# Both are FastAPI-compatible dependency annotations.  No separate
# dependency-injection module is needed: just annotate your route
# parameter with Primary.handle.async_session and FastAPI resolves it.

from dbwarden import DbwardenDatabase

class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    # Sync URL used by CLI commands (make-migrations, migrate, status, etc.)
    database_url_sync = "postgresql://user:password@localhost:5432/myapp"
    # Async URL used by FastAPI routes when injected via primary.async_session
    database_url_async = "postgresql+asyncpg://user:password@localhost:5432/myapp"
    model_paths = ["app.models"]

# Keep a conventional handle name for the route modules.
primary = Primary.handle
