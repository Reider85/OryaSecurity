from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.cache import decision_cache
from app.core.pipeline.fast_path import FastPath, FastPathResult
from app.core.pipeline.suspicion_tagger import SuspicionTagger
from app.core.rules.base import Verdict, RuleMatch
from app.core.pdp import scan_text


@pytest.fixture(autouse=True)
def use_fake_redis(fake_redis, monkeypatch: pytest.MonkeyPatch):
    """Replace Redis connection with fake Redis for cache operations."""
    async def _get_redis():
        return fake_redis
    monkeypatch.setattr("app.core.cache.get_redis_connection", _get_redis)
    return fake_redis


@pytest.fixture
def mock_rule_set():
    """Mock rule set for testing."""
    mock = MagicMock()
    mock.return_value = ([], "test_hash")  # No matches, hash
    return mock


@pytest.fixture
def fast_path_instance(mock_rule_set):
    """FastPath instance with mocked rule set and tagger."""
    return FastPath(rule_set=mock_rule_set, tagger=SuspicionTagger(rule_set=mock_rule_set))


class TestFastPath:
    """Test suite for FastPath class."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_verdict(self, fast_path_instance: FastPath, fake_redis):
        """Test that cache hit returns cached verdict immediately."""
        # Setup fake cache data
        cached_data = {
            "verdict": "block",
            "reason": "cached_block",
            "rules_matched": [{"rule_id": "test_rule", "rule_name": "Test Rule", "value_hash": "abc", "position": [0, 10], "severity": "high", "action": "block"}],
            "ts": 1234567890.0,
        }
        fake_redis.set("abc", '{"verdict": "block", "reason": "cached_block", "rules_matched": [{"rule_id": "test_rule"}], "ts": 1234567890.0, "expires_at": 1234567890.0 + 300}')

        # Mock scan_text to avoid additional processing
        with patch("app.core.pipeline.fast_path.scan_text", return_value=([], "test_hash")):
            result = await fast_path_instance.process("test prompt")

        # Verify result
        assert result.verdict is not None
        assert result.verdict.action == "block"
        assert result.verdict.reason == "cached_block"
        assert result.cache_hit is True
        assert result.is_suspicious is False
        assert result.latency_ms > 0
        assert "cache_ms" in result.breakdown

    @pytest.mark.asyncio
    async def test_cache_miss_no_matches_short_prompt_allows(self, fast_path_instance: FastPath, mock_rule_set):
        """Test cache miss with no regex matches and short prompt returns allow."""
        # Mock rule set to return no matches
        mock_rule_set.return_value = ([], "test_hash")

        result = await fast_path_instance.process("short prompt")

        # Verify result
        assert result.verdict is not None
        assert result.verdict.action == "allow"
        assert result.verdict.reason == "fast_path_clean"
        assert result.cache_hit is False
        assert result.is_suspicious is False
        assert result.latency_ms > 0

    @pytest.mark.asyncio
    async def test_cache_miss_with_matches_marks_suspicious(self, fast_path_instance: FastPath, mock_rule_set):
        """Test cache miss with regex matches marks as suspicious."""
        # Mock rule set to return matches
        mock_rule_set.return_value = (
            [RuleMatch(rule_id="test_rule", rule_name="Test Rule", value_hash="abc", position=(0, 10), severity="high", action="block")],
            "test_hash"
        )

        result = await fast_path_instance.process("suspicious prompt with matches")

        # Verify result
        assert result.verdict is None
        assert result.cache_hit is False
        assert result.is_suspicious is True
        assert result.latency_ms > 0

    @pytest.mark.asyncio
    async def test_long_prompt_without_matches_suspicious(self, fast_path_instance: FastPath, mock_rule_set):
        """Test long prompt without regex matches is suspicious due to length."""
        # Create a long prompt (over 200 tokens)
        long_prompt = "word " * 201  # 201 words = ~201 tokens
        mock_rule_set.return_value = ([], "test_hash")

        result = await fast_path_instance.process(long_prompt)

        # Verify result
        assert result.verdict is None
        assert result.cache_hit is False
        assert result.is_suspicious is True
        assert "length" in fast_path_instance.tagger.is_suspicious(long_prompt).reasons

    @pytest.mark.asyncio
    async def test_very_short_prompt_not_suspicious(self, fast_path_instance: FastPath, mock_rule_set):
        """Test very short prompt is not suspicious (filtered out)."""
        # Mock rule set to return no matches
        mock_rule_set.return_value = ([], "test_hash")

        result = await fast_path_instance.process("hi")

        # Verify result
        assert result.verdict is not None
        assert result.verdict.action == "allow"
        assert result.cache_hit is False
        assert result.is_suspicious is False
        assert "too_short" in fast_path_instance.tagger.is_suspicious("hi").reasons

    @pytest.mark.asyncio
    async def test_latency_breakdown_contains_all_components(self, fast_path_instance: FastPath, mock_rule_set):
        """Test that latency breakdown includes all expected components."""
        mock_rule_set.return_value = ([], "test_hash")

        result = await fast_path_instance.process("test prompt")

        # Verify breakdown contains all components
        breakdown = result.breakdown
        assert "cache_ms" in breakdown
        assert "rule_ms" in breakdown
        assert "suspicion_ms" in breakdown
        assert "total_ms" in breakdown
        assert breakdown["total_ms"] == result.latency_ms

    @pytest.mark.asyncio
    async def test_redis_error_degrades_to_miss(self, fast_path_instance: FastPath):
        """Test that Redis errors degrade to cache miss gracefully."""
        # Simulate Redis error by raising exception
        with patch("app.core.cache.get_redis_connection", side_effect=Exception("Redis connection failed")):
            result = await fast_path_instance.process("test prompt")

        # Should degrade to miss, not raise
        assert result.verdict is None
        assert result.cache_hit is False
        assert result.is_suspicious is True  # No cache, will go to suspicion check

    def test_count_tokens_heuristic_in_tagger(self, fast_path_instance: FastPath):
        """Test token counting heuristic via tagger."""
        assert fast_path_instance.tagger._count_tokens_heuristic("hello world") == 2
        assert fast_path_instance.tagger._count_tokens_heuristic("one two three four") == 4
        assert fast_path_instance.tagger._count_tokens_heuristic("") == 0
        assert fast_path_instance.tagger._count_tokens_heuristic("word " * 100) == 100

    def test_is_suspicious_by_length_in_tagger(self, fast_path_instance: FastPath):
        """Test length-based suspicion detection via tagger."""
        # Short prompt
        assert not fast_path_instance.tagger.is_suspicious("short").is_suspicious
        # Long prompt
        long_text = "word " * 201
        assert fast_path_instance.tagger.is_suspicious(long_text).is_suspicious

    @pytest.mark.asyncio
    async def test_custom_rule_set_dependency_injection(self, mock_rule_set):
        """Test that custom rule set can be injected."""
        custom_fast_path = FastPath(rule_set=mock_rule_set)
        result = await custom_fast_path.process("test")

        # Should use the custom rule set
        mock_rule_set.assert_called_once_with("test")
        assert result is not None

    @pytest.mark.asyncio
    async def test_suspicion_result_reasons_format(self, fast_path_instance: FastPath, mock_rule_set):
        """Test that suspicion result provides proper reasons via tagger."""
        # Test with matches
        mock_rule_set.return_value = (
            [RuleMatch(rule_id="test_rule", rule_name="Test Rule", value_hash="abc", position=(0, 10), severity="high", action="block")],
            "test_hash"
        )
        result = fast_path_instance.tagger.is_suspicious("test with matches")
        assert "regex_matches=1" in result.reasons

        # Test with length
        long_text = "word " * 201
        result = fast_path_instance.tagger.is_suspicious(long_text)
        assert "length=201" in result.reasons

        # Test with both
        mock_rule_set.return_value = (
            [RuleMatch(rule_id="test_rule", rule_name="Test Rule", value_hash="abc", position=(0, 10), severity="high", action="block")],
            "test_hash"
        )
        long_with_matches = "word " * 201 + " suspicious_content"
        result = fast_path_instance.tagger.is_suspicious(long_with_matches)
        assert "length=201" in result.reasons
        assert "regex_matches=1" in result.reasons