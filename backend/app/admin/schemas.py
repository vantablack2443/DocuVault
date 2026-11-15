import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Role
from app.redaction.types import PiiType


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: Role
    is_active: bool
    created_at: datetime


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    key: str = Field(description="Full secret. Shown exactly once; only a digest is stored.")


class PolicyBody(BaseModel):
    enabled_types: list[PiiType] = Field(
        description="PII types this tenant redacts. Everything else passes through unchanged."
    )
