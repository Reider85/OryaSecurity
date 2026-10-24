#!/usr/bin/env python3
"""
Tests for metrics module.
"""

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest
from app.ml.metrics import (
    compute_metrics,
    save_metrics_report,
    save_confusion_matrix,
    plot_confusion_matrix_with_metrics,
)


class TestMetrics:
    """Test metrics computation and saving."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.class_names = ["benign", "prompt_injection", "jailbreak", "suspicious"]
        
        # Sample data
        self.y_true = [0, 0, 1, 1, 2, 2, 3, 3, 0, 1, 2, 3]
        self.y_pred = [0, 1, 1, 0, 2, 3, 3, 2, 0, 1, 2, 3]
        
        # Perfect prediction
        self.y_true_perfect = [0, 1, 2, 3, 0, 1, 2, 3]
        self.y_pred_perfect = [0, 1, 2, 3, 0, 1, 2, 3]
        
        # All wrong
        self.y_true_all_wrong = [0, 1, 2, 3]
        self.y_pred_all_wrong = [1, 2, 3, 0]

    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir)

    def test_compute_metrics_basic(self):
        """Test basic metrics computation."""
        metrics = compute_metrics(self.y_true, self.y_pred, self.class_names)
        
        assert "f1_macro" in metrics
        assert "f1_per_class" in metrics
        assert "precision_per_class" in metrics
        assert "recall_per_class" in metrics
        assert "accuracy" in metrics
        assert "classification_report" in metrics
        
        # Check types
        assert isinstance(metrics["f1_macro"], float)
        assert isinstance(metrics["accuracy"], float)
        assert isinstance(metrics["f1_per_class"], dict)
        assert isinstance(metrics["precision_per_class"], dict)
        assert isinstance(metrics["recall_per_class"], dict)
        
        # Check class keys
        for cls in self.class_names:
            assert cls in metrics["f1_per_class"]
            assert cls in metrics["precision_per_class"]
            assert cls in metrics["recall_per_class"]

    def test_compute_metrics_perfect(self):
        """Test metrics with perfect predictions."""
        metrics = compute_metrics(self.y_true_perfect, self.y_pred_perfect, self.class_names)
        
        assert metrics["f1_macro"] == 1.0
        assert metrics["accuracy"] == 1.0
        assert all(metrics["f1_per_class"][cls] == 1.0 for cls in self.class_names)

    def test_compute_metrics_all_wrong(self):
        """Test metrics with all wrong predictions."""
        metrics = compute_metrics(self.y_true_all_wrong, self.y_pred_all_wrong, self.class_names)
        
        assert metrics["f1_macro"] == 0.0
        assert metrics["accuracy"] == 0.0
        assert all(metrics["f1_per_class"][cls] == 0.0 for cls in self.class_names)

    def test_compute_metrics_no_class_names(self):
        """Test metrics with default class names."""
        metrics = compute_metrics(self.y_true, self.y_pred)
        
        assert metrics["f1_macro"] > 0
        assert metrics["accuracy"] > 0
        assert len(metrics["f1_per_class"]) == 4

    def test_save_metrics_report(self):
        """Test saving metrics report to JSON."""
        metrics = compute_metrics(self.y_true, self.y_pred, self.class_names)
        report_path = self.temp_dir / "metrics.json"
        
        save_metrics_report(metrics, report_path)
        
        assert report_path.exists()
        
        # Verify JSON content
        with open(report_path, 'r') as f:
            saved_metrics = json.load(f)
        
        assert saved_metrics["f1_macro"] == metrics["f1_macro"]
        assert saved_metrics["accuracy"] == metrics["accuracy"]
        assert saved_metrics["f1_per_class"] == metrics["f1_per_class"]

    def test_save_confusion_matrix(self):
        """Test saving confusion matrix."""
        # Test without saving file
        save_confusion_matrix(self.y_true, self.y_pred, self.class_names)
        
        # Test with saving file
        cm_path = self.temp_dir / "confusion_matrix.png"
        save_confusion_matrix(self.y_true, self.y_pred, self.class_names, cm_path)
        
        assert cm_path.exists()

    def test_save_confusion_matrix_normalized(self):
        """Test saving normalized confusion matrix."""
        cm_path = self.temp_dir / "confusion_matrix_norm.png"
        save_confusion_matrix(
            self.y_true, self.y_pred, self.class_names, cm_path, normalize="true"
        )
        
        assert cm_path.exists()

    def test_plot_confusion_matrix_with_metrics(self):
        """Test plotting confusion matrix with metrics."""
        # Test without saving file
        plot_confusion_matrix_with_metrics(self.y_true, self.y_pred, self.class_names)
        
        # Test with saving file
        plot_path = self.temp_dir / "confusion_with_metrics.png"
        plot_confusion_matrix_with_metrics(
            self.y_true, self.y_pred, self.class_names, plot_path
        )
        
        assert plot_path.exists()

    def test_metrics_with_numpy_arrays(self):
        """Test metrics with numpy input arrays."""
        y_true_np = np.array(self.y_true)
        y_pred_np = np.array(self.y_pred)
        
        metrics = compute_metrics(y_true_np, y_pred_np, self.class_names)
        
        assert metrics["f1_macro"] > 0
        assert metrics["accuracy"] > 0

    def test_metrics_edge_cases(self):
        """Test metrics with edge cases."""
        # Single class
        y_true_single = [0, 0, 0]
        y_pred_single = [0, 0, 0]
        
        metrics = compute_metrics(y_true_single, y_pred_single, ["class0"])
        
        assert metrics["f1_macro"] == 1.0
        assert metrics["accuracy"] == 1.0
        
        # Empty predictions (should handle gracefully)
        with pytest.raises(Exception):
            compute_metrics([], [], self.class_names)

    def test_metrics_report_structure(self):
        """Test classification report structure."""
        metrics = compute_metrics(self.y_true, self.y_pred, self.class_names)
        report = metrics["classification_report"]
        
        # Check required keys
        required_keys = self.class_names + ["macro avg", "weighted avg", "accuracy"]
        for key in required_keys:
            assert key in report
        
        # Check report structure for each class
        for cls in self.class_names:
            cls_report = report[cls]
            assert "precision" in cls_report
            assert "recall" in cls_report
            assert "f1-score" in cls_report
            assert "support" in cls_report