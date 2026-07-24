from datetime import datetime
from pydantic import BaseModel, EmailStr


# Pydantic schemas for the FastAPI CRUD routes.
# In a real project, you might replace these with the auto-
# generated schemas from @auto_schema (from the dbwarden-fastapi
# plugin, see dbwarden-fastapi repo for examples) to eliminate
# the duplication between ORM models and API schemas.


class UserBase(BaseModel):
    email: EmailStr
    username: str
    full_name: str | None = None


class UserCreate(UserBase):
    pass


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    username: str | None = None
    full_name: str | None = None
    is_active: bool | None = None


class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        # Tells Pydantic to read data from ORM model attributes
        # (not just dict keys), so model_validate(user_instance) works.
        from_attributes = True
