from collections import Counter

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from app.auth.dependencies import PrincipalDep
from app.auth.schemas import ErrorResponse
from app.redaction import service
from app.redaction.types import PiiType

router = APIRouter(
    prefix="/redaction",
    tags=["redaction"],
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse}},
)

MAX_PREVIEW_CHARACTERS = 20_000


class PreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_PREVIEW_CHARACTERS)


class EntityOut(BaseModel):
    type: PiiType
    start: int = Field(description="Character offset in the submitted text (inclusive).")
    end: int = Field(description="Character offset in the submitted text (exclusive).")
    placeholder: str
    detector: str


class PreviewResponse(BaseModel):
    redacted_text: str
    entities: list[EntityOut]
    counts: dict[PiiType, int]


@router.post("/preview", response_model=PreviewResponse, summary="Show what would be redacted")
async def preview(body: PreviewRequest, _: PrincipalDep, request: Request) -> PreviewResponse:
    """Runs the redaction engine without calling any LLM. Nothing is stored."""
    result = await service.preview(request.app.state.redaction_engine, body.text)
    return PreviewResponse(
        redacted_text=result.text,
        entities=[
            EntityOut(
                type=entity.type,
                start=entity.start,
                end=entity.end,
                placeholder=entity.placeholder,
                detector=entity.detector,
            )
            for entity in result.entities
        ],
        counts=dict(Counter(entity.type for entity in result.entities)),
    )
