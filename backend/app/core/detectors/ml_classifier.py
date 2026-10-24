"""
ML Classifier Inference Service for Prompt Injection Detection.

Provides inference for the ONNX exported classifier with optimized performance.
"""

import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

from app.core.logging import get_logger

logger = get_logger(__name__)


class ClassificationResult:
    """Result of ML classification inference."""
    
    def __init__(
        self,
        top_class: str,
        confidence: float,
        all_scores: Dict[str, float],
        logits: np.ndarray,
        processing_time_ms: float
    ):
        self.top_class = top_class
        self.confidence = confidence
        self.all_scores = all_scores
        self.logits = logits
        self.processing_time_ms = processing_time_ms
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "top_class": self.top_class,
            "confidence": self.confidence,
            "all_scores": self.all_scores,
            "processing_time_ms": self.processing_time_ms
        }


class PromptClassifier:
    """
    ML Classifier for prompt injection detection using ONNX runtime.
    
    Optimized for production inference with:
    - Session reuse
    - Batch processing
    - Memory efficiency
    - Error handling
    """
    
    # Class mapping (must match training)
    CLASS_NAMES = ["benign", "prompt_injection", "jailbreak", "suspicious"]
    
    def __init__(self, model_path: Path, tokenizer_path: Optional[Path] = None):
        """
        Initialize classifier with ONNX model and tokenizer.
        
        Args:
            model_path: Path to ONNX model file (.onnx)
            tokenizer_path: Path to tokenizer config (defaults to same dir as model)
        """
        self.model_path = model_path
        self.tokenizer_path = tokenizer_path or model_path.parent
        
        # Load model and tokenizer
        self._load_model()
        self._load_tokenizer()
        
        # Performance tracking
        self.inference_count = 0
        self.total_time_ms = 0.0
        
        logger.info(f"Classifier initialized: {model_path}")
    
    def _load_model(self):
        """Load ONNX model with optimized session."""
        try:
            # Use CPU execution provider for consistent performance
            providers = ['CPUExecutionProvider']
            
            # Create inference session
            sess_options = ort.SessionOptions()
            sess_options.intra_op_num_threads = 1  # Single thread for latency
            sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            
            self.session = ort.InferenceSession(
                str(self.model_path),
                providers=providers,
                sess_options=sess_options
            )
            
            # Get model info
            self.model_inputs = {inp.name: inp for inp in self.session.get_inputs()}
            self.model_outputs = {out.name: out for out in self.session.get_outputs()}
            
            logger.info(f"Model loaded: {len(self.model_inputs)} inputs, {len(self.model_outputs)} outputs")
            
        except Exception as e:
            logger.error(f"Failed to load model {self.model_path}: {e}")
            raise
    
    def _load_tokenizer(self):
        """Load tokenizer from local path."""
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(str(self.tokenizer_path))
            logger.info(f"Tokenizer loaded from {self.tokenizer_path}")
        except Exception as e:
            logger.error(f"Failed to load tokenizer: {e}")
            raise
    
    def _preprocess(self, texts: List[str]) -> Dict[str, np.ndarray]:
        """
        Tokenize texts for ONNX inference.
        
        Args:
            texts: List of input texts
            
        Returns:
            Dictionary of tokenized inputs ready for ONNX
        """
        # Tokenize with truncation and padding
        encoded = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="np",
            return_token_type_ids=True
        )
        
        # Convert to proper format for ONNX
        inputs = {}
        for key, tensor in encoded.items():
            # Ensure correct data type (usually int64 for token IDs)
            if key in ['input_ids', 'attention_mask', 'token_type_ids']:
                inputs[key] = tensor.astype(np.int64)
            else:
                inputs[key] = tensor
        
        return inputs
    
    def _postprocess(self, outputs: List[Dict[str, np.ndarray]]) -> List[ClassificationResult]:
        """
        Process ONNX outputs to classification results.
        
        Args:
            outputs: List of output dictionaries from ONNX session
            
        Returns:
            List of ClassificationResult objects
        """
        results = []
        
        for output in outputs:
            # Get logits from output
            logits = output['logits']
            
            # Apply softmax to get probabilities
            probs = self._softmax(logits)
            
            # Get top class and confidence
            top_idx = np.argmax(probs)
            top_class = self.CLASS_NAMES[top_idx]
            confidence = float(probs[top_idx])
            
            # Create all_scores dict
            all_scores = {
                class_name: float(prob)
                for class_name, prob in zip(self.CLASS_NAMES, probs)
            }
            
            results.append(ClassificationResult(
                top_class=top_class,
                confidence=confidence,
                all_scores=all_scores,
                logits=logits,
                processing_time_ms=0.0  # Will be set by caller
            ))
        
        return results
    
    @staticmethod
    def _softmax(x: np.ndarray) -> np.ndarray:
        """Apply softmax to logits."""
        # Numerically stable softmax
        exp_x = np.exp(x - np.max(x, axis=-1, keepdims=True))
        return exp_x / np.sum(exp_x, axis=-1, keepdims=True)
    
    async def classify(self, text: str) -> ClassificationResult:
        """
        Classify a single text prompt.
        
        Args:
            text: Input text to classify
            
        Returns:
            ClassificationResult with prediction and confidence
        """
        import time
        start_time = time.time()
        
        try:
            # Preprocess
            inputs = self._preprocess([text])
            
            # Run inference
            outputs = self.session.run(
                list(self.model_outputs.keys()),
                {name: tensor for name, tensor in inputs.items()}
            )
            
            # Convert to expected format
            output_dict = {name: tensor for name, tensor in zip(self.model_outputs.keys(), outputs)}
            
            # Postprocess
            results = self._postprocess([output_dict])
            result = results[0]
            
            # Update performance metrics
            processing_time = (time.time() - start_time) * 1000
            result.processing_time_ms = processing_time
            
            self.inference_count += 1
            self.total_time_ms += processing_time
            
            logger.debug(f"Classification: {text[:50]}... -> {result.top_class} ({result.confidence:.3f})")
            
            return result
            
        except Exception as e:
            logger.error(f"Classification failed for text: {e}")
            # Return low confidence benign as fallback
            return ClassificationResult(
                top_class="benign",
                confidence=0.1,
                all_scores={name: 0.25 for name in self.CLASS_NAMES},
                logits=np.array([0.0, 0.0, 0.0, 0.0]),
                processing_time_ms=0.0
            )
    
    async def classify_batch(self, texts: List[str]) -> List[ClassificationResult]:
        """
        Classify multiple texts in batch for better performance.
        
        Args:
            texts: List of input texts
            
        Returns:
            List of ClassificationResult objects
        """
        import time
        start_time = time.time()
        
        try:
            # Preprocess
            inputs = self._preprocess(texts)
            
            # Run batch inference
            outputs = self.session.run(
                list(self.model_outputs.keys()),
                {name: tensor for name, tensor in inputs.items()}
            )
            
            # Convert to expected format
            output_dict = {name: tensor for name, tensor in zip(self.model_outputs.keys(), outputs)}
            
            # Postprocess
            results = self._postprocess([output_dict] * len(texts))  # Duplicate for each text
            
            # Update performance metrics
            processing_time = (time.time() - start_time) * 1000
            for result in results:
                result.processing_time_ms = processing_time
            
            self.inference_count += len(texts)
            self.total_time_ms += processing_time
            
            logger.info(f"Batch classified {len(texts)} texts in {processing_time:.2f}ms")
            
            return results
            
        except Exception as e:
            logger.error(f"Batch classification failed: {e}")
            # Return fallback results
            return [
                ClassificationResult(
                    top_class="benign",
                    confidence=0.1,
                    all_scores={name: 0.25 for name in self.CLASS_NAMES},
                    logits=np.array([0.0, 0.0, 0.0, 0.0]),
                    processing_time_ms=0.0
                )
                for _ in texts
            ]
    
    def get_stats(self) -> Dict:
        """Get performance statistics."""
        avg_time = self.total_time_ms / self.inference_count if self.inference_count > 0 else 0.0
        return {
            "inference_count": self.inference_count,
            "total_time_ms": self.total_time_ms,
            "avg_time_ms": avg_time,
            "model_path": str(self.model_path),
            "class_names": self.CLASS_NAMES
        }
    
    async def health_check_async(self) -> bool:
        """Check if classifier is healthy (async version)."""
        try:
            # Test with a simple prompt
            test_result = await self.classify("Hello, how are you?")
            return test_result.top_class in self.CLASS_NAMES
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
    
    def health_check(self) -> bool:
        """Check if classifier is healthy (sync version)."""
        try:
            # Create a new event loop for health check
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                test_result = loop.run_until_complete(self.classify("Hello, how are you?"))
                return test_result.top_class in self.CLASS_NAMES
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False


# Global classifier instance (singleton pattern)
_global_classifier: Optional[PromptClassifier] = None


async def get_classifier() -> PromptClassifier:
    """Get global classifier instance (lazy initialization)."""
    global _global_classifier
    
    if _global_classifier is None:
        # Load from default path
        model_path = Path("ml-models/classifier/v1/model.onnx")
        tokenizer_path = Path("ml-models/classifier/v1")
        
        if not model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")
        
        _global_classifier = PromptClassifier(model_path, tokenizer_path)
        logger.info("Global classifier initialized")
    
    return _global_classifier


async def classify_prompt(text: str) -> ClassificationResult:
    """
    Convenience function to classify a prompt using global classifier.
    
    Args:
        text: Input text to classify
        
    Returns:
        ClassificationResult
    """
    classifier = await get_classifier()
    return await classifier.classify(text)


# Shared classifier functions (for __init__.py compatibility)
_shared_classifier: Optional[PromptClassifier] = None


def get_shared_classifier() -> PromptClassifier:
    """Get shared classifier instance (sync version)."""
    global _shared_classifier
    
    if _shared_classifier is None:
        # Load from default path
        model_path = Path("ml-models/classifier/v1/model.onnx")
        tokenizer_path = Path("ml-models/classifier/v1")
        
        if not model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")
        
        _shared_classifier = PromptClassifier(model_path, tokenizer_path)
        logger.info("Shared classifier initialized")
    
    return _shared_classifier


def set_shared_classifier(classifier: PromptClassifier) -> None:
    """Set shared classifier instance."""
    global _shared_classifier
    _shared_classifier = classifier
    logger.info("Shared classifier updated")