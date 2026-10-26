# ML Model Export — multilingual-e5 → ONNX int8 (CPU fallback)

Инструкция по конвертации embedding-модели `intfloat/multilingual-e5-large`
(1024-dim) в ONNX FP32 + int8 для CPU-only deployment (Prompt 8 / EP-08).

## Артефакты

```
ml-models/embeddings/
├── config.pbtxt              # Triton config (default; KIND_CPU)
├── CPU/
│   └── config.pbtxt          # отдельный config для CPU-only deployment
├── README.md
└── 1/                        # Triton versioned layout
    ├── model.onnx            # FP32 (optimized), baseline для валидации
    ├── model_int8.onnx       # int8 dynamic quantized (CPU fallback)
    ├── tokenizer.json        # tokenizer files (сохраняются экспортом)
    ├── special_tokens_map.json
    ├── tokenizer_config.json
    └── ...
```

ONNX-граф соответствует контракту Triton `config.pbtxt` и локальному
`EmbeddingClient`:

- inputs: `input_ids`, `attention_mask` (INT64, `[batch, seq]`)
- output: `last_hidden_state` (FP32, `[batch, seq, 1024]`)
- mean pooling + L2 normalization выполняются на клиенте (Triton client /
  `EmbeddingClient`) — не в графе

## Установка зависимостей

Экспорт требует тяжёлые зависимости (torch, optimum), поэтому они вынесены
в optional extra `[export]` и не попадают в runtime-образ:

```bash
pip install -e "backend[export]"
```

Валидация (`app.ml.validate_onnx`) использует только runtime-зависимости
(`onnxruntime`, `transformers`) и отдельно ставить ничего не требует.

## Запуск конвертации

```bash
cd backend
python -m app.ml.export_onnx_int8
```

Шаги, которые выполняет скрипт:

1. Загрузка HF-модели и экспорт в ONNX FP32 (optimum, opset 17).
2. Оптимизация FP32-графа для CPU (`ORTOptimizer`, level 99; best-effort —
   при ошибке остаётся неоптимизированный граф).
3. Нормализация I/O-контракта (single output `last_hidden_state`).
4. Dynamic int8-квантизация (`ORTQuantizer`, avx2 kernels).
5. Валидация: mean cosine similarity int8 vs FP32 на 100 тестовых промптах
   > 0.95 (при провале — exit code 1, артефакты остаются для инспекции).

Опции:

```bash
python -m app.ml.export_onnx_int8 \
  --model-id intfloat/multilingual-e5-large \
  --output-dir ../ml-models/embeddings \
  --opset 17 \
  --threshold 0.95 \
  [--skip-validate]
```

Ожидаемое время: 10–40 минут (включая скачивание ~2.6 GB с HuggingFace).
Займёт ~4–6 GB места (HF cache + артефакты).

## Отдельная валидация

```bash
cd backend
python -m app.ml.validate_onnx \
  --fp32 ../ml-models/embeddings/1/model.onnx \
  --int8 ../ml-models/embeddings/1/model_int8.onnx \
  --tokenizer ../ml-models/embeddings/1 \
  --report validation_report.json \
  --per-prompt
```

Exit code 0 — прошло, 1 — провал. Порог: `--threshold` (default 0.95).

## Triton

Default (`docker-compose.alpha.yml` монтирует `./ml-models:/models`):

- Triton подхватит `embeddings/1/model.onnx` (FP32) с `embeddings/config.pbtxt`.

CPU-only deployment (serve int8):

```bash
cp ml-models/embeddings/1/model_int8.onnx ml-models/embeddings/1/model.onnx
cp ml-models/embeddings/CPU/config.pbtxt ml-models/embeddings/config.pbtxt
docker compose -f docker-compose.yml -f docker-compose.alpha.yml up -d triton
```

Triton не различает int8/FP32 — это тот же ONNX-файл; int8 просто меньше и
быстрее на CPU. Для GPU используйте FP32/FP16 и `KIND_GPU` (см. README
в `ml-models/embeddings/`).

## Локальный fallback (без Triton)

`config.py` (SCANNER_*) по умолчанию указывает на versioned layout:

- `embedding_model_path`: `ml-models/embeddings/1/model_int8.onnx`
- `embedding_tokenizer_path`: `ml-models/embeddings/1`

При старте без `SCANNER_TRITON_URL` приложение использует локальный
`EmbeddingClient` (ONNX Runtime, CPUExecutionProvider).

## Git

Бинарники `*.onnx` не коммитятся (см. `.gitignore`). Артефакты
распространяются через git-lfs/CDN/внутренний artifact storage.
Токенайзер-файлы в `1/` — небольшие, допустимо хранить в Git.

## Тесты

```bash
cd backend
pytest tests/ml/test_onnx_embedding.py -v
```

Unit-тесты мокают optimum/onnxruntime; интеграционные тесты реального
модели скипаются, пока артефакты не экспортированы (`@pytest.mark.integration`).
