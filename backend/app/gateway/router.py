from typing import Literal

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth.dependencies import ApiKeyPrincipalDep
from app.auth.schemas import ErrorResponse
from app.gateway import service
from app.gateway.schemas import (
    DEFAULT_MODEL,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    Usage,
)
from app.gateway.service import GatewayDeps, Prepared

router = APIRouter(
    prefix="/v1",
    tags=["gateway"],
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse}},
)


class Model(BaseModel):
    id: str
    object: Literal["model"] = "model"
    owned_by: str = "docuvault"


class ModelList(BaseModel):
    object: Literal["list"] = "list"
    data: list[Model]


@router.get("/models", response_model=ModelList, summary="List models (OpenAI-compatible)")
async def list_models(_: ApiKeyPrincipalDep) -> ModelList:
    """One virtual model; the gateway picks the upstream provider."""
    return ModelList(data=[Model(id=DEFAULT_MODEL)])


def _deps(request: Request) -> GatewayDeps:
    state = request.app.state
    return GatewayDeps(
        engine=state.redaction_engine,
        chain=state.provider_chain,
        redis=state.redis,
        bucket=state.token_bucket,
        session_factory=state.session_factory,
        settings=state.settings,
    )


def _headers(prepared: Prepared, provider: str, cache_hit: bool) -> dict[str, str]:
    return {
        "X-DocuVault-Provider": provider,
        "X-DocuVault-Cache": "hit" if cache_hit else "miss",
        "X-DocuVault-Redactions": str(sum(prepared.counts.values())),
    }


@router.post(
    "/chat/completions",
    response_model=ChatCompletionResponse,
    summary="Chat completion with PII minimisation (OpenAI-compatible)",
    responses={
        status.HTTP_200_OK: {
            "content": {"text/event-stream": {}},
            "description": "JSON, or server-sent events when `stream` is true.",
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    },
)
async def chat_completions(
    body: ChatCompletionRequest,
    principal: ApiKeyPrincipalDep,
    request: Request,
    response: Response,
) -> ChatCompletionResponse | StreamingResponse:
    """Personal data is replaced by placeholders before the prompt goes upstream
    and put back into the answer on the way out. Point any OpenAI client at this
    base URL and use a `dv_…` key."""
    deps = _deps(request)
    if body.stream:
        stream = await service.open_stream(deps, principal, body)
        return StreamingResponse(
            stream.events,
            media_type="text/event-stream",
            headers={
                **_headers(stream.prepared, stream.provider, stream.cache_hit),
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # ask reverse proxies not to buffer the stream
            },
        )
    result = await service.complete(deps, principal, body)
    response.headers.update(_headers(result.prepared, result.provider, result.cache_hit))
    return ChatCompletionResponse(
        id=result.prepared.id,
        created=result.prepared.created,
        model=result.prepared.model,
        choices=[Choice(message=ChatMessage(role="assistant", content=result.text))],
        usage=Usage(
            prompt_tokens=result.completion.prompt_tokens,
            completion_tokens=result.completion.completion_tokens,
            total_tokens=result.completion.prompt_tokens + result.completion.completion_tokens,
        ),
    )
