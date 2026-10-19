from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.detectors.toxicity import ToxicityDetector, ToxicityResult, get_shared_toxicity_detector, set_shared_toxicity_detector


class TestToxicityDetector:
    """Heuristic and ML toxicity detector tests."""

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
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_single_marker_below_threshold(self):
        detector = ToxicityDetector(threshold=0.7)
        result = await detector.check("you are an idiot")

        # 1 marker → score = 0.5, below 0.7 threshold
        assert result.toxicity_score == pytest.approx(0.5)
        assert result.is_toxic is False
        assert result.confidence == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_multiple_markers_exceed_threshold(self):
        detector = ToxicityDetector(threshold=0.7)
        result = await detector.check("idiot stupid shut up damn")

        # 4 markers → score = min(4/2, 1.0) = 1.0 > 0.7
        assert result.toxicity_score == pytest.approx(1.0)
        assert result.is_toxic is True
        assert result.confidence == pytest.approx(0.6)

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

    @pytest.mark.asyncio
    async def test_ml_model_integration(self):
        """Test ML model path with fake model."""
        # Create fake model
        fake_model = MagicMock()
        fake_model.predict.return_value = (0.8, 0.9)  # toxic score, confidence
        
        # Create detector with fake model
        with patch('app.core.detectors.toxicity.get_toxicity_model', return_value=fake_model):
            detector = ToxicityDetector(threshold=0.7)
            result = await detector.check("This is toxic content")

            # Verify ML model was called
            fake_model.predict.assert_called_once_with("This is toxic content")
            
            # Verify result from ML model
            assert result.toxicity_score == 0.8
            assert result.is_toxic is True
            assert result.confidence == 0.9

    @pytest.mark.asyncio
    async def test_ml_model_fallback_to_heuristic(self):
        """Test fallback to heuristic when ML model fails."""
        # Create fake model that raises exception
        fake_model = MagicMock()
        fake_model.predict.side_effect = Exception("Model inference failed")
        
        with patch('app.core.detectors.toxicity.get_toxicity_model', return_value=fake_model):
            detector = ToxicityDetector(threshold=0.7)
            result = await detector.check("you are an idiot")

            # Verify fallback to heuristic
            assert result.toxicity_score == pytest.approx(0.5)  # heuristic score
            assert result.is_toxic is False  # below threshold
            assert result.confidence == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_ml_model_non_toxic(self):
        """Test ML model for non-toxic content."""
        fake_model = MagicMock()
        fake_model.predict.return_value = (0.2, 0.8)  # non-toxic score, confidence
        
        with patch('app.core.detectors.toxicity.get_toxicity_model', return_value=fake_model):
            detector = ToxicityDetector(threshold=0.7)
            result = await detector.check("Hello, this is a normal message")

            assert result.toxicity_score == 0.2
            assert result.is_toxic is False
            assert result.confidence == 0.8

    @pytest.mark.asyncio
    async def test_ml_model_threshold_boundary(self):
        """Test ML model at threshold boundary."""
        # Test just below threshold
        fake_model = MagicMock()
        fake_model.predict.return_value = (0.69, 0.85)
        
        with patch('app.core.detectors.toxicity.get_toxicity_model', return_value=fake_model):
            detector = ToxicityDetector(threshold=0.7)
            result = await detector.check("borderline content")
            
            assert result.is_toxic is False

        # Test just above threshold
        fake_model.predict.return_value = (0.71, 0.85)
        
        with patch('app.core.detectors.toxicity.get_toxicity_model', return_value=fake_model):
            detector = ToxicityDetector(threshold=0.7)
            result = await detector.check("borderline content")
            
            assert result.is_toxic is True

    @pytest.mark.asyncio
    async def test_concurrent_checks(self):
        """Test concurrent toxicity checks."""
        fake_model = MagicMock()
        fake_model.predict.side_effect = lambda text: (0.8 if "toxic" in text else 0.2, 0.9)
        
        with patch('app.core.detectors.toxicity.get_toxicity_model', return_value=fake_model):
            detector = ToxicityDetector(threshold=0.7)
            
            # Run multiple concurrent checks
            tasks = [
                detector.check("toxic message 1"),
                detector.check("normal message"),
                detector.check("toxic message 2"),
            ]
            
            results = await asyncio.gather(*tasks)
            
            # Verify all calls completed
            assert len(results) == 3
            assert results[0].is_toxic is True
            assert results[1].is_toxic is False
            assert results[2].is_toxic is True
            
            # Verify model was called for each text
            assert fake_model.predict.call_count == 3


class TestSharedDetector:
    """Test global shared detector functionality."""

    def test_get_set_shared_detector(self):
        """Test setting and getting shared detector."""
        detector1 = ToxicityDetector(threshold=0.5)
        set_shared_toxicity_detector(detector1)
        
        retrieved = get_shared_toxicity_detector()
        assert retrieved is detector1
        
        # Set to None
        set_shared_toxicity_detector(None)
        assert get_shared_toxicity_detector() is None

    def test_shared_detector_integration(self):
        """Test that shared detector works correctly."""
        detector = ToxicityDetector(threshold=0.5)
        set_shared_toxicity_detector(detector)
        
        retrieved = get_shared_toxicity_detector()
        assert retrieved is not None
        assert retrieved.threshold == 0.5
        
        # Test that it can still perform checks
        import asyncio
        result = asyncio.run(retrieved.check("test message"))
        assert isinstance(result, ToxicityResult)