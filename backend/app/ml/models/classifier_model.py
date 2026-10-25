"""
ML Classifier Model — Low-level ONNX inference wrapper.

Provides optimized ONNX inference for prompt-injection detection classifier.
Similar to toxicity_model.py but specialized for DeBERTa-v3-small.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer


class ClassifierModel:
    """
    ONNX classifier model for prompt-injection detection.
    
    Wraps an exported ONNX graph of DeBERTa-v3-small with optimized inference.
    Designed for batch processing with graceful degradation if unavailable.
    """
    
    def __init__(
        self,
        model_path: Path,
        tokenizer_path: Path,
        max_length: int = 512,
    ):
        """
        Initialize classifier model with ONNX model and tokenizer.
        
        Args:
            model_path: Path to ONNX model file (.onnx)
            tokenizer_path: Path to tokenizer directory or tokenizer.json file
            max_length: Max token length for inference
        """
        self.model_path = model_path
        self.tokenizer_path = tokenizer_path
        self.max_length = max_length
        
        # Model and tokenizer
        self._session: Optional[ort.InferenceSession] = None
        self._tokenizer: Optional[AutoTokenizer] = None
        
        # Performance tracking
        self.inference_count = 0
        self.total_time_ms = 0.0
        
        # Load model and tokenizer with graceful degradation
        self._load()
    
    def _load(self) -> None:
        """Load model and tokenizer, mark available if successful."""
        try:
            if not self.model_path.exists():
                raise FileNotFoundError(f"Model not found: {self.model_path}")
            if not self.tokenizer_path.exists():
                raise FileNotFoundError(f"Tokenizer not found: {self.tokenizer_path}")
            
            # Load tokenizer
            self._tokenizer = AutoTokenizer.from_pretrained(str(self.tokenizer_path))
            
            # Load ONNX session
            providers = ["CPUExecutionProvider"]
            self._session = ort.InferenceSession(
                str(self.model_path),
                providers=providers,
            )
            
            # Get input/output names
            self.input_names = [inp.name for inp in self._session.get_inputs()]
            self.output_names = [out.name for out in self._session.get_outputs()]
            
        except Exception as e:
            # Log but don't raise — graceful degradation
            self._session = None
            self._tokenizer = None
            # Logging should be done by caller or via is_available check
            raise
    
    def is_available(self) -> bool:
        """Check if model is loaded and ready."""
        return self._session is not None and self._tokenizer is not None
    
    def _preprocess(self, texts: List[str]) -> Dict[str, np.ndarray]:
        """
        Tokenize texts for ONNX inference.
        
        Args:
            texts: List of input texts
            
        Returns:
            Dictionary of tokenized inputs ready for ONNX
        """
        if not self.is_available():
            raise RuntimeError("Model not available")
        
        # Tokenize with truncation and padding
        encoded = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="np",
            return_token_type_ids=True,
        )
        
        # Filter to only inputs that the ONNX model expects
        inputs = {}
        for key, tensor in encoded.items():
            if key in self.input_names:
                # Ensure correct data type (usually int64 for token IDs)
                inputs[key] = tensor.astype(np.int64)
            else:
                # Skip unused inputs (e.g., token_type_ids if not expected)
                continue
        
        return inputs
    
    def _run_inference(self, inputs: Dict[str, np.ndarray]) -> np.ndarray:
        """
        Run ONNX inference on preprocessed inputs.
        
        Args:
            inputs: Dictionary of tokenized inputs
            
        Returns:
            Raw logits array [batch, num_classes]
        """
        outputs = self._session.run(
            self.output_names,
            inputs,
        )
        
        # Expected output: logits [batch, num_classes]
        logits = outputs[0]
        return logits
    
    @staticmethod
    def _softmax(x: np.ndarray) -> np.ndarray:
        """Apply softmax to logits."""
        # Numerically stable softmax
        exp_x = np.exp(x - np.max(x, axis=-1, keepdims=True))
        return exp_x / np.sum(exp_x, axis=-1, keepdims=True)
    
    def predict(self, texts: List[str]) -> np.ndarray:
        """
        Predict probabilities for input texts.
        
        Args:
            texts: List of input texts
            
        Returns:
            Probabilities array [batch, num_classes]
        """
        import time
        start_time = time.time()
        
        try:
            # Preprocess
            inputs = self._preprocess(texts)
            
            # Run inference
            logits = self._run_inference(inputs)
            
            # Apply softmax to get probabilities
            probs = self._softmax(logits)
            
            # Update performance metrics
            processing_time = (time.time() - start_time) * 1000
            self.inference_count += len(texts)
            self.total_time_ms += processing_time
            
            return probs
            
        except Exception as e:
            # Return uniform distribution as fallback
            num_classes = 4  # benign, prompt_injection, jailbreak, suspicious
            fallback_probs = np.full((len(texts), num_classes), 1.0 / num_classes)
            return fallback_probs
    
    def get_stats(self) -> Dict[str, Any]:
        """Get performance statistics."""
        avg_time = self.total_time_ms / self.inference_count if self.inference_count > 0 else 0.0
        return {
            "inference_count": self.inference_count,
            "total_time_ms": self.total_time_ms,
            "avg_time_ms": avg_time,
            "model_path": str(self.model_path),
            "max_length": self.max_length,
        }


# Global singleton instance
_model: Optional[ClassifierModel] = None


def load_classifier_model(
    model_path: str,
    tokenizer_path: str,
    max_length: int = 512,
) -> ClassifierModel:
    """Load or reload the global classifier model singleton.
    
    Args:
        model_path: Path to ONNX model file
        tokenizer_path: Path to tokenizer directory
        max_length: Max sequence length
        
    Returns:
        Loaded ClassifierModel instance
    """
    global _model
    _model = ClassifierModel(
        Path(model_path),
        Path(tokenizer_path),
        max_length,
    )
    return _model


def get_classifier_model() -> Optional[ClassifierModel]:
    """Get the global classifier model singleton.
    
    Returns:
        ClassifierModel instance or None if not loaded
    """
    return _model


def set_classifier_model(model: Optional[ClassifierModel]) -> None:
    """Set the global classifier model singleton.
    
    Args:
        model: ClassifierModel instance or None
    """
    global _model
    _model = model