from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from dbwarden.data import DataMeta, batch, col, derive, rows


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)

    class Data(DataMeta):
        transformations = [
            derive(
                "value",
                col("source") + 1,
                rollback="capture",
                execution=batch(size=1, key=["id"]),
            ),
        ]


class Country(Base):
    __tablename__ = "country"

    code: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    class Data(DataMeta):
        # First revision: declare and own UY and AR.
        managed_rows = rows(
            key="code",
            rows=[
                {"code": "UY", "name": "Uruguay"},
                {"code": "AR", "name": "Argentina"},
            ],
            owned_columns=["name"],
            rollback="restore_previous",
        )
