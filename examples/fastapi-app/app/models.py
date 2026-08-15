from sqlalchemy import Boolean, DateTime, Integer, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# This model uses SQLAlchemy 2.0 Mapped/mapped_column style.
# dbwarden works with both the classic Column() style (core
# example) and the 2.0 Mapped style shown here: it detects
# columns regardless of declaration style.
#
# Note: no TableMeta or IndexSpec here.  Those are optional
# enhancements for extra DDL like comments and indexes.
# dbwarden generates CREATE TABLE from the columns alone.

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(  # noqa: F821
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(  # noqa: F821
        DateTime, server_default=text("CURRENT_TIMESTAMP")
    )
