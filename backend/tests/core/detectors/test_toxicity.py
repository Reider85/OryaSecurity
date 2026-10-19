from __future__ import annotations

import pytest

from app.core.detectors.toxicity import ToxicityDetector, ToxicityResult


class TestToxicityDetector:
    """Heuristic mock toxicity detector."""

    @pytest.mark.asyncio
    async def test_clean_text_low_score(self):
        detector = ToxicityDetector(threshold=0.7)
        result = await detector.check("Hello, how are you today?")

        assert result.is_toxic is False
        assert result.toxicity_score == 0.0
        assert result.confidence > 0.5

    @pytest.mark.asyncio
    async def test_empty_text(self):
        detector = ToxicityDetector(threshold=0.7)
        result = await detector.check("")

        assert result.is_toxic is False
        assert result.toxicity_score == 0.0

    @pytest.mark.asyncio
    async def test_single_marker_below_threshold(self):
        detector = ToxicityDetector(threshold=0.7)
        result = await detector.check("you are an idiot")

        # 1 marker → score = 0.5, below 0.7 threshold
        assert result.toxicity_score == pytest.approx(0.5)
        assert result.is_toxic is False

    @pytest.mark.asyncio
    async def test_multiple_markers_exceed_threshold(self):
        detector = ToxicityDetector(threshold=0.7)
        result = await detector.check("idiot stupid shut up damn")

        # 4 markers → score = min(4/2, 1.0) = 1.0 > 0.7
        assert result.toxicity_score == pytest.approx(1.0)
        assert result.is_toxic is True

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        detector = ToxicityDetector(threshold=0.7)
        result = await detector.check("IDIOT STUPID")

        assert result.is_toxic is True

    @pytest.mark.asyncio
    async def test_to_dict(self):
        detector = ToxicityDetector(threshold=0.7)
        result = await detector.check("idiot stupid damn")

        d = result.to_dict()
        assert set(d.keys()) == {"toxicity_score", "is_toxic", "confidence"}

    @pytest.mark.asyncio
    async def test_custom_threshold(self):
        detector = ToxicityDetector(threshold=0.3)
        result = await detector.check("you are an idiot")

        # score 0.5 > 0.3 → toxic
        assert result.is_toxic is True
