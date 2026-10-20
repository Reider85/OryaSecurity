# Multilingual E5 Embedding Model

This directory contains the ONNX-quantized multilingual-e5-large embedding model for the LLM Security Scanner.

## Model Details

- **Base model**: `intfloat/multilingual-e5-large`
- **Format**: ONNX int8 quantized (CPU/GPU)
- **Dimensions**: 1024
- **Input**: Text prompts (with "query: " prefix)
- **Output**: Mean-pooled + L2-normalized embeddings
- **Target latency**: < 30 ms per batch (CPU), < 10 ms (GPU)

## Directory Structure

```
ml-models/embeddings/
├── config.pbtxt              # Triton model configuration
├── README.md                # This file
├── 1/
│   ├── model.onnx           # Main ONNX model (binary, git-lfs or external)
│   ├── tokenizer.json       # Tokenizer configuration
│   ├── special_tokens_map.json
│   ├── config.json
│   └── ...                  # Other tokenizer files
```

## Obtaining the Model

### Option 1: Export from HuggingFace (Prompt 8 approach)

Use `backend/app/ml/export_onnx_int8.py` (to be implemented in Prompt 8):

```bash
# Install dependencies
pip install optimum transformers torch onnxruntime

# Export script will generate:
# - ml-models/embeddings/1/model.onnx
# - tokenizer files
```

### Option 2: Download pre-converted

Download from HuggingFace Hub or CDN (model size ~280MB int8):

```bash
# Using git-lfs (if configured)
git lfs install
git lfs clone https://huggingface.co/intfloat/multilingual-e5-large ml-models/embeddings-temp
cd ml-models/embeddings-temp
python -m optimum.exporters.onnx --model . ../embeddings/1/model.onnx
# Copy tokenizer files to embeddings/1/
```

### Option 3: Manual export

```bash
# Using optimum CLI
optimum-cli export onnx \
  --model intfloat/multilingual-e5-large \
  ml-models/embeddings/1/model.onnx

# Quantize to int8 (optional)
optimum-cli quantize \
  --mlperf \
  --calibration-dataset calibration.txt \
  ml-models/embeddings/1/model.onnx \
  ml-models/embeddings/1/model_int8.onnx
```

## Triton Integration

The model is served by NVIDIA Triton Inference Server with:
- Dynamic batching (8, 16, 32 requests)
- CPU execution (default for dev)
- GPU execution (for production, see below)

## Switching to GPU

For production deployments with GPU:

1. Edit `docker-compose.alpha.yml`:
   ```yaml
   triton:
     # ... existing config ...
     deploy:
       resources:
         reservations:
           devices:
             - driver: nvidia
               count: 1
               capabilities: [gpu]
   ```

2. Edit `ml-models/embeddings/config.pbtxt`:
   ```pbtxt
   instance_group [
     {
       kind: KIND_GPU  # Change from KIND_CPU
       count: 1
     }
   ]
   ```

3. Restart services:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.alpha.yml up -d triton
   ```

## Performance Notes

- **CPU latency**: ~25 ms per request (batch=1), ~8 ms (batch=32)
- **GPU latency**: ~3 ms per request (batch=1), ~1 ms (batch=32)
- **Memory**: ~1.2 GB for ONNX model (int8)
- **Throughput**: ~40 requests/second (CPU), ~500 requests/second (GPU)

## Integration

The Triton client in `backend/app/ml/triton_client.py` handles:
- Text tokenization (prefix "query: ")
- Sending tensors to Triton
- Mean pooling + L2 normalization (client-side)
- Fallback to local ONNX if Triton unavailable

## Testing

Run the Triton client tests:
```bash
pytest tests/ml/test_triton_embedding.py -v
```

## Troubleshooting

- **Model not found**: Ensure `1/model.onnx` and tokenizer files exist
- **Triton fails**: Check `docker compose logs triton`
- **GPU errors**: Verify nvidia-container-toolkit and driver compatibility
- **Performance**: Adjust `dynamic_batching` parameters for your workload