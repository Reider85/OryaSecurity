#!/usr/bin/env python3
"""
Train DeBERTa-v3-small classifier for prompt-injection detection.

Usage:
    python -m app.ml.train_classifier
    python -m app.ml.train_classifier --smoke
    python -m app.ml.train_classifier --epochs 3 --batch-size 32 --lr 1e-5
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import structlog
import torch
from datasets import Dataset, load_dataset
from sklearn.utils.class_weight import compute_class_weight
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    EarlyStoppingCallback,
    TrainingArguments,
    Trainer,
    TrainerCallback,
)

from app.ml.metrics import compute_metrics, plot_confusion_matrix_with_metrics

logger = structlog.get_logger()

# Configuration
DEFAULT_CLASSES = ["benign", "prompt_injection", "jailbreak", "suspicious"]
CLASS_LABELS = {cls: i for i, cls in enumerate(DEFAULT_CLASSES)}
ID2LABEL = {i: cls for i, cls in enumerate(DEFAULT_CLASSES)}
LABEL2ID = {cls: i for i, cls in enumerate(DEFAULT_CLASSES)}

# Default training parameters
DEFAULT_EPOCHS = 1
DEFAULT_BATCH_SIZE = 64
DEFAULT_LR = 2e-5
DEFAULT_MAX_LENGTH = 512
DEFAULT_GRAD_ACCUM = 2
DEFAULT_SEED = 42
DEFAULT_SMOKE_SAMPLES = 200
DEFAULT_SMOKE_EPOCHS = 1
DEFAULT_SMOKE_MAX_LENGTH = 128


class CustomTrainer(Trainer):
    """Custom Trainer with class weights for imbalanced datasets."""

    def __init__(self, class_weights=None, **kwargs):
        super().__init__(**kwargs)
        self.class_weights = class_weights

    def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
        # Forward pass
        labels = inputs.pop("labels")
        outputs = model(**inputs)
        logits = outputs.logits
        
        # Compute loss with class weights
        if self.class_weights is not None and labels is not None:
            loss_fct = torch.nn.CrossEntropyLoss(weight=self.class_weights.to(logits.device))
            loss = loss_fct(logits.view(-1, self.model.config.num_labels), labels.view(-1))
        else:
            loss = outputs.loss
        
        return (loss, outputs) if return_outputs else loss


class MetricsCallback(TrainerCallback):
    """Callback to save metrics and confusion matrix after evaluation."""

    def __init__(self, output_dir: Path, class_names: List[str]):
        self.output_dir = output_dir
        self.class_names = class_names
        self.best_metrics = None

    def on_evaluate(self, args: TrainingArguments, state, control, **kwargs):
        """Save metrics and confusion matrix after evaluation."""
        if hasattr(kwargs['metrics'], 'eval_f1_macro'):
            # Save best metrics
            self.best_metrics = {
                "eval_f1_macro": kwargs['metrics']['eval_f1_macro'],
                "eval_accuracy": kwargs['metrics']['eval_accuracy'],
                "eval_f1_per_class": kwargs['metrics']['eval_f1_per_class'],
            }
            
            # Save metrics report
            metrics_path = self.output_dir / "metrics.json"
            with open(metrics_path, 'w', encoding='utf-8') as f:
                json.dump(self.best_metrics, f, indent=2, ensure_ascii=False)
            
            logger.info("metrics_saved", path=str(metrics_path), f1_macro=self.best_metrics["eval_f1_macro"])


def load_dataset_splits(data_dir: Path, smoke: bool = False) -> Tuple[Dataset, Dataset, Dataset]:
    """Load train/val/test splits from JSONL files."""
    # Adjust dataset size for smoke mode
    if smoke:
        # Load all data, then sample
        datasets = {}
        for split in ["train", "val", "test"]:
            split_path = data_dir / f"{split}.jsonl"
            if not split_path.exists():
                raise FileNotFoundError(f"Dataset split not found: {split_path}")
            
            with open(split_path, 'r', encoding='utf-8') as f:
                data = [json.loads(line) for line in f]
            
            # Sample for smoke mode
            # Filter to only text and label fields
            filtered_data = [{'text': d['text'], 'label': d['label']} for d in data[:DEFAULT_SMOKE_SAMPLES]]
            datasets[split] = Dataset.from_list(filtered_data)
    else:
        # Load full splits
        datasets = {}
        for split in ["train", "val", "test"]:
            split_path = data_dir / f"{split}.jsonl"
            if not split_path.exists():
                raise FileNotFoundError(f"Dataset split not found: {split_path}")
            
            datasets[split] = load_dataset("json", data_files=str(split_path))["train"]
    
    return datasets["train"], datasets["val"], datasets["test"]


def tokenize_function(examples: Dict[str, Any], tokenizer, max_length: int) -> Dict[str, Any]:
    """Tokenize text examples."""
    return tokenizer(
        examples["text"],
        padding="max_length",
        truncation=True,
        max_length=max_length
    )


def compute_class_weights(dataset: Dataset) -> torch.Tensor:
    """Compute class weights for imbalanced datasets."""
    labels = np.array(dataset["label"])
    class_weights = compute_class_weight(
        class_weight="balanced",
        classes=np.unique(labels),
        y=labels
    )
    return torch.tensor(class_weights, dtype=torch.float32)


def main():
    parser = argparse.ArgumentParser(description="Train DeBERTa-v3-small classifier")
    parser.add_argument("--data-dir", type=str, default="ml-models/classifier/v1", help="Data directory")
    parser.add_argument("--output-dir", type=str, default="ml-models/classifier/v1", help="Output directory")
    parser.add_argument("--model-name", type=str, default="bert-base-uncased", help="Base model")
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS, help="Number of epochs")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Batch size")
    parser.add_argument("--lr", type=float, default=DEFAULT_LR, help="Learning rate")
    parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH, help="Max sequence length")
    parser.add_argument("--grad-accum", type=int, default=DEFAULT_GRAD_ACCUM, help="Gradient accumulation steps")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Random seed")
    parser.add_argument("--smoke", action="store_true", help="Smoke mode (small subset, 1 epoch)")
    parser.add_argument("--no-fp16", action="store_true", help="Disable fp16 mixed precision")
    parser.add_argument("--min-f1", type=float, default=0.85, help="Minimum F1 threshold")
    parser.add_argument("--no-download", action="store_true", help="Skip dataset download")
    
    args = parser.parse_args()
    
    # Set up logging
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer()
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        context_class=dict,
        cache_logger_on_first_use=True,
    )
    
    # Convert paths to Path objects
    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Adjust parameters for smoke mode
    if args.smoke:
        epochs = DEFAULT_SMOKE_EPOCHS
        batch_size = DEFAULT_BATCH_SIZE
        max_length = DEFAULT_SMOKE_MAX_LENGTH
        logger.info("smoke_mode", epochs=epochs, batch_size=batch_size, max_length=max_length)
    else:
        epochs = args.epochs
        batch_size = args.batch_size
        max_length = args.max_length
    
    # Set random seed
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    
    logger.info("training_start", model_name=args.model_name, epochs=epochs, batch_size=batch_size, lr=args.lr)
    
    # 1. Load dataset splits
    train_dataset, val_dataset, test_dataset = load_dataset_splits(data_dir, args.smoke)
    logger.info("datasets_loaded", train_size=len(train_dataset), val_size=len(val_dataset), test_size=len(test_dataset))
    
    # 2. Load tokenizer and model
    tokenizer = AutoTokenizer.from_pretrained(args.model_name)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.model_name,
        num_labels=len(DEFAULT_CLASSES),
        id2label=ID2LABEL,
        label2id=LABEL2ID,
    )
    
    # 3. Tokenize datasets
    train_dataset = train_dataset.map(
        lambda x: tokenize_function(x, tokenizer, max_length),
        batched=True,
        remove_columns=["text"]
    )
    val_dataset = val_dataset.map(
        lambda x: tokenize_function(x, tokenizer, max_length),
        batched=True,
        remove_columns=["text"]
    )
    test_dataset = test_dataset.map(
        lambda x: tokenize_function(x, tokenizer, max_length),
        batched=True,
        remove_columns=["text"]
    )
    
    logger.info("tokenization_complete", max_length=max_length)
    
    # 4. Compute class weights
    class_weights = compute_class_weights(train_dataset)
    logger.info("class_weights", weights=class_weights.tolist())
    
    # 5. Training arguments
    training_args = TrainingArguments(
        output_dir=output_dir / "training",
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        warmup_steps=500,
        weight_decay=0.01,
        logging_strategy="steps",
        logging_steps=50 if not args.smoke else 10,
        eval_strategy="epoch",  # или "eval_strategy"
        save_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="eval_f1_macro",
        greater_is_better=True,
        fp16=torch.cuda.is_available() and not args.no_fp16,
        bf16=torch.cuda.is_available() and not args.no_fp16,
        seed=args.seed,
        dataloader_num_workers=0,  # Windows compatibility
        remove_unused_columns=False,
    )
    
    # 6. Create trainer
    trainer = CustomTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        data_collator=None,
        compute_metrics=lambda p: compute_metrics(p.label_ids, p.predictions.argmax(-1), DEFAULT_CLASSES),
        class_weights=class_weights,
        callbacks=[MetricsCallback(output_dir, DEFAULT_CLASSES)],
    )
    
    # 7. Train
    logger.info("training_start")
    trainer.train()
    
    # 8. Evaluate on test set
    logger.info("evaluation_start")
    test_results = trainer.evaluate(test_dataset)
    logger.info("test_results", **test_results)
    
    # 9. Save final model and tokenizer
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)
    
    # 10. Save confusion matrix
    y_true = test_dataset["label"]
    y_pred = trainer.predict(test_dataset).predictions.argmax(-1)
    confusion_matrix_path = output_dir / "confusion_matrix.png"
    plot_confusion_matrix_with_metrics(
        y_true, y_pred, DEFAULT_CLASSES, confusion_matrix_path
    )
    
    # 11. Check F1 threshold
    f1_macro = test_results["eval_f1_macro"]
    if f1_macro < args.min_f1:
        logger.error("f1_threshold_failed", f1_macro=f1_macro, threshold=args.min_f1)
        sys.exit(1)
    else:
        logger.info("f1_threshold_passed", f1_macro=f1_macro, threshold=args.min_f1)
    
    logger.info("training_complete", f1_macro=f1_macro, output_dir=str(output_dir))


if __name__ == "__main__":
    main()