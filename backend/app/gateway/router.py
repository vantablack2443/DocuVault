from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel

from app.auth.dependencies import ApiKeyPrincipalDep
from app.auth.schemas import ErrorResponse

router = APIRouter(
    prefix="/v1",
    tags=["gateway"],
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse}},
)

DEFAULT_MODEL = "docuvault-default"


class Model(BaseModel):
    id: str
    object: Literal["model"] = "model"
    owned_by: str = "docuvault"


class ModelList(BaseModel):
    object: Literal["list"] = "list"
    data: list[Model]


@router.get("/models", response_model=ModelList, summary="List models (OpenAI-compatible)")
async def list_models(_: ApiKeyPrincipalDep) -> ModelList:
    """One virtual model; the gateway picks the upstream provider (Phase 3)."""
    return ModelList(data=[Model(id=DEFAULT_MODEL)])
