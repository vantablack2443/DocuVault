from typing import Literal

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import ApiKeyPrincipalDep, PrincipalDep
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
from app.gateway.service import Caller, GatewayDeps, Prepared
from app.redaction.types import PiiType

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
        bus=state.event_bus,
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
    caller = Caller(
        tenant_id=principal.tenant_id,
        api_key_id=principal.api_key_id,
        actor=f"key:{principal.api_key_id}",
        rate_key=f"ratelimit:gateway:{principal.api_key_id}",
    )
    if body.stream:
        stream = await service.open_stream(deps, caller, body)
        return StreamingResponse(
            stream.events,
            media_type="text/event-stream",
            headers={
                **_headers(stream.prepared, stream.provider, stream.cache_hit),
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # ask reverse proxies not to buffer the stream
            },
        )
    result = await service.complete(deps, caller, body)
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


# --- playground: the same pipeline, with every intermediate step made visible ---

playground_router = APIRouter(
    prefix="/playground",
    tags=["playground"],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
    },
)


class PlaygroundRequest(BaseModel):
    message: str = Field(min_length=1, max_length=5000)


class PlaygroundEntity(BaseModel):
    type: PiiType
    start: int
    end: int
    placeholder: str
    detector: str


class PlaygroundResponse(BaseModel):
    original: str
    entities: list[PlaygroundEntity] = Field(description="Offsets into `original`.")
    sent_upstream: str = Field(description="The user message exactly as the LLM received it.")
    upstream_answer: str = Field(description="The LLM's answer, still with placeholders.")
    final_answer: str = Field(description="What the client receives after re-hydration.")
    provider: str
    cache_hit: bool


@playground_router.post(
    "/chat", response_model=PlaygroundResponse, summary="Run one message and show each step"
)
async def playground_chat(
    body: PlaygroundRequest, principal: PrincipalDep, request: Request
) -> PlaygroundResponse:
    """For the dashboard: signed-in users of any role, rate limited per user."""
    caller = Caller(
        tenant_id=principal.tenant_id,
        api_key_id=None,
        actor=f"user:{principal.user_id}",
        rate_key=f"ratelimit:playground:{principal.user_id}",
    )
    chat = ChatCompletionRequest(messages=[ChatMessage(role="user", content=body.message)])
    result = await service.complete(_deps(request), caller, chat)
    return PlaygroundResponse(
        original=body.message,
        entities=[
            PlaygroundEntity(
                type=entity.type,
                start=entity.start,
                end=entity.end,
                placeholder=entity.placeholder,
                detector=entity.detector,
            )
            for entity in result.prepared.entities[0]
        ],
        sent_upstream=result.prepared.messages[-1]["content"],
        upstream_answer=result.completion.text,
        final_answer=result.text,
        provider=result.provider,
        cache_hit=result.cache_hit,
    )
