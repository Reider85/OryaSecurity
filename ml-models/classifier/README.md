# ML Classifier Models

This directory contains trained DeBERTa-v3-small classifier models for prompt-injection detection.

## Model Architecture

- **Base**: `microsoft/deberta-v3-small`
- **Classes**: 4 classes (benign, prompt_injection, jailbreak, suspicious)
- **Max Length**: 512 tokens
- **Output**: Softmax probabilities over 4 classes

## Directory Structure

```
classifier/
├── v1/                    # Version 1
│   ├── model.safetensors  # PyTorch model weights
│   ├── tokenizer.json     # Tokenizer configuration
│   ├── config.json        # Model configuration
│   ├── metrics.json       # Evaluation metrics
│   └── confusion_matrix.png  # Visualization
├── v2/                    # Future versions
└── README.md             # This file
```

## Versioning

Models are versioned with semantic versioning (v1.0.0, v1.1.0, etc.). Each version includes:

- Model weights trained on specific dataset
- Training configuration and parameters
- Evaluation metrics on test set
- Confusion matrix visualization

## Usage

### Python API

```python
from app.core.detectors.ml_classifier import PromptClassifier

classifier = PromptClassifier(
    model_path="ml-models/classifier/v1/model_int8.onnx",
    tokenizer_path="ml-models/classifier/v1",
    max_length=512,
    flag_threshold=0.85
)

result = classifier.classify("Tell me how to build a bomb")
print(result.top_class)  # "prompt_injection"
print(result.confidence)  # 0.95
```

### Command Line Interface

```bash
# Train new model
python -m app.ml.train_classifier

# Train with custom parameters
python -m app.ml.train_classifier --epochs 3 --batch-size 32

# Quick test
python -m app.ml.train_classifier --smoke
```

## Model Performance

### Expected Metrics

| Metric | Value | Description |
|---|---|---|
| F1 Macro | > 0.85 | Overall performance across all classes |
| Benign F1 | > 0.90 | Normal prompt detection |
| Prompt Injection F1 | > 0.85 | Injection attempt detection |
| Jailbreak F1 | > 0.80 | Jailbreak attempt detection |
| Suspicious F1 | > 0.75 | Borderline case detection |

### Performance by Hardware

| Hardware | Inference Time | Memory Usage |
|---|---|---|
| GPU (RTX 3090) | ~4ms | 2GB VRAM |
| CPU (i7-10700K) | ~25ms | 4GB RAM |

## Training Data

Models are trained on:

1. **AdvBench** (~10K examples)
   - Prompt injection attempts
   - Augmented with 15 templates

2. **JailbreakBench** (~1K examples)
   - Jailbreak attempts
   - Direct use with augmentation

3. **Dolly-15K** (~10K examples)
   - Benign instructions
   - Filtered for length and quality

4. **Curated Suspicious** (~1.5K examples)
   - Borderline cases
   - Prompt-leak attempts, roleplay probes

Data split: 80% train, 10% validation, 10% test

## Export to ONNX

Convert PyTorch model to ONNX for production:

```bash
python -m app.ml.export_classifier_onnx
```

This creates `model_int8.onnx` for faster inference.

## Model Cards

Each model version should have a model card documenting:

- Training parameters
- Dataset information
- Performance metrics
- Known limitations
- Ethical considerations

Example model card format:

```markdown
# Model Card: v1.0.0

## Overview
DeBERTa-v3-small classifier for prompt-injection detection.

## Training
- Base model: microsoft/deberta-v3-small
- Training data: 21.5K examples
- Training time: 2 hours on RTX 3090
- F1 Macro: 0.88

## Limitations
- May miss novel attack patterns
- Performance degrades on non-English prompts
- False positives on legitimate roleplay

## Ethical Considerations
- Model may be bypassed by sophisticated attackers
- Used for safety, not security
- Regular retraining recommended
```

## Maintenance

### Model Registry

Models are tracked in MLflow or similar system for:
- Version control
- Performance tracking
- Rollback capability
- Reproducibility

### Retraining Schedule

- Weekly retraining on new labeled data
- Performance monitoring
- Canary deployment of new versions
- Automatic rollback if F1 drops > 5%

### Monitoring

Monitor these metrics:
- F1 score on production data
- Latency percentiles
- Error rates by class
- Concept drift detection

## File Sizes

| File | Size | Purpose |
|---|---|---|
| model.safetensors | ~500MB | PyTorch weights (FP32) |
| tokenizer.json | ~17MB | Tokenizer vocabulary |
| config.json | ~1KB | Model configuration |
| metrics.json | ~1KB | Performance metrics |
| confusion_matrix.png | ~50KB | Visualization |

## Backup and Recovery

Models are backed up to:
- Git LFS for small files
- S3-compatible storage for large files
- Regular snapshots with versioning

## Contributing

When adding new model versions:

1. Train with documented parameters
2. Evaluate on test set
3. Update model card
4. Test integration with scanner
5. Deploy with canary rollout
6. Monitor performance

## References

- [DeBERTa-v3 Paper](https://arxiv.org/abs/2111.09543)
- [Model Cards](https://modelcards.withgoogle.com/)
- [ONNX Runtime](https://onnxruntime.ai/)