# This config registers TWO database targets in one project:
#   primary  : PostgreSQL for transactional user data
#   analytics: ClickHouse for page view event analytics
#
# Each gets its own migration directory (migrations/primary/,
# migrations/analytics/), its own migration tracking table,
# and its own lock.  CLI commands target a specific database
# with --database / -d, or use --all to target every database.

from dbwarden import DbwardenDatabase

class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True
    database_type = "postgresql"
    database_url_sync = "postgresql://user:password@localhost:5432/primary"
    database_url_async = "postgresql+asyncpg://user:password@localhost:5432/primary"
    # Model discovery is scoped to this database only.
    # When you run `dbwarden make-migrations --database primary`,
    # it only scans these model paths.
    model_paths = ["app.models.primary"]

class Analytics(DbwardenDatabase):
    database_name = "analytics"
    # No default=True here: exactly one target must have it.
    database_type = "clickhouse"
    # ClickHouse uses HTTP for the sync connection.
    database_url_sync = "http://localhost:8123/analytics"
    model_paths = ["app.models.analytics"]
