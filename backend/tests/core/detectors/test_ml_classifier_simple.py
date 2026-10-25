"""
Simple tests for ML Classifier inference service.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.core.detectors.ml_classifier import ClassificationResult, PromptClassifier, CLASS_NAMES


@pytest.fixture(autouse=True)
def reset_classifier_singletons():
    """Isolate module-level singletons between tests."""
    import app.core.detectors.ml_classifier as ml_classifier_module

    ml_classifier_module._global_classifier = None
    ml_classifier_module._shared_classifier = None
    yield
    ml_classifier_module._global_classifier = None
    ml_classifier_module._shared_classifier = None


class TestClassificationResult:
    """Test ClassificationResult class."""
    
    def test_to_dict(self):
        """Test serialization to dictionary."""
        result = ClassificationResult(
            top_class="benign",
            confidence=0.95,
            all_classes={"benign": 0.95, "prompt_injection": 0.05},
            logits=np.array([1.0, 0.0, 0.0, 0.0]),
            processing_time_ms=10.5
        )
        
        result_dict = result.to_dict()
        
        assert result_dict["top_class"] == "benign"
        assert result_dict["confidence"] == 0.95
        assert result_dict["all_classes"]["benign"] == 0.95
        assert result_dict["processing_time_ms"] == 10.5
    
    def test_legacy_all_scores_alias(self):
        """Test backward compatibility with all_scores parameter."""
        result = ClassificationResult(
            top_class="prompt_injection",
            confidence=0.92,
            all_classes={"benign": 0.08, "prompt_injection": 0.92},
            all_scores={"benign": 0.08, "prompt_injection": 0.92}  # legacy
        )
        
        assert result.all_classes["prompt_injection"] == 0.92
        assert result.all_scores["prompt_injection"] == 0.92  # property alias


class TestPromptClassifierLogic:
    """Test PromptClassifier logic without real model loading."""
    
    def test_class_names_constant(self):
        """Test class names are correct."""
        assert CLASS_NAMES == ["benign", "prompt_injection", "jailbreak", "suspicious"]


class TestGlobalClassifier:
    """Test global classifier functionality."""
    
    @pytest.mark.asyncio
    async def test_get_classifier_singleton(self):
        """Test that get_classifier returns the same instance."""
        with patch("app.core.detectors.ml_classifier.PromptClassifier") as mock_classifier_class:
            mock_classifier = MagicMock()
            mock_classifier.available = True
            mock_classifier_class.return_value = mock_classifier
            
            # Get classifier twice
            classifier1 = await get_classifier()
            classifier2 = await get_classifier()
            
            # Should be the same instance
            assert classifier1 is classifier2
            assert mock_classifier_class.call_count == 1
    
    @pytest.mark.asyncio
    async def test_get_classifier_returns_instance_when_unavailable(self):
        """get_classifier returns the instance even when the model failed to load."""
        with patch("app.core.detectors.ml_classifier.PromptClassifier") as mock_classifier_class:
            mock_classifier = MagicMock()
            mock_classifier.available = False
            mock_classifier_class.return_value = mock_classifier
            
            classifier = await get_classifier()
            
            # Callers check .available themselves (graceful degrade, not None)
            assert classifier is mock_classifier
            assert classifier.available is False


# Import the convenience function at the bottom to avoid circular imports
from app.core.detectors.ml_classifier import get_classifier, classify_prompt


@pytest.mark.asyncio
async def test_classify_prompt_convenience_function():
    """Test the convenience function for prompt classification."""
    with patch("app.core.detectors.ml_classifier.get_classifier") as mock_get_classifier:
        mock_classifier = AsyncMock()
        mock_result = ClassificationResult(
            top_class="benign",
            confidence=0.95,
            all_classes={"benign": 0.95},
            logits=np.array([1.0]),
            processing_time_ms=10.0
        )
        mock_classifier.classify.return_value = mock_result
        mock_get_classifier.return_value = mock_classifier
        
        result = await classify_prompt("Hello world")
        
        assert result.top_class == "benign"
        assert result.confidence == 0.95
        mock_classifier.classify.assert_called_once_with("Hello world")