"""
ML Classifier Inference Service for Prompt Injection Detection.

Provides inference for the ONNX exported classifier with optimized performance.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from app.core.logging import get_logger
from app.core.metrics import scanner_ml_classifier_duration_seconds
from app.ml.models.classifier_model import ClassifierModel, get_classifier_model, set_classifier_model

logger = get_logger(__name__)

# Class mapping (must match training)
CLASS_NAMES = ["benign", "prompt_injection", "jailbreak", "suspicious"]


class ClassificationResult:
    """Result of ML classification inference."""
    
    def __init__(
        self,
        top_class: str,
        confidence: float,
        all_classes: Dict[str, float],
        logits: Optional[np.ndarray] = None,
        processing_time_ms: float = 0.0,
        # Legacy alias for backward compatibility
        all_scores: Optional[Dict[str, float]] = None,
    ):
        """
        Initialize classification result.
        
        Args:
            top_class: Predicted class name
            confidence: Confidence score (0.0-1.0)
            all_classes: Dictionary of all class probabilities
            logits: Raw logits array (optional, for explainability)
            processing_time_ms: Time taken for classification in milliseconds
            all_scores: Legacy alias for all_classes (deprecated)
        """
        self.top_class = top_class
        self.confidence = confidence
        self.all_classes = all_classes
        self.logits = logits
        self.processing_time_ms = processing_time_ms
        
        # Handle legacy all_scores parameter
        if all_scores is not None:
            self.all_classes = all_scores
    
    @property
    def all_scores(self) -> Dict[str, float]:
        """Legacy property for backward compatibility."""
        return self.all_classes
    
    @property
    def is_suspicious(self) -> bool:
        """Check if result indicates suspicious content."""
        return self.top_class in ("prompt_injection", "jailbreak", "suspicious")
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "top_class": self.top_class,
            "confidence": self.confidence,
            "all_classes": self.all_classes,
            "processing_time_ms": self.processing_time_ms,
        }


class PromptClassifier:
    """
    ML Classifier for prompt injection detection using ONNX runtime.
    
    Optimized for production inference with:
    - Session reuse
    - Batch processing
    - Memory efficiency
    - Error handling
    - Graceful degradation
    """
    
    def __init__(
        self,
        model_path: str | Path,
        tokenizer_path: Optional[str | Path] = None,
        max_length: int = 512,
        flag_threshold: float = 0.85,
    ):
        """
        Initialize classifier with ONNX model and tokenizer.
        
        Args:
            model_path: Path to ONNX model file (.onnx)
            tokenizer_path: Path to tokenizer config (defaults to same dir as model)
            max_length: Max token length for inference
            flag_threshold: Confidence threshold for flagging suspicious content
        """
        self.model_path = Path(model_path)
        self.tokenizer_path = Path(tokenizer_path) if tokenizer_path else self.model_path.parent
        self.max_length = max_length
        self.flag_threshold = flag_threshold
        
        # Load model and tokenizer with graceful degradation
        self._model: Optional[ClassifierModel] = None
        self._available = False
        self._load()
        
        # Performance tracking
        self.inference_count = 0
        self.total_time_ms = 0.0
    
    def _load(self) -> None:
        """Load model with graceful degradation."""
        try:
            self._model = ClassifierModel(
                self.model_path,
                self.tokenizer_path,
                self.max_length,
            )
            self._available = True
            logger.info(f"Classifier loaded: {self.model_path}")
        except Exception as e:
            self._model = None
            self._available = False
            logger.warning("classifier_load_failed", error=str(e), fallback="disabled")
    
    @property
    def available(self) -> bool:
        """Check if classifier is available and ready."""
        return self._available and self._model is not None and self._model.is_available()
    
    def should_flag(self, result: ClassificationResult) -> bool:
        """
        Determine if result should be flagged based on threshold.
        
        Args:
            result: Classification result
            
        Returns:
            True if result should be flagged
        """
        return result.is_suspicious and result.confidence >= self.flag_threshold
    
    async def classify(self, text: str) -> Optional[ClassificationResult]:
        """
        Classify a single text prompt.
        
        Args:
            text: Input text to classify
            
        Returns:
            ClassificationResult with prediction and confidence, or None if unavailable
        """
        if not self.available:
            return None
        
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, self._classify_sync, text)
            return result
        except Exception as e:
            logger.error(f"Classification failed for text: {e}")
            return None
    
    def _classify_sync(self, text: str) -> ClassificationResult:
        """Synchronous classification for executor."""
        start_time = time.time()
        
        try:
            # Get probabilities from model
            probs = self._model.predict([text])
            
            # Get top class and confidence
            probs_flat = probs[0]
            top_idx = np.argmax(probs_flat)
            top_class = CLASS_NAMES[top_idx]
            confidence = float(probs_flat[top_idx])
            
            # Create all_classes dict
            all_classes = {
                class_name: float(prob)
                for class_name, prob in zip(CLASS_NAMES, probs_flat)
            }
            
            # Update performance metrics
            processing_time = (time.time() - start_time) * 1000
            self.inference_count += 1
            self.total_time_ms += processing_time
            
            # Record latency metric
            scanner_ml_classifier_duration_seconds.observe(processing_time / 1000)
            
            logger.debug(f"Classification: {text[:50]}... -> {top_class} ({confidence:.3f})")
            
            return ClassificationResult(
                top_class=top_class,
                confidence=confidence,
                all_classes=all_classes,
                processing_time_ms=processing_time,
            )
            
        except Exception as e:
            logger.error(f"Classification failed: {e}")
            # Return low confidence benign as fallback
            return ClassificationResult(
                top_class="benign",
                confidence=0.1,
                all_classes={name: 0.25 for name in CLASS_NAMES},
                processing_time_ms=0.0,
            )
    
    async def classify_batch(self, texts: List[str]) -> List[Optional[ClassificationResult]]:
        """
        Classify multiple texts in batch for better performance.
        
        Args:
            texts: List of input texts
            
        Returns:
            List of ClassificationResult objects or None for unavailable
        """
        if not self.available:
            return [None] * len(texts)
        
        try:
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(None, self._classify_batch_sync, texts)
            return results
        except Exception as e:
            logger.error(f"Batch classification failed: {e}")
            # Return fallback results
            return [
                ClassificationResult(
                    top_class="benign",
                    confidence=0.1,
                    all_classes={name: 0.25 for name in CLASS_NAMES},
                    processing_time_ms=0.0,
                )
                for _ in texts
            ]
    
    def _classify_batch_sync(self, texts: List[str]) -> List[ClassificationResult]:
        """Synchronous batch classification for executor."""
        start_time = time.time()
        
        try:
            # Get probabilities from model
            probs = self._model.predict(texts)
            
            # Create results for each text
            results = []
            for prob_row in probs:
                # Get top class and confidence
                top_idx = np.argmax(prob_row)
                top_class = CLASS_NAMES[top_idx]
                confidence = float(prob_row[top_idx])
                
                # Create all_classes dict
                all_classes = {
                    class_name: float(prob)
                    for class_name, prob in zip(CLASS_NAMES, prob_row)
                }
                
                results.append(ClassificationResult(
                    top_class=top_class,
                    confidence=confidence,
                    all_classes=all_classes,
                    processing_time_ms=0.0,  # Will be set below
                ))
            
            # Update performance metrics
            processing_time = (time.time() - start_time) * 1000
            self.inference_count += len(texts)
            self.total_time_ms += processing_time
            
            # Record latency metric
            scanner_ml_classifier_duration_seconds.observe(processing_time / 1000)
            
            # Set processing time for all results
            for result in results:
                result.processing_time_ms = processing_time
            
            logger.info(f"Batch classified {len(texts)} texts in {processing_time:.2f}ms")
            
            return results
            
        except Exception as e:
            logger.error(f"Batch classification failed: {e}")
            # Return fallback results
            return [
                ClassificationResult(
                    top_class="benign",
                    confidence=0.1,
                    all_classes={name: 0.25 for name in CLASS_NAMES},
                    processing_time_ms=0.0,
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
            "max_length": self.max_length,
            "flag_threshold": self.flag_threshold,
            "available": self.available,
        }
    
    async def health_check_async(self) -> bool:
        """Check if classifier is healthy (async version)."""
        if not self.available:
            return False
        
        try:
            result = await self.classify("Hello, how are you?")
            return result is not None and result.top_class in CLASS_NAMES
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
    
    def health_check(self) -> bool:
        """Check if classifier is healthy (sync version)."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(self.classify("Hello, how are you?"))
                return result is not None and result.top_class in CLASS_NAMES
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False


# Global classifier instances
_global_classifier: Optional[PromptClassifier] = None


async def get_classifier() -> Optional[PromptClassifier]:
    """Get global classifier instance (lazy initialization)."""
    global _global_classifier
    
    if _global_classifier is None:
        # Load from config settings
        from app.config import settings
        
        _global_classifier = PromptClassifier(
            model_path=settings.classifier_model_path,
            tokenizer_path=settings.classifier_tokenizer_path,
            max_length=settings.classifier_max_length,
            flag_threshold=settings.classifier_flag_threshold,
        )
        logger.info("Global classifier initialized")
    
    return _global_classifier


async def classify_prompt(text: str) -> Optional[ClassificationResult]:
    """
    Convenience function to classify a prompt using global classifier.
    
    Args:
        text: Input text to classify
        
    Returns:
        ClassificationResult or None if unavailable
    """
    classifier = await get_classifier()
    return await classifier.classify(text) if classifier else None


# Shared classifier functions (for __init__.py compatibility)
_shared_classifier: Optional[PromptClassifier] = None


def get_shared_classifier() -> Optional[PromptClassifier]:
    """Get shared classifier instance (sync version)."""
    global _shared_classifier
    
    if _shared_classifier is None:
        # Load from config settings
        from app.config import settings
        
        _shared_classifier = PromptClassifier(
            model_path=settings.classifier_model_path,
            tokenizer_path=settings.classifier_tokenizer_path,
            max_length=settings.classifier_max_length,
            flag_threshold=settings.classifier_flag_threshold,
        )
        logger.info("Shared classifier initialized")
    
    return _shared_classifier


def set_shared_classifier(classifier: Optional[PromptClassifier]) -> None:
    """Set shared classifier instance."""
    global _shared_classifier
    _shared_classifier = classifier
    logger.info("Shared classifier updated")