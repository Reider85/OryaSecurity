"""Pydantic models for LLM Security Scanner SDK."""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class RuleMatch(BaseModel):
    """Information about a matched rule in scan results."""
    rule_id: str = Field(..., description="Unique identifier of the matched rule")
    rule_name: str = Field(..., description="Human-readable name of the rule")
    value_hash: str = Field(..., description="SHA256 hash of the matched value")
    position: List[int] = Field(..., description="Start and end position of match in text")
    severity: str = Field(..., description="Severity level: low, medium, high, critical")
    action: str = Field(..., description="Action taken: allow, block, log_only")


class ScanResult(BaseModel):
    """Result of a security scan operation."""
    verdict: str = Field(..., description="Final verdict: allow or block")
    reason: str = Field(..., description="Human-readable explanation of the verdict")
    latency_ms: float = Field(..., description="Latency of the scan operation in milliseconds")
    request_id: str = Field(..., description="Unique request identifier for tracing")
    rules_matched: List[RuleMatch] = Field(default=[], description="List of rules that matched")
    cache_hit: bool = Field(default=False, description="Whether result was served from cache")


class ChatCompletionMessage(BaseModel):
    """A message in a chat completion request/response."""
    role: str = Field(..., description="Role of the message: system, user, assistant")
    content: Optional[str] = Field(None, description="Content of the message")


class ChatCompletionChoice(BaseModel):
    """A choice in a chat completion response."""
    index: int = Field(default=0, description="Index of the choice")
    message: ChatCompletionMessage = Field(..., description="The chat message")
    finish_reason: Optional[str] = Field(default="stop", description="Reason for stopping generation")


class ChatCompletionUsage(BaseModel):
    """Usage information for a chat completion."""
    prompt_tokens: int = Field(default=0, description="Number of tokens in the prompt")
    completion_tokens: int = Field(default=0, description="Number of tokens in the completion")
    total_tokens: int = Field(default=0, description="Total number of tokens")


class ChatCompletionResponse(BaseModel):
    """Response from a chat completion operation."""
    id: str = Field(..., description="Unique identifier for the response")
    object: str = Field(default="chat.completion", description="Type of object")
    created: int = Field(..., description="Unix timestamp of when the response was created")
    model: str = Field(..., description="Model that was used for completion")
    choices: List[ChatCompletionChoice] = Field(..., description="List of completion choices")
    usage: ChatCompletionUsage = Field(default_factory=ChatCompletionUsage, description="Token usage information")


class ScannerError(Exception):
    """Base exception for SDK errors."""
    pass


class AuthenticationError(ScannerError):
    """Raised when authentication fails (401)."""
    pass


class RateLimitError(ScannerError):
    """Raised when rate limit is exceeded (429)."""
    def __init__(self, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.retry_after = retry_after


class ScannerAPIError(ScannerError):
    """Raised when the API returns an error response."""
    def __init__(self, message: str, status_code: int, response_text: str):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text