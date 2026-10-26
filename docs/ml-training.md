# ML Training Guide

This guide explains how to train the DeBERTa-v3-small prompt-injection classifier for the LLM Security Scanner.

## Overview

The classifier is fine-tuned to detect 4 types of prompts:
- `benign` - Normal, harmless prompts
- `prompt_injection` - Attempts to inject instructions into the prompt
- `jailbreak` - Attempts to bypass safety measures
- `suspicious` - Borderline cases that may require human review

## Prerequisites

### Dependencies

Install the training dependencies:

```bash
cd backend
uv sync --extra train --extra export
```

This installs:
- `accelerate` - Distributed training utilities
- `scikit-learn` - Metrics computation and stratified splitting
- `sentencepiece` - Tokenizer for DeBERTa-v3
- `matplotlib` - Confusion matrix visualization
- `torch` - Deep learning framework (already in `[export]`)
- `transformers` - HuggingFace transformers (already in runtime)

### Hardware Requirements

- **Recommended**: GPU with 8GB+ VRAM (faster training)
- **Minimum**: CPU training possible but slow (hours to days)
- **Memory**: 16GB+ RAM recommended
- **Disk Space**: 5GB+ for model downloads and training data

## Training Pipeline

### 1. Data Preparation

The training data is automatically downloaded and prepared when you run the training script. The datasets used:

- **AdvBench**: ~10K prompt injection examples (augmented from 520 base examples)
- **JailbreakBench**: ~1K jailbreak examples
- **Dolly-15K**: ~10K benign examples (instruction dataset)
- **Curated suspicious**: ~1.5K borderline examples

Data is split into:
- 80% training
- 10% validation
- 10% testing

### 2. Training Command

#### Full Training

```bash
python -m app.ml.train_classifier
```

#### Smoke Mode (Quick Test)

For quick validation of the pipeline:

```bash
python -m app.ml.train_classifier --smoke
```

This uses a small subset (200 samples per class) and 1 epoch for fast testing.

#### Custom Parameters

```bash
python -m app.ml.train_classifier \
    --epochs 3 \
    --batch-size 32 \
    --lr 1e-5 \
    --max-length 512 \
    --seed 42
```

### 3. Training Parameters

| Parameter | Default | Description |
|---|---|---|
| `--epochs` | 5 | Number of training epochs |
| `--batch-size` | 16 | Batch size per GPU |
| `--lr` | 2e-5 | Learning rate |
| `--max-length` | 512 | Maximum sequence length |
| `--grad-accum` | 2 | Gradient accumulation steps |
| `--seed` | 42 | Random seed for reproducibility |
| `--smoke` | - | Use small subset for quick testing |
| `--no-fp16` | - | Disable mixed precision (auto on CPU) |
| `--min-f1` | 0.85 | Minimum F1 score threshold (fails if below) |

### 4. Output Artifacts

Training outputs to `ml-models/classifier/v1/`:

- `model.safetensors` - PyTorch model weights
- `tokenizer.json` - Tokenizer configuration
- `config.json` - Model configuration with id2label mapping
- `metrics.json` - Evaluation metrics
- `confusion_matrix.png` - Confusion matrix visualization

## Training Process

### 1. Data Loading
- Downloads datasets from public sources
- Applies augmentation for prompt injection and jailbreak examples
- Deduplicates based on text hash
- Performs stratified split

### 2. Model Setup
- Loads `microsoft/deberta-v3-small` base model
- Configures for 4-class classification
- Sets up class weights for imbalanced dataset

### 3. Training
- Uses AdamW optimizer with linear warmup
- Implements early stopping (patience=2 epochs)
- Uses mixed precision (fp16) if GPU available
- Tracks F1 macro as primary metric

### 4. Evaluation
- Evaluates on test set after training
- Saves metrics and confusion matrix
- Fails if F1 macro < 0.85 (configurable)

## Example Usage

### Train with Custom Data Directory

```bash
python -m app.ml.train_classifier \
    --data-dir ./custom_data \
    --output-dir ./my_classifier
```

### Train on CPU (No GPU)

```bash
python -m app.ml.train_classifier --no-fp16
```

### Check Training Progress

Training logs are shown in the console. Example output:

```
{
  "event": "training_start",
  "model_name": "microsoft/deberta-v3-small",
  "epochs": 5,
  "batch_size": 16,
  "lr": 2e-5
}
```

## Evaluation Metrics

### Key Metrics

- **F1 Macro**: Harmonic mean of precision and recall across all classes
- **Per-class F1**: F1 score for each class individually
- **Accuracy**: Overall classification accuracy
- **Confusion Matrix**: Visual breakdown of predictions vs true labels

### Expected Performance

Target: F1 Macro > 0.85 on test set

Typical results on GPU:
- F1 Macro: 0.88-0.92
- Training time: 1-2 hours
- Model size: ~500MB (FP32), ~125MB (int8 after export)

### Locked Hold-out Test Set (Prompt 20)

- `backend/app/ml/datasets/test_v1.json` — 1000 examples, 250 per class
  (benign / prompt_injection / jailbreak / suspicious), version-locked
- `backend/app/ml/datasets/test_v1.manifest.json` — sha256 of the examples
  payload + fingerprints of the training splits
- Build: `python -m app.ml.build_test_set` (downloads the source datasets,
  deduplicates against `ml-models/classifier/v1/{train,val,test}.jsonl`,
  deterministic for `--seed 42`)
- Verify: `python -m app.ml.build_test_set --check` — fails when the lock is
  broken or the hold-out overlaps the training splits

### Evaluation Command

```bash
python -m app.ml.evaluate \
  --model ml-models/classifier/v1 \
  --test-set app/ml/datasets/test_v1.json \
  --baseline ml-models/classifier/v1/evaluation_baseline.json \
  --max-regression 0.05
```

Outputs: console summary, `<model_dir>/evaluation_report.json`,
`<model_dir>/confusion_matrix.png`.

Metrics: F1 macro, per-class F1/precision/recall, ROC AUC (one-vs-all),
confusion matrix, latency p50/p99 (batch=1).

Exit codes: `0` — ok (or gate skipped), `1` — F1 regressed more than
`--max-regression` against the baseline, `2` — invalid test set, broken lock
or missing model artifacts.

### CI Gate

`.github/workflows/ml-evaluation.yml` runs on PRs touching `backend/app/ml/**`,
`backend/ml-models/**`, `ml-models/**`: verify lock → smoke-train (seed 42, CPU)
→ evaluate → compare with `ml-models/classifier/v1/evaluation_baseline.json`.
A drop of more than 5% fails the PR; the report and confusion matrix are
uploaded as build artifacts.

### Troubleshooting

#### Low F1 Score (< 0.85)

- Check data quality and class balance
- Increase epochs or learning rate
- Try different base model
- Augment training data with more examples

#### Training Too Slow

- Use GPU if available
- Reduce batch size and increase gradient accumulation
- Use smaller subset for initial testing

#### Memory Issues

- Reduce batch size
- Use gradient checkpointing
- Train on CPU with smaller batch size

## Integration with Scanner

Once trained, the model is used by:

1. **PromptClassifier** in `backend/app/core/detectors/ml_classifier.py`
2. **ONNX Export** (see `export_classifier_onnx.py`)
3. **Inference** in the slow path of the scanner pipeline

The model expects prompts and returns classification results with confidence scores.

## Retraining

For continuous improvement:

1. Collect new labeled data from feedback loop
2. Add to training dataset
3. Re-run training with updated data
4. Evaluate performance
5. Deploy new version

See `Prompt 17-20` in `analytics/ALPHA-PROMPTS.md` for ONNX export and evaluation pipeline.

## References

- [DeBERTa-v3 Paper](https://arxiv.org/abs/2111.09543)
- [AdvBench Dataset](https://github.com/llm-attacks/llm-attacks)
- [JailbreakBench](https://jailbreakbench.github.io/)
- [Dolly Dataset](https://github.com/databricks/dolly)
- [HuggingFace Transformers](https://huggingface.co/docs/transformers)