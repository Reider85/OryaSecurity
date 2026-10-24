#!/usr/bin/env python3
"""
Metrics utilities for DeBERTa-v3-small classifier evaluation.

F1 score, per-class metrics, confusion matrix plotting.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
from sklearn.metrics import classification_report, confusion_matrix


def compute_metrics(y_true: List[int], y_pred: List[int], class_names: List[str] = None) -> Dict[str, Any]:
    """Compute classification metrics: F1 macro, per-class F1/precision/recall, accuracy.
    
    Args:
        y_true: Ground truth labels
        y_pred: Predicted labels
        class_names: Optional class names (default: ["benign", "prompt_injection", "jailbreak", "suspicious"])
    
    Returns:
        Dict with metrics:
        {
            "f1_macro": float,
            "f1_per_class": Dict[str, float],
            "precision_per_class": Dict[str, float],
            "recall_per_class": Dict[str, float],
            "accuracy": float,
            "classification_report": Dict[str, Any]  # Full sklearn report
        }
    """
    if class_names is None:
        class_names = ["benign", "prompt_injection", "jailbreak", "suspicious"]
    
    # Ensure labels are numpy arrays
    y_true = np.array(y_true)
    y_pred = np.array(y_pred)
    
    # Overall accuracy
    accuracy = np.mean(y_true == y_pred)
    
    # Per-class metrics
    report = classification_report(
        y_true, y_pred, target_names=class_names, output_dict=True, zero_division=0
    )
    
    # Extract per-class metrics
    f1_per_class = {cls: report[cls]["f1-score"] for cls in class_names}
    precision_per_class = {cls: report[cls]["precision"] for cls in class_names}
    recall_per_class = {cls: report[cls]["recall"] for cls in class_names}
    
    # F1 macro average
    f1_macro = report["macro avg"]["f1-score"]
    
    return {
        "f1_macro": f1_macro,
        "f1_per_class": f1_per_class,
        "precision_per_class": precision_per_class,
        "recall_per_class": recall_per_class,
        "accuracy": accuracy,
        "classification_report": report,
    }


def save_metrics_report(metrics: Dict[str, Any], output_path: Path) -> None:
    """Save metrics report to JSON file.
    
    Args:
        metrics: Metrics dict from compute_metrics()
        output_path: Path to save JSON report
    """
    # Convert numpy floats to Python floats for JSON serialization
    def convert_numpy(obj):
        if isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, dict):
            return {k: convert_numpy(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_numpy(v) for v in obj]
        return obj
    
    serializable_metrics = convert_numpy(metrics)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(serializable_metrics, f, indent=2, ensure_ascii=False)
    
    logger.info("metrics_saved", path=str(output_path))


def save_confusion_matrix(
    y_true: List[int],
    y_pred: List[int],
    class_names: List[str] = None,
    output_path: Path = None,
    figsize: Tuple[int, int] = (10, 8),
    normalize: str = None,
) -> None:
    """Save confusion matrix as PNG image.
    
    Args:
        y_true: Ground truth labels
        y_pred: Predicted labels
        class_names: Optional class names
        output_path: Path to save PNG (if None, no file saved)
        figsize: Figure size
        normalize: None, 'true', 'pred', or 'all'
    """
    if class_names is None:
        class_names = ["benign", "prompt_injection", "jailbreak", "suspicious"]
    
    cm = confusion_matrix(y_true, y_pred, normalize=normalize)
    
    plt.figure(figsize=figsize)
    sns.heatmap(
        cm,
        annot=True,
        fmt='.2f' if normalize else 'd',
        cmap='Blues',
        xticklabels=class_names,
        yticklabels=class_names,
        cbar_kws={'label': 'Count' if normalize is None else 'Proportion'}
    )
    
    plt.title(f'Confusion Matrix{" (Normalized)" if normalize else ""}')
    plt.xlabel('Predicted Label')
    plt.ylabel('True Label')
    plt.tight_layout()
    
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        logger.info("confusion_matrix_saved", path=str(output_path))
    else:
        plt.show()


def plot_confusion_matrix_with_metrics(
    y_true: List[int],
    y_pred: List[int],
    class_names: List[str] = None,
    output_path: Path = None,
    figsize: Tuple[int, int] = (12, 10),
    normalize: str = None,
) -> None:
    """Save confusion matrix with metrics as PNG.
    
    Args:
        y_true: Ground truth labels
        y_pred: Predicted labels
        class_names: Optional class names
        output_path: Path to save PNG
        figsize: Figure size
        normalize: None, 'true', 'pred', or 'all'
    """
    if class_names is None:
        class_names = ["benign", "prompt_injection", "jailbreak", "suspicious"]
    
    # Compute metrics
    metrics = compute_metrics(y_true, y_pred, class_names)
    
    # Create figure with subplots
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=figsize)
    
    # Confusion matrix
    cm = confusion_matrix(y_true, y_pred, normalize=normalize)
    sns.heatmap(
        cm,
        annot=True,
        fmt='.2f' if normalize else 'd',
        cmap='Blues',
        xticklabels=class_names,
        yticklabels=class_names,
        cbar_kws={'label': 'Count' if normalize is None else 'Proportion'},
        ax=ax1
    )
    ax1.set_title(f'Confusion Matrix{" (Normalized)" if normalize else ""}')
    ax1.set_xlabel('Predicted Label')
    ax1.set_ylabel('True Label')
    
    # Metrics table
    ax2.axis('off')
    metrics_text = f"""Classification Metrics

F1 Macro: {metrics['f1_macro']:.4f}
Accuracy: {metrics['accuracy']:.4f}

Per-Class F1:
"""
    for cls in class_names:
        metrics_text += f"{cls}: {metrics['f1_per_class'][cls]:.4f}\n"
    
    ax2.text(0.1, 0.9, metrics_text, transform=ax2.transAxes, fontsize=12,
             verticalalignment='top', fontfamily='monospace',
             bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    
    plt.tight_layout()
    
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        logger.info("confusion_matrix_with_metrics_saved", path=str(output_path))
    else:
        plt.show()


# Import logger
import structlog
logger = structlog.get_logger()