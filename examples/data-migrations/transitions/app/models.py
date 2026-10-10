from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Final models: the legacy_people table has been split into person and
# customer. Scripts copy stage/final/models.py over this file after the
# legacy snapshot exists; stage/legacy/models.py is the starting point.
class Base(DeclarativeBase):
    pass


class Person(Base):
    __tablename__ = "person"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)


class Customer(Base):
    __tablename__ = "customer"

    person_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tier: Mapped[str] = mapped_column(String, nullable=False)
