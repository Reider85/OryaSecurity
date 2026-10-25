"""
Tests for ML Classifier Model wrapper.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import app.ml.models.classifier_model as classifier_model_module
from app.ml.models.classifier_model import (
    ClassifierModel,
    get_classifier_model,
    load_classifier_model,
    set_classifier_model,
)


@pytest.fixture(autouse=True)
def reset_classifier_model_singleton():
    """Keep the module-level singleton isolated between tests."""
    classifier_model_module._model = None
    yield
    classifier_model_module._model = None


class TestClassifierModel:
    """Test ClassifierModel class."""
    
    @pytest.fixture
    def mock_model_path(self, tmp_path):
        """Create a temporary model file."""
        model_path = tmp_path / "model.onnx"
        model_path.write_bytes(b"dummy onnx content")
        return model_path
    
    @pytest.fixture
    def mock_tokenizer_path(self, tmp_path):
        """Create a temporary tokenizer directory."""
        tokenizer_path = tmp_path / "tokenizer"
        tokenizer_path.mkdir()
        return tokenizer_path
    
    @pytest.fixture
    def model(self, mock_model_path, mock_tokenizer_path):
        """Create a test model with mocked dependencies."""
        # Mock tokenizer
        with patch("app.ml.models.classifier_model.AutoTokenizer") as mock_tokenizer_class:
            mock_tokenizer = MagicMock()
            mock_tokenizer_class.from_pretrained.return_value = mock_tokenizer
            
            # Mock ONNX session
            with patch("onnxruntime.InferenceSession") as mock_session_class:
                mock_session = MagicMock()
                mock_session.get_inputs.return_value = [
                    MagicMock(name="input_ids"),
                    MagicMock(name="attention_mask")
                ]
                mock_session.get_outputs.return_value = [
                    MagicMock(name="logits")
                ]
                # Mock the session.run method to return the expected output
                mock_session.run.return_value = [np.array([[1.0, 0.0, 0.0, 0.0]])]
                mock_session_class.return_value = mock_session
                
                model = ClassifierModel(mock_model_path, mock_tokenizer_path)
                
                # Set input_names after model creation to match the mocked session
                model.input_names = ["input_ids", "attention_mask"]
                
                yield model
    
    def test_init(self, mock_model_path, mock_tokenizer_path):
        """Test model initialization."""
        with patch("app.ml.models.classifier_model.AutoTokenizer") as mock_tokenizer_class:
            mock_tokenizer = MagicMock()
            mock_tokenizer_class.from_pretrained.return_value = mock_tokenizer
            
            with patch("onnxruntime.InferenceSession") as mock_session_class:
                mock_session = MagicMock()
                mock_session.get_inputs.return_value = [
                    MagicMock(name="input_ids"),
                    MagicMock(name="attention_mask")
                ]
                mock_session.get_outputs.return_value = [MagicMock(name="logits")]
                mock_session_class.return_value = mock_session
                
                model = ClassifierModel(mock_model_path, mock_tokenizer_path)
                model.input_names = ["input_ids", "attention_mask"]
                
                assert model.model_path == mock_model_path
                assert model.tokenizer_path == mock_tokenizer_path
                assert model.max_length == 512
                assert model.is_available()
    
    def test_init_load_failure(self, mock_tokenizer_path):
        """Test model loading failure."""
        non_existent_path = Path("non_existent_model.onnx")
        
        with pytest.raises(Exception):
            ClassifierModel(non_existent_path, mock_tokenizer_path)
    
    def test_is_available(self, model):
        """Test is_available property."""
        assert model.is_available()
        
        # Test unavailable
        model._session = None
        assert not model.is_available()
    
    def test_preprocess(self, model):
        """Test text preprocessing."""
        texts = ["Hello world", "This is a test"]
        model._tokenizer.return_value = {
            "input_ids": np.array([[1, 2, 3], [4, 5, 6]]),
            "attention_mask": np.array([[1, 1, 1], [1, 1, 1]]),
            "token_type_ids": np.array([[0, 0, 0], [0, 0, 0]])
        }

        inputs = model._preprocess(texts)

        assert set(inputs) == {"input_ids", "attention_mask"}
        assert inputs["input_ids"].dtype == np.int64
        assert inputs["attention_mask"].dtype == np.int64
    
    def test_run_inference(self, model):
        """Test inference execution."""
        inputs = {
            "input_ids": np.array([[1, 2, 3]]),
            "attention_mask": np.array([[1, 1, 1]])
        }
        
        logits = model._run_inference(inputs)
        
        assert logits.shape == (1, 4)
        assert np.allclose(logits[0], [1.0, 0.0, 0.0, 0.0])
    
    def test_predict_success(self, model):
        """Test successful prediction."""
        texts = ["Hello world", "This is a test"]
        
        # Mock successful inference
        model._session.run.return_value = [np.array([[0.9, 0.05, 0.03, 0.02], [0.8, 0.1, 0.05, 0.05]])]
        
        probs = model.predict(texts)
        
        assert probs.shape == (2, 4)
        assert np.allclose(probs.sum(axis=1), 1.0)
        assert model.inference_count == 2
        assert model.total_time_ms > 0
    
    def test_predict_failure(self, model):
        """Test prediction failure handling."""
        # Mock inference failure
        model._session.run.side_effect = Exception("Inference failed")
        
        texts = ["Hello world"]
        probs = model.predict(texts)
        
        # Should return uniform distribution as fallback
        expected = np.array([[0.25, 0.25, 0.25, 0.25]])
        assert np.allclose(probs, expected)
    
    def test_predict_unavailable(self, model):
        """Unavailable model: _preprocess guards, predict degrades to uniform probs."""
        with patch.object(model, "is_available", return_value=False):
            with pytest.raises(RuntimeError, match="Model not available"):
                model._preprocess(["Hello world"])

            probs = model.predict(["Hello world"])

        assert probs.shape == (1, 4)
        assert np.allclose(probs, 0.25)
    
    def test_get_stats(self, model):
        """Test performance statistics."""
        # Simulate some predictions
        model.inference_count = 10
        model.total_time_ms = 100.0
        
        stats = model.get_stats()
        
        assert stats["inference_count"] == 10
        assert stats["total_time_ms"] == 100.0
        assert stats["avg_time_ms"] == 10.0
        assert stats["model_path"] == str(model.model_path)
        assert stats["max_length"] == 512


class TestClassifierModelSingleton:
    """Test ClassifierModel singleton functions."""
    
    def test_load_classifier_model(self, tmp_path):
        """Test loading classifier model singleton."""
        model_path = tmp_path / "model.onnx"
        model_path.write_bytes(b"dummy onnx content")
        tokenizer_path = tmp_path / "tokenizer"
        tokenizer_path.mkdir()

        with patch("app.ml.models.classifier_model.ClassifierModel") as mock_model_class:
            mock_model = MagicMock()
            mock_model_class.return_value = mock_model

            model = load_classifier_model(str(model_path), str(tokenizer_path))

            assert model is mock_model
            mock_model_class.assert_called_once_with(model_path, tokenizer_path, 512)
            assert classifier_model_module._model is mock_model
    
    def test_get_classifier_model(self):
        """Test getting classifier model singleton."""
        mock_model = MagicMock()
        classifier_model_module._model = mock_model

        assert get_classifier_model() is mock_model

    def test_get_classifier_model_none(self):
        """Test getting classifier model when none loaded."""
        assert get_classifier_model() is None

    def test_set_classifier_model(self):
        """Test setting classifier model singleton."""
        mock_model = MagicMock()

        set_classifier_model(mock_model)

        assert classifier_model_module._model is mock_model
        assert get_classifier_model() is mock_model

    def test_set_classifier_model_none(self):
        """Test setting classifier model singleton to None."""
        set_classifier_model(MagicMock())
        set_classifier_model(None)

        assert classifier_model_module._model is None
        assert get_classifier_model() is None


class TestClassifierModelSoftmax:
    """Test ClassifierModel softmax function."""
    
    def test_softmax(self):
        """Test softmax function."""
        logits = np.array([[1.0, 2.0, 0.0, 0.5]])
        probs = ClassifierModel._softmax(logits)
        
        assert probs.shape == (1, 4)
        assert np.allclose(probs.sum(axis=1), 1.0)
        assert probs[0][1] > probs[0][0]  # 2.0 > 1.0
    
    def test_softmax_numerical_stability(self):
        """Test softmax numerical stability."""
        logits = np.array([[1000.0, 1001.0, 0.0, 0.0]])
        probs = ClassifierModel._softmax(logits)
        
        assert probs.shape == (1, 4)
        assert np.allclose(probs.sum(axis=1), 1.0)
        assert probs[0][1] > probs[0][0]  # 1001.0 > 1000.0