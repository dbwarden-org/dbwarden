# This is the dbwarden configuration file.
# It is loaded by the CLI when you run `dbwarden` commands from this directory.
#
# The filename "dbwarden.py" is the convention: it tells the CLI
# "this is the project root." dbwarden uses a sandboxed loader to
# import it safely without conflicting with the installed package.

from dbwarden import DbwardenDatabase

class Primary(DbwardenDatabase):
    # Concrete subclasses register a named database target automatically.
    # Primary.handle can be used at runtime, for example, to inject sessions
    # into FastAPI routes.
    # Arbitrary name used with --database / -d on the CLI
    database_name = "primary"

    # Exactly one database must have default=True. This is the one
    # used when you omit --database from CLI commands.
    default = True

    # The SQLAlchemy backend type. dbwarden uses this to generate
    # backend-specific DDL for PostgreSQL.
    database_type = "postgresql"

    # The SQLAlchemy connection URL (sync driver).
    # Update user/password/host/port to match your local PostgreSQL.
    database_url_sync = "postgresql://user:password@localhost:5432/primary"

    # Dotted paths to Python modules containing SQLAlchemy model
    # classes. dbwarden discovers them by scanning these modules
    # and their parent packages for DeclarativeBase subclasses.
    model_paths = ["app"]
