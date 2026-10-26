#!/usr/bin/env python3
"""
Evaluation report helpers for the prompt-injection classifier (Prompt 20).

Computes F1 macro / per-class F1, precision, recall, ROC AUC (one-vs-all),
confusion matrix and inference latency (p50/p99); renders the JSON report and
the confusion matrix PNG; applies the F1 regression gate against a baseline.

Used by `python -m app.ml.evaluate`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np
import structlog
from sklearn.metrics import confusion_matrix as sklearn_confusion_matrix
from sklearn.metrics import roc_auc_score

from app.ml.metrics import compute_metrics, plot_confusion_matrix_with_metrics

logger = structlog.get_logger()

DEFAULT_CLASSES = ["benign", "prompt_injection", "jailbreak", "suspicious"]
DEFAULT_MAX_REGRESSION = 0.05
DEFAULT_LATENCY_SAMPLES = 200


@dataclass
class GateResult:
    """Outcome of the F1 regression gate (baseline comparison)."""

    checked: bool
    passed: bool
    current_f1: float
    baseline_f1: Optional[float] = None
    regression: Optional[float] = None
    max_regression: float = DEFAULT_MAX_REGRESSION
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checked": self.checked,
            "passed": self.passed,
            "current_f1": self.current_f1,
            "baseline_f1": self.baseline_f1,
            "regression": self.regression,
            "max_regression": self.max_regression,
            "message": self.message,
        }


@dataclass
class EvaluationReport:
    """Full evaluation report, serialized as evaluation_report.json."""

    model_path: str
    test_set_path: str
    test_set_sha256: str
    num_examples: int
    f1_macro: float
    accuracy: float
    per_class: Dict[str, Dict[str, Optional[float]]]
    roc_auc_macro: Optional[float]
    confusion_matrix: List[List[int]]
    latency_ms: Dict[str, float]
    generated_at: str
    gate: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_path": self.model_path,
            "test_set_path": self.test_set_path,
            "test_set_sha256": self.test_set_sha256,
            "num_examples": self.num_examples,
            "f1_macro": self.f1_macro,
            "accuracy": self.accuracy,
            "per_class": self.per_class,
            "roc_auc_macro": self.roc_auc_macro,
            "confusion_matrix": self.confusion_matrix,
            "latency_ms": self.latency_ms,
            "generated_at": self.generated_at,
            "gate": self.gate,
        }


def compute_roc_auc(
    y_true: Sequence[int],
    y_proba: np.ndarray,
    class_names: Sequence[str],
) -> tuple[Dict[str, Optional[float]], Optional[float]]:
    """Per-class ROC AUC (one-vs-all) + macro average.

    Classes absent from `y_true` yield None instead of raising.
    """
    y_true_arr = np.asarray(y_true)
    y_proba_arr = np.asarray(y_proba)
    per_class: Dict[str, Optional[float]] = {}
    for index, name in enumerate(class_names):
        y_binary = (y_true_arr == index).astype(int)
        if y_binary.min() == y_binary.max():
            per_class[name] = None
            continue
        try:
            per_class[name] = float(roc_auc_score(y_binary, y_proba_arr[:, index]))
        except ValueError:
            per_class[name] = None
    available = [value for value in per_class.values() if value is not None]
    macro = float(np.mean(available)) if available else None
    return per_class, macro


def compute_full_metrics(
    y_true: Sequence[int],
    y_pred: Sequence[int],
    y_proba: np.ndarray,
    class_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """All metrics required by Prompt 20 in one dict."""
    names = list(class_names or DEFAULT_CLASSES)
    base = compute_metrics(list(y_true), list(y_pred), names)
    roc_per_class, roc_macro = compute_roc_auc(y_true, y_proba, names)
    matrix = sklearn_confusion_matrix(
        y_true, y_pred, labels=list(range(len(names)))
    ).tolist()
    per_class = {
        name: {
            "f1": float(base["f1_per_class"][name]),
            "precision": float(base["precision_per_class"][name]),
            "recall": float(base["recall_per_class"][name]),
            "roc_auc": roc_per_class[name],
        }
        for name in names
    }
    return {
        "f1_macro": float(base["f1_macro"]),
        "accuracy": float(base["accuracy"]),
        "per_class": per_class,
        "roc_auc_macro": roc_macro,
        "confusion_matrix": matrix,
        "classification_report": base["classification_report"],
    }


def measure_latency(
    predict_fn: Callable[[str], Any],
    texts: Sequence[str],
    max_samples: int = DEFAULT_LATENCY_SAMPLES,
) -> Dict[str, float]:
    """Latency of single-request (batch=1) inference: p50/p99/mean in ms."""
    sample = list(texts)[: max(0, max_samples)]
    if not sample:
        return {"p50": 0.0, "p99": 0.0, "mean": 0.0, "min": 0.0, "max": 0.0, "n": 0}
    durations: List[float] = []
    for text in sample:
        start = time.perf_counter()
        predict_fn(text)
        durations.append((time.perf_counter() - start) * 1000.0)
    values = np.asarray(durations, dtype=float)
    return {
        "p50": float(np.percentile(values, 50)),
        "p99": float(np.percentile(values, 99)),
        "mean": float(values.mean()),
        "min": float(values.min()),
        "max": float(values.max()),
        "n": int(len(values)),
    }


def compare_to_baseline(
    current_f1: float,
    baseline_path: Optional[Path | str] = None,
    max_regression: float = DEFAULT_MAX_REGRESSION,
) -> GateResult:
    """F1 regression gate: fail when baseline_f1 - current_f1 > max_regression.

    Missing baseline file (or no path given) -> gate skipped (pass).
    Corrupt baseline -> gate failed (loud: the repo baseline must be valid).
    """
    if baseline_path is None:
        return GateResult(
            checked=False,
            passed=True,
            current_f1=current_f1,
            max_regression=max_regression,
            message="no baseline provided — gate skipped",
        )

    path = Path(baseline_path)
    if not path.exists():
        return GateResult(
            checked=False,
            passed=True,
            current_f1=current_f1,
            max_regression=max_regression,
            message=f"baseline not found ({path}) — gate skipped",
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        baseline_f1 = float(data["f1_macro"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        return GateResult(
            checked=True,
            passed=False,
            current_f1=current_f1,
            max_regression=max_regression,
            message=f"baseline unreadable ({e}) — gate failed",
        )

    regression = baseline_f1 - current_f1
    passed = regression <= max_regression
    message = (
        f"f1 regression {regression:.4f} "
        f"{'<=' if passed else '>'} max {max_regression:.4f} "
        f"(baseline {baseline_f1:.4f} -> current {current_f1:.4f})"
    )
    return GateResult(
        checked=True,
        passed=passed,
        current_f1=current_f1,
        baseline_f1=baseline_f1,
        regression=regression,
        max_regression=max_regression,
        message=message,
    )


def build_report(
    *,
    model_path: str,
    test_set_path: str,
    test_set_sha256: str,
    metrics: Dict[str, Any],
    latency_ms: Dict[str, float],
    gate: Optional[GateResult] = None,
) -> EvaluationReport:
    """Assemble the EvaluationReport from computed metrics."""
    return EvaluationReport(
        model_path=model_path,
        test_set_path=test_set_path,
        test_set_sha256=test_set_sha256,
        num_examples=int(metrics.get("num_examples", 0)),
        f1_macro=float(metrics["f1_macro"]),
        accuracy=float(metrics["accuracy"]),
        per_class=metrics["per_class"],
        roc_auc_macro=metrics.get("roc_auc_macro"),
        confusion_matrix=metrics["confusion_matrix"],
        latency_ms=latency_ms,
        generated_at=datetime.now(timezone.utc).isoformat(),
        gate=gate.to_dict() if gate is not None else {},
    )


def write_report(report: EvaluationReport, output_path: Path | str) -> Path:
    """Write evaluation_report.json."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        f.write("\n")
    logger.info("evaluation_report_written", path=str(path), f1_macro=report.f1_macro)
    return path


def render_confusion_matrix(
    y_true: Sequence[int],
    y_pred: Sequence[int],
    output_path: Path | str,
    class_names: Optional[Sequence[str]] = None,
) -> Path:
    """Write confusion_matrix.png next to the model."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    plot_confusion_matrix_with_metrics(
        list(y_true),
        list(y_pred),
        list(class_names or DEFAULT_CLASSES),
        path,
    )
    logger.info("confusion_matrix_written", path=str(path))
    return path


def print_summary(report: EvaluationReport) -> None:
    """Human-readable console summary."""
    gate = report.gate or {}
    lines = [
        "",
        "=== ML Classifier Evaluation ===",
        f"model:      {report.model_path}",
        f"test set:   {report.test_set_path} ({report.num_examples} examples)",
        f"sha256:     {report.test_set_sha256}",
        f"f1_macro:   {report.f1_macro:.4f}",
        f"accuracy:   {report.accuracy:.4f}",
        f"roc_auc:    {report.roc_auc_macro if report.roc_auc_macro is not None else 'n/a'}",
        "",
        f"{'class':<20}{'f1':>8}{'prec':>8}{'rec':>8}{'auc':>8}",
    ]
    for name, values in report.per_class.items():
        auc = values.get("roc_auc")
        lines.append(
            f"{name:<20}"
            f"{values['f1']:>8.4f}"
            f"{values['precision']:>8.4f}"
            f"{values['recall']:>8.4f}"
            f"{('n/a' if auc is None else f'{auc:.4f}'):>8}"
        )
    latency = report.latency_ms
    lines += [
        "",
        f"latency (ms): p50={latency.get('p50', 0.0):.2f} "
        f"p99={latency.get('p99', 0.0):.2f} "
        f"mean={latency.get('mean', 0.0):.2f} (n={latency.get('n', 0)})",
        "",
    ]
    if gate:
        status = "PASSED" if gate.get("passed") else "FAILED"
        if not gate.get("checked"):
            status = "SKIPPED"
        lines.append(f"gate: {status} — {gate.get('message', '')}")
    print("\n".join(lines))


__all__ = [
    "DEFAULT_CLASSES",
    "DEFAULT_MAX_REGRESSION",
    "DEFAULT_LATENCY_SAMPLES",
    "GateResult",
    "EvaluationReport",
    "compute_roc_auc",
    "compute_full_metrics",
    "measure_latency",
    "compare_to_baseline",
    "build_report",
    "write_report",
    "render_confusion_matrix",
    "print_summary",
]
