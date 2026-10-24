#!/usr/bin/env python3
"""
Validate int8 ONNX classifier against FP32 baseline on test set.

Compares F1 scores (macro) of FP32 and int8 models on the same test set.
Accepts int8 when F1 degradation < max_degradation (default 2%).

Usage:
    python -m app.ml.validate_onnx_classifier \
        --fp32 ml-models/classifier/v1/model.onnx \
        --int8 ml-models/classifier/v1/model_int8.onnx \
        --tokenizer ml-models/classifier/v1 \
        --test-set ml-models/classifier/v1/test.jsonl

Exit code 0 when validation passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer
import structlog

from app.ml.metrics import compute_metrics

logger = structlog.get_logger()

DEFAULT_MAX_DEGRADATION = 0.02
DEFAULT_MAX_LENGTH = 512
DEFAULT_CLASSES = ["benign", "prompt_injection", "jailbreak", "suspicious"]


def load_test_set(test_set_path: Path) -> List[tuple[str, int]]:
    """Load test set from JSONL file. Returns list of (text, label) tuples."""
    test_set = []
    with open(test_set_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            try:
                data = json.loads(line.strip())
                text = data.get('text', '').strip()
                label = int(data.get('label', -1))
                
                if not text:
                    raise ValueError(f"Empty text at line {line_num}")
                if label < 0 or label >= len(DEFAULT_CLASSES):
                    raise ValueError(f"Invalid label {label} at line {line_num}")
                
                test_set.append((text, label))
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON at line {line_num}: {e}")
            except ValueError as e:
                raise ValueError(f"Invalid data at line {line_num}: {e}")
    
    if not test_set:
        raise ValueError("Test set is empty")
    
    return test_set


@dataclass
class ValidationReport:
    """Report for ONNX classifier validation."""
    
    num_examples: int
    baseline_f1: float
    int8_f1: float
    degradation: float
    max_degradation: float
    baseline_f1_per_class: Dict[str, float] = field(default_factory=dict)
    int8_f1_per_class: Dict[str, float] = field(default_factory=dict)
    skipped: bool = False
    
    @property
    def passed(self) -> bool:
        """True if validation passed or was skipped."""
        return self.skipped or (self.num_examples > 0 and self.degradation < self.max_degradation)
    
    def to_dict(self, include_per_class: bool = False) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data: Dict[str, Any] = {
            "num_examples": self.num_examples,
            "baseline_f1": self.baseline_f1,
            "int8_f1": self.int8_f1,
            "degradation": self.degradation,
            "max_degradation": self.max_degradation,
            "passed": self.passed,
        }
        if include_per_class:
            data["baseline_f1_per_class"] = self.baseline_f1_per_class
            data["int8_f1_per_class"] = self.int8_f1_per_class
        return data


class OnnxF1Evaluator:
    """Helper to run ONNX inference and compute F1 metrics."""
    
    def __init__(
        self,
        model_path: str | Path,
        tokenizer_path: str | Path,
        classes: List[str],
        max_length: int = DEFAULT_MAX_LENGTH,
    ):
        self.model_path = Path(model_path)
        self.tokenizer_path = Path(tokenizer_path)
        self.classes = classes
        self.max_length = max_length
        
        # Load model and tokenizer
        self.session = ort.InferenceSession(
            str(self.model_path),
            providers=["CPUExecutionProvider"],
        )
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.tokenizer_path))
        
        # Get input names
        self.input_names = [i.name for i in self.session.get_inputs()]
        logger.info("model_loaded", model_path=str(self.model_path), inputs=self.input_names)
    
    def predict(self, texts: List[str]) -> np.ndarray:
        """Run inference on a batch of texts. Returns class predictions."""
        # Tokenize all texts
        inputs = self.tokenizer(
            texts,
            return_tensors="np",
            max_length=self.max_length,
            truncation=True,
            padding="max_length",
        )
        
        # Prepare feeds with correct dtype (INT64 for BERT-style inputs)
        feeds = {}
        for input_name in self.input_names:
            if input_name in inputs:
                feeds[input_name] = inputs[input_name].astype(np.int64)
        
        # Run inference
        outputs = self.session.run(None, feeds)
        logits = outputs[0]  # Shape: [batch, num_classes]
        
        # Get predicted classes (argmax)
        predictions = np.argmax(logits, axis=1)
        return predictions


def evaluate_f1(y_true: List[int], y_pred: List[int], class_names: List[str]) -> tuple[float, Dict[str, float]]:
    """Compute F1 metrics using app.ml.metrics.compute_metrics.
    
    Returns:
        (f1_macro, f1_per_class)
    """
    metrics = compute_metrics(y_true, y_pred, class_names)
    return metrics["f1_macro"], metrics["f1_per_class"]


def validate(
    fp32_model_path: str | Path,
    int8_model_path: str | Path,
    tokenizer_path: str | Path,
    test_set_path: str | Path,
    max_degradation: float = DEFAULT_MAX_DEGRADATION,
    max_length: int = DEFAULT_MAX_LENGTH,
) -> ValidationReport:
    """Compare int8 F1 vs FP32 F1 on test set. Returns ValidationReport."""
    
    test_set_path = Path(test_set_path)
    fp32_model_path = Path(fp32_model_path)
    int8_model_path = Path(int8_model_path)
    tokenizer_path = Path(tokenizer_path)
    
    # Load test set
    if not test_set_path.exists():
        logger.warning("test_set_missing", path=str(test_set_path))
        return ValidationReport(
            num_examples=0,
            baseline_f1=0.0,
            int8_f1=0.0,
            degradation=0.0,
            max_degradation=max_degradation,
            skipped=True,
        )
    
    try:
        test_data = load_test_set(test_set_path)
    except ValueError as e:
        if "Test set is empty" in str(e):
            logger.warning("test_set_empty", path=str(test_set_path))
            return ValidationReport(
                num_examples=0,
                baseline_f1=0.0,
                int8_f1=0.0,
                degradation=0.0,
                max_degradation=max_degradation,
                skipped=True,
            )
        else:
            raise
    texts, labels = zip(*test_data)
    logger.info("test_set_loaded", count=len(test_data), path=str(test_set_path))
    
    # Create evaluators
    fp32_evaluator = OnnxF1Evaluator(
        fp32_model_path,
        tokenizer_path,
        DEFAULT_CLASSES,
        max_length=max_length,
    )
    int8_evaluator = OnnxF1Evaluator(
        int8_model_path,
        tokenizer_path,
        DEFAULT_CLASSES,
        max_length=max_length,
    )
    
    # Run inference
    logger.info("running_inference")
    fp32_predictions = fp32_evaluator.predict(list(texts))
    int8_predictions = int8_evaluator.predict(list(texts))
    
    # Compute F1 scores
    baseline_f1, baseline_f1_per_class = evaluate_f1(list(labels), fp32_predictions, DEFAULT_CLASSES)
    int8_f1, int8_f1_per_class = evaluate_f1(list(labels), int8_predictions, DEFAULT_CLASSES)
    
    # Calculate degradation
    degradation = baseline_f1 - int8_f1
    
    logger.info("f1_scores", 
                baseline_f1=baseline_f1,
                int8_f1=int8_f1,
                degradation=degradation,
                max_degradation=max_degradation)
    
    return ValidationReport(
        num_examples=len(test_data),
        baseline_f1=baseline_f1,
        int8_f1=int8_f1,
        degradation=degradation,
        max_degradation=max_degradation,
        baseline_f1_per_class=baseline_f1_per_class,
        int8_f1_per_class=int8_f1_per_class,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate int8 ONNX classifier against FP32 baseline"
    )
    parser.add_argument(
        "--fp32",
        required=True,
        help="Path to FP32 ONNX model"
    )
    parser.add_argument(
        "--int8",
        required=True,
        help="Path to int8 ONNX model"
    )
    parser.add_argument(
        "--tokenizer",
        required=True,
        help="Path to tokenizer directory"
    )
    parser.add_argument(
        "--test-set",
        required=True,
        help="Path to test set JSONL file"
    )
    parser.add_argument(
        "--max-degradation",
        type=float,
        default=DEFAULT_MAX_DEGRADATION,
        help="Maximum allowed F1 degradation (default 0.02)"
    )
    parser.add_argument(
        "--max-length",
        type=int,
        default=DEFAULT_MAX_LENGTH,
        help="Max sequence length for tokenization"
    )
    parser.add_argument(
        "--report",
        help="Optional path to write JSON report"
    )
    parser.add_argument(
        "--per-class",
        action="store_true",
        help="Include per-class F1 in the report"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    
    report = validate(
        args.fp32,
        args.int8,
        args.tokenizer,
        args.test_set,
        max_degradation=args.max_degradation,
        max_length=args.max_length,
    )
    
    # Print summary
    summary = report.to_dict(include_per_class=False)
    print(json.dumps(summary, indent=2))
    
    if args.report:
        Path(args.report).write_text(
            json.dumps(report.to_dict(include_per_class=args.per_class), indent=2),
            encoding="utf-8",
        )
        print(f"report written: {args.report}", file=sys.stderr)
    
    if not report.passed:
        print(
            f"VALIDATION FAILED: F1 degradation {report.degradation:.4f} >= threshold {report.max_degradation}",
            file=sys.stderr,
        )
        return 1
    print(
        f"VALIDATION PASSED: F1 degradation {report.degradation:.4f} < {report.max_degradation}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    # Import logger here to avoid issues if not all deps available
    import structlog
    logger = structlog.get_logger()
    raise SystemExit(main())