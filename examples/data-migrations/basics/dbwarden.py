# dbwarden configuration for the data-migrations basics example.
#
# The filename "dbwarden.py" marks this directory as the project root.
# Running the dbwarden CLI from here loads this file and registers the
# database targets below.

from dbwarden import DbwardenDatabase


class Primary(DbwardenDatabase):
    database_name = "primary"
    default = True

    # SQLite keeps the example zero-dependency. The database file lives
    # beside this config and is ignored by git.
    database_type = "sqlite"
    database_url_sync = "sqlite:///./app.db"

    # Python files or directories scanned for SQLAlchemy models. A model
    # may declare managed rows and transformations through an inner
    # `Data(DataMeta)` class, so those declarations are discovered here.
    model_paths = ["app"]

    # Optional: modules with DataTransition declarations. This example
    # keeps its declarations on the models, but later examples in the
    # tutorial use this directory.
    data_paths = ["app/data"]

    # Compiled data snapshots and the historical schema registry.
    data_snapshot_dir = ".dbwarden/data"
    snapshot_registry = ".dbwarden/snapshots/registry.json"
