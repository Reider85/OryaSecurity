# ML Toxicity Model

This directory contains the ONNX-quantized toxicity classifier model for the LLM Security Scanner.

## Model Details

- **Base model**: `s-nlp/roberta_toxicity_classifier`
- **Format**: ONNX int8 quantized
- **Input**: Text chunks (8-32 tokens)
- **Output**: Toxicity probability score (0.0-1.0)
- **Threshold**: > 0.7 → BLOCK
- **Target latency**: < 2ms per chunk (CPU) — note: RoBERTa-base int8 typically achieves ~5-25ms on CPU for short sequences

## Exporting the Model

To obtain the ONNX model files:

### Prerequisites
```bash
pip install optimum transformers torch onnxruntime
```

### Export Script
```python
from optimum.exporters.onnx import export

# Load the base model
model_name = "s-nlp/roberta_toxicity_classifier"
model, tokenizer = export(
    model_name,
    save_dir="ml-models/toxicity",
    file_name="model_int8.onnx",
    quantization_config={"num_calibration_samples": 100},
    # For int8 quantization, use:
    # quantization_config={"num_calibration_samples": 100, "use_external_data_format": True}
)

# Save tokenizer
tokenizer.save_pretrained("ml-models/toxicity")
```

### Manual Export (Command Line)
```bash
# Using optimum CLI
optimum-cli export onnx --model s-nlp/roberta_toxicity_classifier ml-models/toxicity/model_int8.onnx

# Quantize to int8 (optional, may not be needed if base model is already quantized)
optimum-cli quantize --mlperf --calibration-dataset ml-models/toxicity/calibration_data.txt ml-models/toxicity/model_int8.onnx ml-models/toxicity/model_int8_quantized.onnx
```

### Required Files
Place these files in this directory:
- `model_int8.onnx` — Main ONNX model (quantized)
- `tokenizer.json` — Tokenizer configuration
- `special_tokens_map.json`, `config.json`, etc. — Other tokenizer files

## Directory Structure
```
ml-models/toxicity/
├── model_int8.onnx          # Main model file (large binary)
├── tokenizer.json          # Tokenizer config
├── special_tokens_map.json  # Special tokens mapping
├── config.json              # Model configuration
└── README.md                # This file
```

## Performance Notes

- **CPU latency**: RoBERTa-base int8 inference on short sequences (8-32 tokens) typically takes 5-25ms on modern CPUs
- **Memory**: ~250MB for ONNX model + tokenizer
- **Throughput**: Can process ~40-200 chunks/second on CPU depending on hardware
- **Accuracy**: Maintains >95% of original model accuracy after int8 quantization

## Integration

The model is automatically loaded by the scanner at startup. If files are missing, the system falls back to the heuristic toxicity detector.

## Testing

To test the model export:
```python
from app.ml.models.toxicity_model import ToxicityModel

model = ToxicityModel.load(
    "ml-models/toxicity/model_int8.onnx",
    "ml-models/toxicity"
)

score, confidence = model.predict("This is a toxic message")
print(f"Toxicity: {score:.3f}, Confidence: {confidence:.3f}")
```

## Troubleshooting

- **ImportError**: Ensure optimum and transformers are installed
- **FileNotFoundError**: Check all tokenizer files are present
- **Memory issues**: Use smaller batch sizes or model quantization
- **Performance**: Consider using GPU if available for faster inference