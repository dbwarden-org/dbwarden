from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Final model: the two legacy ledgers have been consolidated into one
# account table. Scripts copy stage/final/models.py over this file after
# the legacy snapshots exist; stage/legacy/models.py is the starting point.
class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
