from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class DoSCheckResult:
    """Result of DoS protection check."""
    
    ok: bool
    reason: str
    complexity_score: int
    estimated_tokens: int | None = None
    length: int | None = None


class DoSProtector:
    """DoS protection for prompt inputs.
    
    Checks prompt length, estimated token count, and complexity score.
    Returns 413 for rejected prompts with audit event.
    """
    
    def __init__(
        self,
        *,
        max_prompt_chars: int = 32_768,
        max_prompt_tokens: int = 8_192,
        complexity_threshold: int = 40_000,
    ):
        self.max_prompt_chars = max_prompt_chars
        self.max_prompt_tokens = max_prompt_tokens
        self.complexity_threshold = complexity_threshold
    
    def check(self, prompt: str) -> DoSCheckResult:
        """Check prompt for DoS protection criteria.
        
        Args:
            prompt: Input text to check
            
        Returns:
            DoSCheckResult with check result and metadata
        """
        # Length check
        length = len(prompt)
        if length > self.max_prompt_chars:
            return DoSCheckResult(
                ok=False,
                reason=f"prompt_too_long:{length}",
                complexity_score=0,
                length=length,
                estimated_tokens=None,
            )
        
        # Token estimation (conservative: ~1 token per 3 chars for multi-byte text)
        estimated_tokens = length // 3
        if estimated_tokens > self.max_prompt_tokens:
            return DoSCheckResult(
                ok=False,
                reason=f"too_many_tokens:{estimated_tokens}",
                complexity_score=0,
                length=length,
                estimated_tokens=estimated_tokens,
            )
        
        # Complexity score calculation
        complexity_score = self._calculate_complexity(prompt)
        
        if complexity_score > self.complexity_threshold:
            return DoSCheckResult(
                ok=False,
                reason=f"complexity_exceeded:{complexity_score}",
                complexity_score=complexity_score,
                length=length,
                estimated_tokens=estimated_tokens,
            )
        
        return DoSCheckResult(
            ok=True,
            reason="ok",
            complexity_score=complexity_score,
            length=length,
            estimated_tokens=estimated_tokens,
        )
    
    def _calculate_complexity(self, prompt: str) -> int:
        """Calculate complexity score as sum of length, special chars, and encoding diversity.
        
        Complexity formula: length + special_chars + encoding_diversity
        - length: raw character count
        - special_chars: count of non-alphanumeric, non-space characters
        - encoding_diversity: number of distinct Unicode general categories
        """
        # Length component
        length_component = len(prompt)
        
        # Special characters component (non-alphanumeric, non-space)
        special_chars_component = sum(
            1 for c in prompt 
            if not c.isalnum() and not c.isspace()
        )
        
        # Encoding diversity component (Unicode general categories)
        try:
            unicode_categories = {unicodedata.category(c) for c in prompt if c.isprintable()}
            encoding_diversity_component = len(unicode_categories)
        except Exception:
            # Fallback if unicodedata fails (e.g., control chars)
            encoding_diversity_component = 1
        
        return length_component + special_chars_component + encoding_diversity_component


def hash_prompt(prompt: str) -> str:
    """Generate SHA256 hash of prompt for audit logging."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def extract_prompt_from_json_body(body: bytes) -> str | None:
    """Extract prompt text from JSON request body.
    
    Supports:
    - {"prompt": "..."} (ScanRequest format)
    - {"messages": [{"role": "user", "content": "..."}]} (OpenAI format)
    
    Args:
        body: Raw request body bytes
        
    Returns:
        Extracted prompt text or None if not found
    """
    try:
        import json
        data = json.loads(body.decode("utf-8"))
        
        # ScanRequest format: {"prompt": "..."}
        if isinstance(data, dict) and "prompt" in data:
            prompt = data["prompt"]
            if isinstance(prompt, str):
                return prompt
        
        # OpenAI format: {"messages": [...]}
        elif isinstance(data, dict) and "messages" in data:
            messages = data["messages"]
            if isinstance(messages, list):
                parts = []
                for msg in messages:
                    if isinstance(msg, dict) and "content" in msg:
                        content = msg["content"]
                        if isinstance(content, str):
                            parts.append(content)
                return "\n".join(parts)
        
        return None
        
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
        return None


__all__ = [
    "DoSCheckResult",
    "DoSProtector",
    "hash_prompt",
    "extract_prompt_from_json_body",
]