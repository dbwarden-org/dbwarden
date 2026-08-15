from sqlalchemy import (
    Column, Integer, String, Text, Boolean,
    DateTime, ForeignKey, Float, text,
)
from sqlalchemy.orm import declarative_base
from dbwarden import TableMeta, IndexSpec

# Every SQLAlchemy model must inherit from a common declarative base.
# dbwarden discovers models by scanning for classes that inherit from it.
Base = declarative_base()


# ── TableMeta ──────────────────────────────────────────────────
# The inner Meta class (inheriting from TableMeta) is how you attach
# table-level metadata that dbwarden translates into DDL at migration
# time.  The comment becomes a COMMENT ON TABLE in PostgreSQL, and
# IndexSpec entries become CREATE INDEX statements.
#
# Columns defined directly on the model (via Column()) are read by
# dbwarden's schema scanner, their types, nullability, defaults,
# unique constraints, and foreign keys all feed into the generated SQL.

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    username = Column(String(100), unique=True, nullable=False)
    full_name = Column(String(200), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"))

    class Meta(TableMeta):
        comment = "Core user accounts"
        indexes = [
            # Named indexes produce "CREATE INDEX IF NOT EXISTS ..."
            # in the generated DDL.
            IndexSpec(name="ix_users_created_at", columns=["created_at"]),
        ]


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    # ForeignKey("users.id") generates a REFERENCES clause in the
    # CREATE TABLE statement.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"))

    class Meta(TableMeta):
        comment = "User blog posts"
        indexes = [
            IndexSpec(name="ix_posts_user_id", columns=["user_id"]),
            IndexSpec(name="ix_posts_created_at", columns=["created_at"]),
        ]


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    price = Column(Float, nullable=False)
    description = Column(Text, nullable=True)
    in_stock = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"))

    class Meta(TableMeta):
        comment = "Product catalog"
        # CHECK constraints defined in Meta.checks are rendered as
        # CONSTRAINT ... CHECK (...) in the generated DDL.
        checks = [
            {"name": "ck_products_price_positive", "sql": "price > 0"},
        ]


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)

    class Meta(TableMeta):
        comment = "Taxonomy tags for products"
