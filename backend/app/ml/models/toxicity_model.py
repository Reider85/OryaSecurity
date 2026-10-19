from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Optional

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer


class ToxicityModel:
    """ONNX toxicity classifier for streaming chunks.
    
    Loads a RoBERTa-based toxicity model from ONNX format with int8 quantization.
    Designed for fast inference on short text chunks (8-32 tokens).
    """

    def __init__(
        self,
        model_path: str,
        tokenizer_path: str,
        toxic_label_index: int = 1,
        max_length: int = 64,
    ):
        """Initialize the toxicity model.
        
        Args:
            model_path: Path to ONNX model file (.onnx)
            tokenizer_path: Path to tokenizer directory or tokenizer.json file
            toxic_label_index: Index of 'toxic' class in model output logits
            max_length: Max token length for inference (short chunks = faster)
        """
        self.model_path = Path(model_path)
        self.tokenizer_path = Path(tokenizer_path)
        self.toxic_label_index = toxic_label_index
        self.max_length = max_length
        
        # Initialize model and tokenizer
        self._session: Optional[ort.InferenceSession] = None
        self._tokenizer: Optional[AutoTokenizer] = None
        
    @classmethod
    def load(
        cls,
        model_path: str,
        tokenizer_path: str,
        toxic_label_index: int = 1,
        max_length: int = 64,
    ) -> ToxicityModel:
        """Load model from paths.
        
        Args:
            model_path: Path to ONNX model file
            tokenizer_path: Path to tokenizer directory
            toxic_label_index: Index of toxic class in logits
            max_length: Max sequence length
            
        Returns:
            Loaded ToxicityModel instance
            
        Raises:
            FileNotFoundError: If model or tokenizer files don't exist
            RuntimeError: If model loading fails
        """
        if not Path(model_path).exists():
            raise FileNotFoundError(f"Model file not found: {model_path}")
        if not Path(tokenizer_path).exists():
            raise FileNotFoundError(f"Tokenizer not found: {tokenizer_path}")
            
        instance = cls(model_path, tokenizer_path, toxic_label_index, max_length)
        instance._load_session()
        instance._load_tokenizer()
        return instance
    
    def _load_session(self) -> None:
        """Load ONNX inference session."""
        try:
            # Use CPUExecutionProvider for consistent performance across environments
            self._session = ort.InferenceSession(
                str(self.model_path),
                providers=["CPUExecutionProvider"],
            )
        except Exception as e:
            raise RuntimeError(f"Failed to load ONNX model: {e}")
    
    def _load_tokenizer(self) -> None:
        """Load tokenizer from HF directory."""
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(str(self.tokenizer_path))
        except Exception as e:
            raise RuntimeError(f"Failed to load tokenizer: {e}")
    
    def predict(self, text: str) -> tuple[float, float]:
        """Predict toxicity score for input text.
        
        Args:
            text: Input text (typically 8-32 tokens)
            
        Returns:
            Tuple of (toxicity_score, confidence)
            - toxicity_score: Probability of toxic class (0.0-1.0)
            - confidence: Maximum probability across classes (0.0-1.0)
        """
        if self._session is None or self._tokenizer is None:
            raise RuntimeError("Model not loaded")
            
        # Tokenize with truncation
        inputs = self._tokenizer(
            text,
            return_tensors="np",
            max_length=self.max_length,
            truncation=True,
            padding="max_length",
        )
        
        # Convert to int64 (required by ONNX Runtime)
        inputs = {k: v.astype(np.int64) for k, v in inputs.items()}
        
        # Run inference
        outputs = self._session.run(None, inputs)
        logits = outputs[0]  # Shape: [1, num_classes]
        
        # Apply softmax to get probabilities
        probs = self._softmax(logits[0])
        
        # Extract toxicity score and confidence
        toxic_score = float(probs[self.toxic_label_index])
        confidence = float(np.max(probs))
        
        return toxic_score, confidence
    
    @staticmethod
    def _softmax(x: np.ndarray) -> np.ndarray:
        """Compute softmax for logits."""
        exp_x = np.exp(x - np.max(x))
        return exp_x / exp_x.sum()
    
    def is_available(self) -> bool:
        """Check if model is loaded and ready."""
        return self._session is not None and self._tokenizer is not None


# Global singleton instance
_model: Optional[ToxicityModel] = None


def load_toxicity_model(
    model_path: str,
    tokenizer_path: str,
    toxic_label_index: int = 1,
    max_length: int = 64,
) -> ToxicityModel:
    """Load or reload the global toxicity model singleton.
    
    Args:
        model_path: Path to ONNX model file
        tokenizer_path: Path to tokenizer directory
        toxic_label_index: Index of toxic class in logits
        max_length: Max sequence length
        
    Returns:
        Loaded ToxicityModel instance
    """
    global _model
    _model = ToxicityModel.load(
        model_path, tokenizer_path, toxic_label_index, max_length
    )
    return _model


def get_toxicity_model() -> Optional[ToxicityModel]:
    """Get the global toxicity model singleton.
    
    Returns:
        ToxicityModel instance or None if not loaded
    """
    return _model


def set_toxicity_model(model: Optional[ToxicityModel]) -> None:
    """Set the global toxicity model singleton.
    
    Args:
        model: ToxicityModel instance or None
    """
    global _model
    _model = model