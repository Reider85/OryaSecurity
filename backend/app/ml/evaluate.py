#!/usr/bin/env python3
"""
Evaluation pipeline for the prompt-injection classifier (Prompt 20).

Runs the locked hold-out test set through the model, computes F1 / per-class
precision, recall, ROC AUC, confusion matrix and latency p50/p99, writes
evaluation_report.json + confusion_matrix.png next to the model and applies
the F1 regression gate against a committed baseline.

Usage:
    python -m app.ml.evaluate --model ml-models/classifier/v1/ \
        --test-set app/ml/datasets/test_v1.json

    python -m app.ml.evaluate --model ml-models/classifier/v1/ \
        --test-set app/ml/datasets/test_v1.json \
        --baseline ml-models/classifier/v1/evaluation_baseline.json \
        --max-regression 0.05

Exit codes:
    0 - evaluation completed, gate passed (or skipped)
    1 - F1 regression gate failed
    2 - invalid input (test set / lock / model missing)

All relative paths assume CWD = backend/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import matplotlib

matplotlib.use("Agg")  # headless report rendering; must precede pyplot import

import numpy as np
import structlog

from app.ml.build_test_set import canonical_hash
from app.ml.evaluate_report import (
    DEFAULT_CLASSES,
    DEFAULT_LATENCY_SAMPLES,
    DEFAULT_MAX_REGRESSION,
    GateResult,
    build_report,
    compare_to_baseline,
    compute_full_metrics,
    measure_latency,
    print_summary,
    render_confusion_matrix,
    write_report,
)

logger = structlog.get_logger()

DEFAULT_MODEL_DIR = Path("ml-models/classifier/v1")
DEFAULT_TEST_SET = Path("app/ml/datasets/test_v1.json")
DEFAULT_MAX_LENGTH = 512
DEFAULT_BATCH_SIZE = 32
ONNX_INT8_FILENAME = "model_int8.onnx"
ONNX_FP32_FILENAME = "model.onnx"


class ModelLoadError(RuntimeError):
    """Model artifacts missing or unusable."""


class OnnxRunner:
    """ONNX Runtime inference (CPU) returning softmax probabilities."""

    def __init__(self, model_path: Path, tokenizer_path: Path, max_length: int = DEFAULT_MAX_LENGTH):
        import onnxruntime as ort
        from transformers import AutoTokenizer

        self.model_path = model_path
        self.max_length = max_length
        self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_path))
        self.input_names = [i.name for i in self.session.get_inputs()]
        logger.info("onnx_model_loaded", path=str(model_path), inputs=self.input_names)

    def predict_proba(self, texts: Sequence[str]) -> np.ndarray:
        inputs = self.tokenizer(
            list(texts),
            return_tensors="np",
            max_length=self.max_length,
            truncation=True,
            padding="max_length",
        )
        feeds = {
            name: inputs[name].astype(np.int64)
            for name in self.input_names
            if name in inputs
        }
        logits = self.session.run(None, feeds)[0]
        return _softmax(logits)

    def predict_one(self, text: str) -> np.ndarray:
        return self.predict_proba([text])[0]


class HuggingFaceRunner:
    """Transformers (safetensors) inference returning softmax probabilities."""

    def __init__(self, model_dir: Path, max_length: int = DEFAULT_MAX_LENGTH):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch = torch
        self.max_length = max_length
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
        self.model = AutoModelForSequenceClassification.from_pretrained(str(model_dir))
        self.model.eval()
        self.model_path = model_dir
        logger.info("hf_model_loaded", path=str(model_dir))

    def predict_proba(self, texts: Sequence[str]) -> np.ndarray:
        inputs = self.tokenizer(
            list(texts),
            return_tensors="pt",
            max_length=self.max_length,
            truncation=True,
            padding=True,  # pad to the longest text in the batch, not to max_length
        )
        with self._torch.no_grad():
            logits = self.model(**inputs).logits.cpu().numpy()
        return _softmax(logits)

    def predict_one(self, text: str) -> np.ndarray:
        return self.predict_proba([text])[0]


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=-1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=-1, keepdims=True)


def resolve_runner(model_path: Path, max_length: int = DEFAULT_MAX_LENGTH):
    """Pick ONNX (int8 > fp32) when available, else a local HF model."""
    if model_path.is_file() and model_path.suffix == ".onnx":
        return OnnxRunner(model_path, model_path.parent, max_length)

    if not model_path.is_dir():
        raise ModelLoadError(f"Model path not found: {model_path}")

    for filename in (ONNX_INT8_FILENAME, ONNX_FP32_FILENAME):
        candidate = model_path / filename
        if candidate.exists():
            return OnnxRunner(candidate, model_path, max_length)

    has_config = (model_path / "config.json").exists()
    has_weights = (model_path / "model.safetensors").exists() or (model_path / "pytorch_model.bin").exists()
    if has_config and has_weights:
        return HuggingFaceRunner(model_path, max_length)

    raise ModelLoadError(
        f"No model artifacts in {model_path} "
        f"(expected {ONNX_INT8_FILENAME}, {ONNX_FP32_FILENAME} or HF config+weights). "
        "Train one first: python -m app.ml.train_classifier --smoke"
    )


def load_test_set(test_set_path: Path) -> List[Dict[str, Any]]:
    """Load the locked test set document ({"examples": [...]})."""
    if not test_set_path.exists():
        raise ValueError(f"Test set not found: {test_set_path}")
    try:
        with open(test_set_path, "r", encoding="utf-8-sig") as f:
            document = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {test_set_path}: {e}")

    examples = document.get("examples") if isinstance(document, dict) else None
    if not isinstance(examples, list) or not examples:
        raise ValueError(f"Test set has no examples: {test_set_path}")

    for index, example in enumerate(examples):
        text = example.get("text")
        label = example.get("label")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Empty text at example {index}")
        if not isinstance(label, int) or not 0 <= label < len(DEFAULT_CLASSES):
            raise ValueError(f"Invalid label {label!r} at example {index}")
    return examples


def verify_lock(
    test_set_path: Path,
    examples: Sequence[Dict[str, Any]],
    manifest_path: Optional[Path],
) -> str:
    """Verify the sha256 lock of the test set; returns the digest."""
    path = manifest_path or test_set_path.with_suffix(".manifest.json")
    if not path.exists():
        raise ValueError(
            f"Manifest not found: {path} — test set is not locked. "
            "Run: python -m app.ml.build_test_set"
        )
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            manifest = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid manifest JSON: {e}")

    digest = canonical_hash(list(examples))
    expected = manifest.get("sha256")
    if digest != expected:
        raise ValueError(
            f"Lock mismatch: test set sha256 {digest} != manifest {expected} "
            "(test set was modified after locking)"
        )
    return digest


def predict_all(runner: Any, texts: Sequence[str], batch_size: int = DEFAULT_BATCH_SIZE) -> np.ndarray:
    """Batched inference -> softmax probabilities [N, num_classes]."""
    batches = [
        runner.predict_proba(list(texts[start:start + batch_size]))
        for start in range(0, len(texts), batch_size)
    ]
    return np.vstack(batches)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate the classifier on the locked hold-out set")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_DIR,
                        help=f"Model dir or .onnx file (default: {DEFAULT_MODEL_DIR})")
    parser.add_argument("--test-set", type=Path, default=DEFAULT_TEST_SET,
                        help=f"Locked test set JSON (default: {DEFAULT_TEST_SET})")
    parser.add_argument("--manifest", type=Path, default=None,
                        help="Manifest path (default: <test-set>.manifest.json)")
    parser.add_argument("--baseline", type=Path, default=None,
                        help="Baseline report for the F1 regression gate")
    parser.add_argument("--max-regression", type=float, default=DEFAULT_MAX_REGRESSION,
                        help=f"Max allowed F1 drop vs baseline (default: {DEFAULT_MAX_REGRESSION})")
    parser.add_argument("--report", type=Path, default=None,
                        help="Report path (default: <model_dir>/evaluation_report.json)")
    parser.add_argument("--confusion-matrix", type=Path, default=None,
                        help="PNG path (default: <model_dir>/confusion_matrix.png)")
    parser.add_argument("--max-samples", type=int, default=DEFAULT_LATENCY_SAMPLES,
                        help=f"Samples for latency measurement (default: {DEFAULT_LATENCY_SAMPLES})")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Eval batch size (default: {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH,
                        help=f"Tokenizer max length (default: {DEFAULT_MAX_LENGTH})")
    return parser


def _model_output_dir(model_path: Path) -> Path:
    return model_path.parent if model_path.is_file() else model_path


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        examples = load_test_set(args.test_set)
        digest = verify_lock(args.test_set, examples, args.manifest)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        logger.error("test_set_invalid", error=str(e))
        return 2

    try:
        runner = resolve_runner(args.model, args.max_length)
    except ModelLoadError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        logger.error("model_unavailable", error=str(e))
        return 2

    texts = [example["text"] for example in examples]
    labels = [example["label"] for example in examples]
    logger.info("evaluation_start", model=str(args.model), num_examples=len(examples))

    y_proba = predict_all(runner, texts, args.batch_size)
    y_pred = y_proba.argmax(axis=1)
    logger.info("inference_complete", num_examples=len(examples))

    metrics = compute_full_metrics(labels, y_pred, y_proba)
    metrics["num_examples"] = len(examples)

    latency = measure_latency(runner.predict_one, texts, args.max_samples)
    logger.info("latency_measured", **latency)
    gate: GateResult = compare_to_baseline(metrics["f1_macro"], args.baseline, args.max_regression)

    report = build_report(
        model_path=str(args.model),
        test_set_path=str(args.test_set),
        test_set_sha256=digest,
        metrics=metrics,
        latency_ms=latency,
        gate=gate,
    )

    output_dir = _model_output_dir(args.model)
    report_path = args.report or output_dir / "evaluation_report.json"
    matrix_path = args.confusion_matrix or output_dir / "confusion_matrix.png"

    write_report(report, report_path)
    try:
        render_confusion_matrix(labels, y_pred, matrix_path)
    except Exception as e:  # noqa: BLE001 - report rendering must not fail the gate
        logger.warning("confusion_matrix_failed", error=str(e))

    print_summary(report)
    print(f"report:          {report_path}")
    print(f"confusion matrix: {matrix_path}")

    if not gate.passed:
        print(f"GATE FAILED: {gate.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
