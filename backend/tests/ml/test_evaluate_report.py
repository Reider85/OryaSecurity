"""
Tests for evaluation report helpers (Prompt 20).
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from app.ml.evaluate_report import (
    DEFAULT_CLASSES,
    build_report,
    compare_to_baseline,
    compute_full_metrics,
    compute_roc_auc,
    measure_latency,
    print_summary,
    render_confusion_matrix,
    write_report,
)


class TestComputeRocAuc:
    def test_perfect_separation_scores_one(self):
        y_true = [0, 1, 2, 3, 0, 1, 2, 3]
        y_proba = np.eye(4)[np.array(y_true)]
        per_class, macro = compute_roc_auc(y_true, y_proba, DEFAULT_CLASSES)

        assert per_class == {name: pytest.approx(1.0) for name in DEFAULT_CLASSES}
        assert macro == pytest.approx(1.0)

    def test_class_absent_from_labels_yields_none(self):
        y_true = [0, 0, 1, 1]
        y_proba = np.array([[0.9, 0.1], [0.8, 0.2], [0.2, 0.8], [0.1, 0.9]])
        per_class, macro = compute_roc_auc(y_true, y_proba, ["a", "b", "c", "d"])

        assert per_class["c"] is None
        assert per_class["d"] is None
        assert per_class["a"] is not None
        assert macro is not None


class TestComputeFullMetrics:
    def test_report_structure(self):
        y_true = [0, 1, 2, 3, 0, 1, 2, 3]
        y_pred = [0, 1, 2, 3, 1, 1, 2, 0]
        y_proba = np.eye(4)[np.array(y_pred)]

        metrics = compute_full_metrics(y_true, y_pred, y_proba)

        assert set(metrics) >= {
            "f1_macro",
            "accuracy",
            "per_class",
            "roc_auc_macro",
            "confusion_matrix",
        }
        assert 0.0 <= metrics["f1_macro"] <= 1.0
        assert len(metrics["confusion_matrix"]) == 4
        assert all(len(row) == 4 for row in metrics["confusion_matrix"])
        assert sum(sum(row) for row in metrics["confusion_matrix"]) == len(y_true)

        for name in DEFAULT_CLASSES:
            entry = metrics["per_class"][name]
            assert set(entry) == {"f1", "precision", "recall", "roc_auc"}

    def test_perfect_predictions_give_f1_of_one(self):
        y_true = [0, 1, 2, 3]
        y_pred = [0, 1, 2, 3]
        y_proba = np.eye(4)[np.array(y_true)]
        metrics = compute_full_metrics(y_true, y_pred, y_proba)
        assert metrics["f1_macro"] == pytest.approx(1.0)
        assert metrics["accuracy"] == pytest.approx(1.0)


class TestMeasureLatency:
    def test_returns_percentiles(self):
        calls = []

        def predict(text):
            calls.append(text)
            return np.array([1.0])

        texts = [f"sample {i}" for i in range(10)]
        result = measure_latency(predict, texts, max_samples=5)

        assert len(calls) == 5
        assert result["n"] == 5
        assert 0.0 <= result["p50"] <= result["p99"]
        assert result["mean"] >= 0.0

    def test_empty_input_returns_zeros(self):
        result = measure_latency(lambda t: None, [], max_samples=10)
        assert result["n"] == 0
        assert result["p50"] == 0.0
        assert result["p99"] == 0.0


class TestRegressionGate:
    def _baseline(self, tmp_path, f1, raw=None):
        path = tmp_path / "baseline.json"
        content = raw if raw is not None else json.dumps({"f1_macro": f1})
        path.write_text(content, encoding="utf-8")
        return path

    def test_skipped_without_baseline(self):
        gate = compare_to_baseline(0.5, baseline_path=None)
        assert gate.checked is False
        assert gate.passed is True
        assert "skipped" in gate.message

    def test_skipped_when_baseline_missing(self, tmp_path):
        gate = compare_to_baseline(0.5, tmp_path / "absent.json")
        assert gate.checked is False
        assert gate.passed is True

    def test_corrupt_baseline_fails_loudly(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("not-json", encoding="utf-8")
        gate = compare_to_baseline(0.5, path)
        assert gate.checked is True
        assert gate.passed is False

    def test_passes_within_regression_budget(self, tmp_path):
        gate = compare_to_baseline(0.86, self._baseline(tmp_path, 0.90), max_regression=0.05)
        assert gate.checked is True
        assert gate.passed is True
        assert gate.regression == pytest.approx(0.04)

    def test_fails_beyond_regression_budget(self, tmp_path):
        gate = compare_to_baseline(0.80, self._baseline(tmp_path, 0.90), max_regression=0.05)
        assert gate.checked is True
        assert gate.passed is False
        assert gate.regression == pytest.approx(0.10)

    def test_improvement_passes(self, tmp_path):
        gate = compare_to_baseline(0.95, self._baseline(tmp_path, 0.90))
        assert gate.passed is True
        assert gate.regression < 0

    def test_baseline_with_utf8_bom_is_tolerated(self, tmp_path):
        path = self._baseline(tmp_path, 0.5, raw=chr(0xFEFF) + json.dumps({"f1_macro": 0.5}))
        gate = compare_to_baseline(0.5, path)
        assert gate.checked is True
        assert gate.baseline_f1 == pytest.approx(0.5)


class TestReportOutputs:
    def _report(self):
        y_true = [0, 1, 2, 3]
        y_pred = [0, 1, 2, 3]
        y_proba = np.eye(4)[np.array(y_true)]
        metrics = compute_full_metrics(y_true, y_pred, y_proba)
        metrics["num_examples"] = 4
        gate = compare_to_baseline(metrics["f1_macro"], None)
        return build_report(
            model_path="ml-models/classifier/v1",
            test_set_path="app/ml/datasets/test_v1.json",
            test_set_sha256="abc123",
            metrics=metrics,
            latency_ms={"p50": 1.0, "p99": 2.0, "mean": 1.5, "n": 4},
            gate=gate,
        )

    def test_write_report_creates_json(self, tmp_path):
        report = self._report()
        path = write_report(report, tmp_path / "evaluation_report.json")

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["num_examples"] == 4
        assert data["f1_macro"] == pytest.approx(1.0)
        assert data["test_set_sha256"] == "abc123"
        assert data["per_class"]["benign"]["roc_auc"] == pytest.approx(1.0)
        assert data["gate"]["checked"] is False
        assert data["latency_ms"]["p99"] == 2.0

    def test_render_confusion_matrix_writes_png(self, tmp_path):
        path = render_confusion_matrix(
            [0, 1, 2, 3], [0, 1, 2, 3], tmp_path / "confusion_matrix.png"
        )
        assert path.exists()
        assert path.stat().st_size > 0

    def test_print_summary_includes_key_fields(self, capsys):
        report = self._report()
        print_summary(report)
        out = capsys.readouterr().out
        assert "f1_macro" in out
        assert "roc_auc" in out
        assert "latency" in out
        assert "gate" in out
