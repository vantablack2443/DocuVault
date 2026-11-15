import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict

from app.audit import service
from app.auth.dependencies import require_roles
from app.auth.schemas import ErrorResponse
from app.auth.service import Principal
from app.db.models import Role
from app.db.session import SessionDep

router = APIRouter(
    prefix="/audit",
    tags=["audit"],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
    },
)

Reader = Annotated[Principal, Depends(require_roles(Role.ADMIN, Role.AUDITOR))]


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    actor: str
    action: str
    details: dict[str, object]
    created_at: datetime
    prev_hash: str
    hash: str


class VerificationOut(BaseModel):
    valid: bool
    entries: int
    first_invalid_seq: int | None


@router.get("", response_model=list[AuditEntryOut], summary="Audit log of my tenant, newest first")
async def list_audit(
    principal: Reader,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_seq: Annotated[int | None, Query(ge=1, description="Page backwards from here")] = None,
) -> list[AuditEntryOut]:
    entries = await service.list_entries(
        session, principal.tenant_id, limit=limit, before_seq=before_seq
    )
    return [AuditEntryOut.model_validate(entry) for entry in entries]


@router.get("/verify", response_model=VerificationOut, summary="Recompute the hash chain")
async def verify_audit(principal: Reader, session: SessionDep) -> VerificationOut:
    """Walks the whole chain and reports the first entry that was altered, removed or
    reordered."""
    result = await service.verify(session, principal.tenant_id)
    return VerificationOut(
        valid=result.valid, entries=result.entries, first_invalid_seq=result.first_invalid_seq
    )
