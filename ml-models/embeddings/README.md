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
├── config.pbtxt              # Triton model configuration (default)
├── README.md                 # This file
├── CPU/
│   └── config.pbtxt          # Alternative config for CPU-only deployments
└── 1/                        # Triton versioned layout
    ├── model.onnx            # FP32 optimized ONNX (baseline; git-lfs/external)
    ├── model_int8.onnx       # int8 dynamic quantized (CPU fallback)
    ├── tokenizer.json        # Tokenizer configuration
    ├── special_tokens_map.json
    ├── tokenizer_config.json
    └── ...                   # Other tokenizer files
```

## Obtaining the Model

### Option 1: Export from HuggingFace (implemented — Prompt 8)

Use `backend/app/ml/export_onnx_int8.py`:

```bash
# Install export dependencies (torch + optimum + onnx)
pip install -e "backend[export]"

# Export FP32 + int8, normalize the Triton I/O contract, validate
# (mean cosine similarity vs FP32 on 100 test prompts > 0.95)
cd backend
python -m app.ml.export_onnx_int8
```

See [docs/ml-model-export.md](../../docs/ml-model-export.md) for details.

### Option 2: Download pre-converted

Download pre-converted artifacts from the internal artifact store / CDN
(int8 ~280 MB) into `1/`, including tokenizer files.

## Triton Integration

The model is served by NVIDIA Triton Inference Server with:
- Dynamic batching (8, 16, 32 requests)
- CPU execution (default for dev)
- GPU execution (for production, see below)

For CPU-only deployments serve the int8 model (Triton does not distinguish
int8 vs FP32 — it is the same ONNX file, just smaller/faster on CPU):

```bash
cp ml-models/embeddings/1/model_int8.onnx ml-models/embeddings/1/model.onnx
cp ml-models/embeddings/CPU/config.pbtxt ml-models/embeddings/config.pbtxt
```

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