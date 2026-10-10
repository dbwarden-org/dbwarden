from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class LegacyA(Base):
    __tablename__ = "legacy_a"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)


class LegacyB(Base):
    __tablename__ = "legacy_b"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
