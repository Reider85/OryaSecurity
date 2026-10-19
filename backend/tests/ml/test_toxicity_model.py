from __future__ import annotations

import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from app.ml.models.toxicity_model import ToxicityModel, get_toxicity_model, set_toxicity_model


class TestToxicityModel:
    """Test the ONNX toxicity model wrapper."""

    def test_init(self):
        """Test model initialization."""
        model = ToxicityModel(
            model_path="fake_model.onnx",
            tokenizer_path="fake_tokenizer",
            toxic_label_index=1,
            max_length=64
        )
        
        assert model.model_path == "fake_model.onnx"
        assert model.tokenizer_path == "fake_tokenizer"
        assert model.toxic_label_index == 1
        assert model.max_length == 64
        assert model._session is None
        assert model._tokenizer is None

    def test_load_success(self):
        """Test successful model loading."""
        fake_session = MagicMock()
        fake_tokenizer = MagicMock()
        
        with patch('app.ml.models.toxicity_model.ort.InferenceSession') as mock_session, \
             patch('app.ml.models.toxicity_model.AutoTokenizer') as mock_tokenizer:
            
            mock_session.return_value = fake_session
            mock_tokenizer.from_pretrained.return_value = fake_tokenizer
            
            model = ToxicityModel.load(
                "fake_model.onnx",
                "fake_tokenizer",
                toxic_label_index=1,
                max_length=64
            )
            
            assert model._session is fake_session
            assert model._tokenizer is fake_tokenizer
            mock_session.assert_called_once()
            mock_tokenizer.from_pretrained.assert_called_once_with("fake_tokenizer")

    def test_load_file_not_found(self):
        """Test model loading with missing files."""
        with pytest.raises(FileNotFoundError, match="Model file not found"):
            ToxicityModel.load("missing_model.onnx", "missing_tokenizer")

    def test_predict_success(self):
        """Test successful prediction."""
        fake_session = MagicMock()
        fake_tokenizer = MagicMock()
        
        # Mock tokenizer output
        fake_tokenizer.return_value = {
            'input_ids': np.array([[1, 2, 3]]),
            'attention_mask': np.array([[1, 1, 1]])
        }
        
        # Mock model output (logits)
        fake_session.run.return_value = [np.array([[0.1, 0.9]])]  # [non-toxic, toxic]
        
        model = ToxicityModel("fake_model.onnx", "fake_tokenizer")
        model._session = fake_session
        model._tokenizer = fake_tokenizer
        
        score, confidence = model.predict("test text")
        
        # Verify softmax: [0.1, 0.9] → [0.2689, 0.7311]
        # toxic_label_index=1 → score = 0.7311, confidence = 0.7311
        assert score == pytest.approx(0.7311, rel=1e-3)
        assert confidence == pytest.approx(0.7311, rel=1e-3)
        
        # Verify tokenizer was called with correct parameters
        fake_tokenizer.assert_called_once_with(
            "test text",
            return_tensors="np",
            max_length=64,
            truncation=True,
            padding="max_length"
        )
        
        # Verify model was called with converted inputs
        fake_session.run.assert_called_once()
        call_args = fake_session.run.call_args[0][0]  # first positional arg (inputs)
        assert 'input_ids' in call_args
        assert 'attention_mask' in call_args

    def test_predict_empty_text(self):
        """Test prediction with empty text."""
        fake_session = MagicMock()
        fake_tokenizer = MagicMock()
        
        fake_tokenizer.return_value = {
            'input_ids': np.array([[0]]),  # padding
            'attention_mask': np.array([[0]])
        }
        fake_session.run.return_value = [np.array([[0.1, 0.9]])]
        
        model = ToxicityModel("fake_model.onnx", "fake_tokenizer")
        model._session = fake_session
        model._tokenizer = fake_tokenizer
        
        score, confidence = model.predict("")
        
        # Should still work with empty string (padding)
        assert 0.0 <= score <= 1.0
        assert 0.0 <= confidence <= 1.0

    def test_predict_not_loaded(self):
        """Test prediction when model is not loaded."""
        model = ToxicityModel("fake_model.onnx", "fake_tokenizer")
        
        with pytest.raises(RuntimeError, match="Model not loaded"):
            model.predict("test text")

    def test_is_available(self):
        """Test availability check."""
        model = ToxicityModel("fake_model.onnx", "fake_tokenizer")
        
        # Not available initially
        assert not model.is_available()
        
        # After loading
        fake_session = MagicMock()
        fake_tokenizer = MagicMock()
        model._session = fake_session
        model._tokenizer = fake_tokenizer
        
        assert model.is_available()

    def test_softmax(self):
        """Test softmax implementation."""
        model = ToxicityModel("fake_model.onnx", "fake_tokenizer")
        
        # Test basic softmax
        x = np.array([1.0, 2.0, 3.0])
        result = model._softmax(x)
        expected = np.exp([1.0, 2.0, 3.0]) / np.exp([1.0, 2.0, 3.0]).sum()
        np.testing.assert_array_almost_equal(result, expected)
        
        # Test numerical stability
        large_x = np.array([100.0, 200.0, 300.0])
        result = model._softmax(large_x)
        assert np.allclose(result.sum(), 1.0)
        assert result[2] > result[1] > result[0]

    def test_custom_toxic_label_index(self):
        """Test with custom toxic label index."""
        fake_session = MagicMock()
        fake_tokenizer = MagicMock()
        
        fake_tokenizer.return_value = {
            'input_ids': np.array([[1, 2, 3]]),
            'attention_mask': np.array([[1, 1, 1]])
        }
        fake_session.run.return_value = [np.array([[0.9, 0.1]])]  # [toxic, non-toxic]
        
        model = ToxicityModel("fake_model.onnx", "fake_tokenizer", toxic_label_index=0)
        model._session = fake_session
        model._tokenizer = fake_tokenizer
        
        score, confidence = model.predict("test text")
        
        # toxic_label_index=0 → score = 0.9
        assert score == pytest.approx(0.9, rel=1e-3)
        assert confidence == pytest.approx(0.9, rel=1e-3)


class TestToxicityModelSingleton:
    """Test global singleton functionality."""

    def test_get_set_singleton(self):
        """Test setting and getting singleton."""
        model = ToxicityModel("fake_model.onnx", "fake_tokenizer")
        
        # Initially None
        assert get_toxicity_model() is None
        
        # Set and get
        set_toxicity_model(model)
        assert get_toxicity_model() is model
        
        # Set to None
        set_toxicity_model(None)
        assert get_toxicity_model() is None

    def test_load_toxicity_model(self):
        """Test the load_toxicity_model function."""
        fake_session = MagicMock()
        fake_tokenizer = MagicMock()
        
        with patch('app.ml.models.toxicity_model.ort.InferenceSession') as mock_session, \
             patch('app.ml.models.toxicity_model.AutoTokenizer') as mock_tokenizer:
            
            mock_session.return_value = fake_session
            mock_tokenizer.from_pretrained.return_value = fake_tokenizer
            
            model = load_toxicity_model(
                "fake_model.onnx",
                "fake_tokenizer",
                toxic_label_index=1,
                max_length=64
            )
            
            assert model is get_toxicity_model()
            assert model._session is fake_session
            assert model._tokenizer is fake_tokenizer