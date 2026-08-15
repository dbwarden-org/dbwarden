# 10. Auto-Generated Pydantic Schemas

## What You'll Learn

- How `@auto_schema` generates Pydantic schemas from model annotations
- How `public = False` controls field visibility
- How `CreateSchema`, `UpdateSchema`, and `PublicSchema` differ

## Prerequisites

- `dbwarden-fastapi` plugin installed: `dbwarden plugin add dbwarden-fastapi`
- `uv add dbwarden sqlalchemy`

## The Problem

In FastAPI applications, you typically define SQLAlchemy models for the database and Pydantic schemas for the API. This means maintaining two parallel definitions for every entity: the ORM layer and the API layer. They drift apart over time.

dbwarden's `@auto_schema` eliminates this duplication by deriving Pydantic schemas directly from model annotations.

## Step 1: Define a Model with @auto_schema

```python
from dbwarden.databases import TableMeta
from dbwarden_fastapi import auto_schema


@auto_schema
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))

    class Meta(TableMeta):
        comment = "User accounts with auto-generated Pydantic schemas"

        class email:
            comment = "Login email"

        class password_hash:
            public = False  # Excluded from PublicSchema
```

## Step 2: What Gets Generated

The decorator creates four schema classes on the model:

### `User.CreateSchema`

Used for POST requests: includes all fields that the client should provide. Server-defaulted fields (like auto-increment `id`) are excluded.

```python
create = User.CreateSchema(
    email="alice@example.com",
    username="alice",
    password_hash="secret",
    full_name="Alice Smith",
    is_active=True,
)
```

### `User.UpdateSchema`

All fields optional: used for PATCH requests.

```python
update = User.UpdateSchema(full_name="Alice Johnson")
```

### `User.PublicSchema`

Excludes fields marked `public = False`. Perfect for API responses where you never want to leak `password_hash`.

```python
user = User(
    id=1,
    email="alice@example.com",
    username="alice",
    password_hash="secret",
    is_active=True,
)
public = User.PublicSchema.model_validate(user)
print(public.model_dump())
# {
#     "id": 1,
#     "email": "alice@example.com",
#     "username": "alice",
#     "full_name": None,
#     "is_active": True,
#     "created_at": ...,
# }
# password_hash is NOT included
```

### `User.Schema`

All mapped columns, including those marked `public = False`.

## Step 3: Controlling Visibility

| Technique | Effect |
|-----------|--------|
| `class Meta: class field: public = False` | Excluded from PublicSchema |
| Field name starting with `_` | Implicitly `public = False` |
| `SchemaConfig(exclude_public=["field"])` | Excluded from PublicSchema |
| `SchemaConfig(exclude_create=["field"])` | Excluded from CreateSchema |

## Step 4: Customizing Schema Generation

```python
from dbwarden_fastapi import auto_schema, SchemaConfig


@auto_schema(config=SchemaConfig(
    exclude_public=["internal_note"],
    exclude_create=["created_at"],
    field_overrides={
        "email": EmailStr,
    },
))
class User(Base):
    ...
```

`SchemaConfig` supports:

| Option | Description |
|--------|-------------|
| `exclude_always` | Excluded from all schemas |
| `exclude_create` | Excluded from CreateSchema only |
| `exclude_update` | Excluded from UpdateSchema only |
| `exclude_public` | Excluded from PublicSchema only |
| `field_overrides` | Override Pydantic field types |
| `required_always` | Fields always required |
| `optional_always` | Fields always optional |

## Key Takeaways

- `@auto_schema` generates CreateSchema, UpdateSchema, PublicSchema, and Schema
- `public = False` in `class Meta` controls API visibility; no manual filtering in routes
- Fields starting with `_` are implicitly non-public
- Use `User.PublicSchema.model_validate(instance)` to convert model instances to API responses
- Customize with `SchemaConfig` for advanced use cases

## Related Documentation

- [Modeling Guide: Auto-Generated Schemas](../getting-started/modeling.md#auto-generated-pydantic-schemas-with-auto_schema)
- [SQLAlchemy Models Reference](../models.md)

## Next

[Section 11: Observability](11-observability.md)
