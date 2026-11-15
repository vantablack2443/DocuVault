import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models import Role


class ErrorResponse(BaseModel):
    detail: str


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = Field(
        description="Absent for guest sessions, which end when the sandbox expires."
    )
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int = Field(description="Access token lifetime in seconds.")


class CurrentUser(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: Role
    is_guest: bool
