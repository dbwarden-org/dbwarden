from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base

from dbwarden.data import DataMeta, col, derive, func, rows, validate

# Every SQLAlchemy model inherits from a shared declarative base. dbwarden
# discovers models by scanning the configured model_paths for subclasses.
Base = declarative_base()


class Country(Base):
    """Managed rows declared literally beside the model."""

    __tablename__ = "countries"

    code = Column(String(2), primary_key=True)
    name = Column(String(100), nullable=False)

    class Data(DataMeta):
        # key is the column that identifies each row. owned_columns lists
        # the columns this declaration is responsible for: application
        # code may write other columns without triggering a conflict.
        managed_rows = rows(
            key="code",
            rows=[
                {"code": "UY", "name": "Uruguay"},
                {"code": "AR", "name": "Argentina"},
                {"code": "BR", "name": "Brazil"},
            ],
            owned_columns=["name"],
            rollback="restore_previous",
        )


class Currency(Base):
    """Managed rows loaded from a static CSV file at compile time."""

    __tablename__ = "currencies"

    code = Column(String(3), primary_key=True)
    name = Column(String(100), nullable=False)

    class Data(DataMeta):
        # The file is read during compilation and embedded as canonical
        # values in the frozen artifact; execution never rereads the CSV.
        managed_rows = rows(
            key="code",
            source="data/currencies.csv",
            owned_columns=["name"],
            rollback="restore_previous",
        )


class User(Base):
    """Managed rows, a derived column, and a validation rule."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False)
    name = Column(String(200), nullable=False)
    slug = Column(String(200), nullable=True)

    class Data(DataMeta):
        managed_rows = rows(
            key="id",
            rows=[{"id": 1, "email": "ada@example.com", "name": "  Ada Lovelace  "}],
            owned_columns=["email", "name"],
            rollback="restore_previous",
        )

        # derive owns exactly one target column inside its declared domain.
        # rollback="clear" requires the target to be nullable.
        transformations = [
            derive("slug", func.lower(func.trim(col("name"))), rollback="clear"),
        ]

        # Every validation expression must hold for every target row.
        validations = [
            validate(col("email").is_not(None), message="email is required"),
        ]
