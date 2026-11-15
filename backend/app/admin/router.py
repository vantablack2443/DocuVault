import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.admin import service
from app.admin.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut, UserOut
from app.auth.dependencies import require_roles
from app.auth.schemas import ErrorResponse
from app.auth.service import Principal
from app.db.models import Role
from app.db.session import SessionDep

router = APIRouter(
    tags=["admin"],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
    },
)

# Auditors may look, only admins may change.
Reader = Annotated[Principal, Depends(require_roles(Role.ADMIN, Role.AUDITOR))]
Admin = Annotated[Principal, Depends(require_roles(Role.ADMIN))]


@router.get("/users", response_model=list[UserOut], summary="List users in my tenant")
async def list_users(principal: Reader, session: SessionDep) -> list[UserOut]:
    users = await service.list_users(session, principal)
    return [UserOut.model_validate(user) for user in users]


@router.get("/api-keys", response_model=list[ApiKeyOut], summary="List API keys in my tenant")
async def list_api_keys(principal: Reader, session: SessionDep) -> list[ApiKeyOut]:
    keys = await service.list_api_keys(session, principal)
    return [ApiKeyOut.model_validate(key) for key in keys]


@router.post(
    "/api-keys",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key",
)
async def create_api_key(
    body: ApiKeyCreate, principal: Admin, session: SessionDep
) -> ApiKeyCreated:
    api_key, plaintext = await service.create_api_key(session, principal, name=body.name)
    return ApiKeyCreated(**ApiKeyOut.model_validate(api_key).model_dump(), key=plaintext)


@router.delete(
    "/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key",
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
)
async def revoke_api_key(key_id: uuid.UUID, principal: Admin, session: SessionDep) -> None:
    await service.revoke_api_key(session, principal, key_id)
