from __future__ import annotations

import pytest

from app.core.dos_protection import DoSProtector, DoSCheckResult, extract_prompt_from_json_body


class TestDoSCheckResult:
    """Test suite for DoSCheckResult dataclass."""
    
    def test_result_fields(self) -> None:
        """Test that DoSCheckResult has correct fields."""
        result = DoSCheckResult(
            ok=True,
            reason="test",
            complexity_score=100,
            estimated_tokens=50,
            length=200,
        )
        
        assert result.ok is True
        assert result.reason == "test"
        assert result.complexity_score == 100
        assert result.estimated_tokens == 50
        assert result.length == 200


class TestDoSProtector:
    """Test suite for DoSProtector class."""
    
    @pytest.fixture
    def protector(self) -> DoSProtector:
        """Create a DoSProtector with default settings."""
        return DoSProtector(
            max_prompt_chars=2000,  # Allow longer for token test
            max_prompt_tokens=500,
            complexity_threshold=1000,
        )
    
    @pytest.fixture
    def short_prompt(self) -> str:
        """A short, benign prompt."""
        return "Hello, world!"
    
    @pytest.fixture
    def long_prompt(self) -> str:
        """A prompt that exceeds max length."""
        return "x" * 1001
    
    @pytest.fixture
    def high_token_prompt(self) -> str:
        """A prompt with many tokens but short length."""
        # Single characters separated by spaces to maximize token count
        # 26 letters * 20 repetitions = 520 chars, ~173 tokens (520//3)
        return "a b c d e f g h i j k l m n o p q r s t u v w x y z " * 20
    
    @pytest.fixture
    def complex_prompt(self) -> str:
        """A prompt with high complexity score."""
        # Mix of ASCII, emoji, CJK, and special chars - keep under 1000 chars
        return (
            "Hello world! " * 30 +  # length component
            "🚀🔥📱💻" * 15 +  # emoji
            "你好世界" * 15 +  # CJK
            "!@#$%^&*()" * 15 +  # special chars
            "áéíóú" * 15  # Latin-1
        )
    
    def test_short_prompt_passes(self, protector: DoSProtector, short_prompt: str) -> None:
        """Test that short, benign prompts pass."""
        result = protector.check(short_prompt)
        
        assert result.ok is True
        assert result.reason == "ok"
        assert result.length == len(short_prompt)
        assert result.estimated_tokens is not None
        assert result.complexity_score > 0
    
    def test_long_prompt_fails(self) -> None:
        """Test that prompts exceeding max length fail."""
        # Use a protector with high complexity threshold but low length limit
        protector = DoSProtector(
            max_prompt_chars=1000,  # Low to trigger length limit
            max_prompt_tokens=2000,  # High to avoid token limit
            complexity_threshold=10000,  # High to avoid complexity check
        )
        
        # Create a prompt that exceeds length limit but has low complexity
        long_prompt = "x" * 1001
        
        result = protector.check(long_prompt)
        
        assert result.ok is False
        assert "prompt_too_long" in result.reason
        assert result.length == len(long_prompt)
        assert result.estimated_tokens is None
        assert result.complexity_score == 0
    
    def test_high_token_prompt_fails(self) -> None:
        """Test that prompts with too many tokens fail."""
        # Use a protector with low token limit but high complexity threshold
        protector = DoSProtector(
            max_prompt_chars=2000,
            max_prompt_tokens=100,  # Very low to trigger token limit
            complexity_threshold=10000,  # High to avoid complexity check
        )
        
        # Create a prompt with many tokens but low complexity
        # Single letters separated by spaces = max tokens for given length
        # 26 letters * 19 repetitions = 494 chars, ~164 tokens (494//3)
        high_token_prompt = "a b c d e f g h i j k l m n o p q r s t u v w x y z " * 19
        
        result = protector.check(high_token_prompt)
        
        assert result.ok is False
        assert "too_many_tokens" in result.reason
        assert result.estimated_tokens is not None
        assert result.estimated_tokens > 100
        assert result.length == len(high_token_prompt)
    
    def test_complex_prompt_fails(self) -> None:
        """Test that prompts with high complexity fail."""
        # Use a protector with high length/token limits but low complexity threshold
        protector = DoSProtector(
            max_prompt_chars=5000,  # High to avoid length/token limits
            max_prompt_tokens=2000,  # High to avoid token limit
            complexity_threshold=1000,  # Low to trigger complexity check
        )
        
        # Create a prompt with high complexity but moderate length
        complex_prompt = (
            "Hello world! " * 50 +  # length component
            "🚀🔥📱💻" * 10 +  # emoji
            "你好世界" * 10 +  # CJK
            "!@#$%^&*()" * 10 +  # special chars
            "áéíóú" * 10  # Latin-1
        )
        
        result = protector.check(complex_prompt)
        
        assert result.ok is False
        assert "complexity_exceeded" in result.reason
        assert result.complexity_score > 1000
        assert result.length == len(complex_prompt)
        assert result.estimated_tokens is not None
    
    def test_custom_thresholds(self) -> None:
        """Test that custom thresholds work."""
        # Protector with very high threshold
        protector = DoSProtector(complexity_threshold=100000)
        
        # A prompt that would normally fail
        complex_prompt = "Hello! " * 1000 + "🚀" * 100
        result = protector.check(complex_prompt)
        
        assert result.ok is True
        assert result.reason == "ok"
        assert result.complexity_score > 1000  # but < 100000
    
    def test_empty_prompt(self) -> None:
        """Test behavior with empty prompt."""
        protector = DoSProtector()
        result = protector.check("")
        
        assert result.ok is True
        assert result.reason == "ok"
        assert result.length == 0
        assert result.estimated_tokens == 0
        assert result.complexity_score == 0
    
    def test_unicode_prompt(self) -> None:
        """Test with Unicode characters."""
        protector = DoSProtector()
        unicode_prompt = "Привет! 你好! こんにちは! 🌍"
        
        result = protector.check(unicode_prompt)
        
        assert result.ok is True
        assert result.length == len(unicode_prompt)
        assert result.estimated_tokens is not None
        assert result.complexity_score > 0
    
    def test_complexity_formula_components(self) -> None:
        """Test that complexity formula components work as expected."""
        # Test with known components
        ascii_only = "Hello world! " * 100  # length=1300, special=0, diversity=low
        special_only = "!@#$%^&*()" * 100  # length=800, special=800, diversity=low
        mixed = ascii_only + special_only + "你好🌍"  # length=2100+, special=800+, diversity=high
        
        protector = DoSProtector(complexity_threshold=5000)
        
        ascii_result = protector.check(ascii_only)
        special_result = protector.check(special_only)
        mixed_result = protector.check(mixed)
        
        # Mixed should have highest complexity
        assert mixed_result.complexity_score > special_result.complexity_score
        assert special_result.complexity_score > ascii_result.complexity_score
        
        # All should pass with high threshold
        assert ascii_result.ok is True
        assert special_result.ok is True
        assert mixed_result.ok is True


class TestExtractPromptFromJsonBody:
    """Test suite for prompt extraction from JSON."""
    
    def test_scan_request_format(self) -> None:
        """Test extraction from ScanRequest format."""
        body = b'{"prompt": "Hello world!", "tenant_id": "test"}'
        
        prompt = extract_prompt_from_json_body(body)
        
        assert prompt == "Hello world!"
    
    def test_openai_format(self) -> None:
        """Test extraction from OpenAI messages format."""
        body = b'{"messages": [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}]}'
        
        prompt = extract_prompt_from_json_body(body)
        
        assert prompt == "Hi\nHello"
    
    def test_openai_format_empty_content(self) -> None:
        """Test extraction with None content in messages."""
        body = b'{"messages": [{"role": "user", "content": null}]}'
        
        prompt = extract_prompt_from_json_body(body)
        
        assert prompt == ""
    
    def test_no_prompt_field(self) -> None:
        """Test body without prompt field."""
        body = b'{"other_field": "value"}'
        
        prompt = extract_prompt_from_json_body(body)
        
        assert prompt is None
    
    def test_invalid_json(self) -> None:
        """Test invalid JSON body."""
        body = b'{"invalid": json}'
        
        prompt = extract_prompt_from_json_body(body)
        
        assert prompt is None
    
    def test_non_string_prompt(self) -> None:
        """Test non-string prompt field."""
        body = b'{"prompt": 123}'
        
        prompt = extract_prompt_from_json_body(body)
        
        assert prompt is None
    
    def test_messages_not_list(self) -> None:
        """Test messages field not a list."""
        body = b'{"messages": "not a list"}'
        
        prompt = extract_prompt_from_json_body(body)
        
        assert prompt is None
    
    def test_unicode_content(self) -> None:
        """Test Unicode content in messages."""
        body = '{"messages": [{"role": "user", "content": "Привет! 你好!"}]}'.encode('utf-8')
        
        prompt = extract_prompt_from_json_body(body)
        
        assert prompt == "Привет! 你好!"


__all__ = []