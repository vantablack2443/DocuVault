from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_MODEL = "docuvault-default"
MAX_PROMPT_CHARACTERS = 50_000


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatCompletionRequest(BaseModel):
    """Subset of the OpenAI request. Unknown fields are accepted and ignored."""

    model_config = ConfigDict(extra="ignore")

    model: str = Field(default=DEFAULT_MODEL, max_length=80)
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    stream: bool = False
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=4096)

    @model_validator(mode="after")
    def limit_prompt_size(self) -> Self:
        if sum(len(message.content) for message in self.messages) > MAX_PROMPT_CHARACTERS:
            raise ValueError(f"messages exceed {MAX_PROMPT_CHARACTERS} characters in total")
        return self


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class Choice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: Literal["stop"] = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage
