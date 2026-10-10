# dbwarden configuration for the data-migrations "transitions" example.
#
# The filename "dbwarden.py" marks this directory as the project root.
# Running the dbwarden CLI from here loads this file and registers the
# database target below.

from dbwarden import DbwardenDatabase


class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True

    # SQLite keeps the example zero-dependency. The database file lives
    # beside this config and is ignored by git.
    database_type = "sqlite"
    database_url_sync = "sqlite:///./app.db"

    # Python files or directories scanned for SQLAlchemy models.
    model_paths = ["app"]

    # Modules with DataTransition declarations. This example writes the
    # split transition here once the legacy snapshot exists.
    data_paths = ["app/data"]

    # Compiled data snapshots and the historical schema registry.
    data_snapshot_dir = ".dbwarden/data"
    snapshot_registry = ".dbwarden/snapshots/registry.json"
