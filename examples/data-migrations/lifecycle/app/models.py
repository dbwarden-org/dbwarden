from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from dbwarden.data import DataMeta, archive_table, batch, col, derive, literal, rows

# Final state: item.value is derived with capture rollback and country rows
# that are no longer declared are archived. Scripts copy stage/base,
# stage/capture, then stage/archive over this file as they replay history.


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)

    class Data(DataMeta):
        # value is recomputed as source + 1. rollback="capture" stores the
        # typed preimage so a rollback restores the original value.
        # batch() processes rows in keyset chunks of one.
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
        # Previously owned rows that are no longer declared are moved to
        # country_archive instead of being dropped. The scope limits the
        # declaration to rows it may have owned.
        managed_rows = rows(
            key="code",
            rows=[{"code": "UY", "name": "Uruguay"}],
            owned_columns=["name"],
            on_missing="archive",
            scope=col("code") != literal(""),
            archive_to=archive_table("country_archive"),
            acknowledge_archive=True,
            rollback="restore_previous",
        )
