from __future__ import annotations

from pydantic import BaseModel, Field


class ChatCompletionMessage(BaseModel):
    role: str = Field(..., description="Message role: system, user, assistant")
    content: str | None = Field(None, description="Message content")


class ChatCompletionRequest(BaseModel):
    model: str = Field("gpt-3.5-turbo", description="Model name (ignored, forwarded to configured LLM)")
    messages: list[ChatCompletionMessage] = Field(..., min_length=1)
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(None, ge=1)
    top_p: float | None = Field(None, ge=0.0, le=1.0)
    frequency_penalty: float | None = Field(None, ge=-2.0, le=2.0)
    presence_penalty: float | None = Field(None, ge=-2.0, le=2.0)
    stop: str | list[str] | None = None
    stream: bool = False


class ChatCompletionUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatCompletionMessage
    finish_reason: str | None = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage = Field(default_factory=ChatCompletionUsage)
