# ADR-0009: Embedding Service — self-hosted multilingual-e5 + ONNX/Triton

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-27 |
| **Deciders** | Architecture Team, ML Engineering |
| **Related** | ADR-0002, ADR-0005, ADR-0008, ADR-0014 |

## Context

Embedding service — критически важный компонент slow path. Преобразует текст (промпт, генерацию) в вектор (1024-dim) для последующего векторного поиска. Используется:
- На каждый suspicious prompt (slow path),
- На каждую post-factum генерацию для async-проверки.

Требования:
- p99 latency: < 10 ms (batch=1), < 30 ms (batch=32).
- Мультиязычность (RU, EN, как минимум — для русскоязычных AI-продуктов).
- Локальное развёртывание (air-gapped).
- Cost-effective: GPU дорогой, нужно кешировать и батчить.
- High throughput: 500 RPS × ~25% в slow path = 125 embeddings/sec.

### Forces

- **Latency**: каждый лишний ms embedding-инференса — добавка к p99 сканера.
- **Quality**: embeddings должны хорошо разделять malicious/benign промпты.
- **Language coverage**: ru-en мультиязычность критична (Russian traffic существенный).
- **Cost**: GPU (T4, A10) — дорогой. CPU (AVX-512) — дешёвый, но медленный.
- **Offline**: модель должна работать без обращения к OpenAI/Anthropic API (privacy, on-prem).

## Decision

**Принять self-hosted multilingual-e5-large (1024-dim) с ONNX Runtime или NVIDIA Triton Inference Server.**

```
[Scanner] ─> [Embedding Cache (Redis)] ─miss─> [Triton/ONNX Runtime] ─> vector (1024-dim)
                       │                                  │
                       │                                  └── GPU (T4 / A10) или CPU (AVX-512)
                       │
                       └──hit──> cached vector (1 µs)
```

### Модель

**`intfloat/multilingual-e5-large`** (или newer `multilingual-e5-large-instruct`):
- 1024-dim embedding,
- Поддержка 100+ языков (включая русский),
- Размер: ~2.2 GB (FP32), ~560 MB (FP16), ~280 MB (int8 quantized),
- SOTA на MTEB benchmark для мультиязычных задач.

### Inference runtime

| Опция | Когда | Latency (batch=32) | Latency (batch=1) |
|---|---|---|---|
| **NVIDIA Triton** | Production (GPU) | 8 ms | 3 ms |
| **ONNX Runtime** | CPU-only deployments | 25 ms | 8 ms |
| **Hugging Face Transformers (PyTorch)** | Dev / experimentation | 50 ms | 20 ms |

**Выбор:** Triton для prod (GPU), ONNX Runtime для on-prem CPU-only.

### Caching

Embedding Cache в Redis:
- Key: `SHA256(text)`,
- Value: 1024-dim vector (4 KB binary),
- TTL: 24 часа,
- Size: до 1M unique prompts × 4 KB = 4 GB.

**Cache hit rate** (ожидаемый):
- 30–50% для типичного AI-продукта (system prompts, FAQ),
- 60–80% в customer support чат-ботах.

### Batching

- Incoming embedding requests буферизуются в queue (5 ms window).
- Батч до 32 запросов → один inference call.
- Эффективность GPU: ~95% utilization.

### Deployment

- Single GPU (T4 16GB) обслуживает ~500 embeddings/sec.
- HA: 2 GPU-ноды, round-robin LB.
- Air-gapped: модель загружается через signed bundle, без обращений к HuggingFace Hub.

## Consequences

### Positive

- ✅ Multilingual: русский + английский + 100+ языков.
- ✅ Self-hosted: нет обращений к OpenAI/Anthropic, privacy preserved.
- ✅ On-prem совместимо (без интернет-доступа).
- ✅ Embedding cache снижает GPU-нагрузку в ~2×.
- ✅ Batching утилизирует GPU на 95%.
- ✅ ONNX/Triton — зрелые runtime, поддержка GPU/CPU.

### Negative

- ❌ GPU — дорогой ($1.5–3/час для T4). Cost в production: $1K–2K/месяц per node.
- ❌ Model size ~2.2 GB — память GPU нужна. T4 (16GB) вмещает + батчинг + KV cache.
- ❌ Multilingual-e5 — не специализирован для security domain. Возможна fine-tuning на prompt-injection corpus (Phase 3).
- ❌ Cache invalidation: при обновлении модели cache инвалидируется (новые embeddings несовместимы).

### Neutral

- ➖ Embedded vs separate service: выбран отдельный сервис (Triton) для масштабируемости и переиспользования.
- ➖ 1024-dim — баланс между качеством и storage cost. Возможно 768-dim для smaller deployments.

## Alternatives Considered

### Alternative A: OpenAI text-embedding-3-large

| Аспект | Оценка |
|---|---|
| Pros | Best quality, no GPU infra, pay-per-use |
| Cons | Cloud-only, privacy risk (PII в OpenAI), не работает on-prem, latency 100–200 ms (network) |
| Why rejected | Противоречит требованию локального развёртывания и privacy. PII в промпте нельзя отправлять в OpenAI |

**Вердикт:** Отклонено для primary. Возможно как опция для SaaS-режима.

### Alternative B: Cohere multilingual embeddings

| Аспект | Оценка |
|---|---|
| Pros | Отличные multilingual embeddings, hosted |
| Cons | Cloud-only, vendor lock-in, платно per call |
| Why rejected | Те же причины, что и OpenAI |

**Вердикт:** Отклонено.

### Alternative C: sentence-transformers/LaBSE

| Аспект | Оценка |
|---|---|
| Pros | Лучший multilingual coverage (109 языков), хорошо работает на редких языках |
| Cons | 768-dim (меньше емкость), больший размер (4.7 GB), ниже throughput |
| Why rejected | Для нашего случая (ru+en) — overkill. Multilingual-e5 даёт сопоставимое качество при меньшем размере |

**Вердикт:** Возможно для клиентов с редкими языками.

### Alternative D: BGE-m3 (BAAI)

| Аспект | Оценка |
|---|---|
| Pros | SOTA multilingual (нон недавно), long-context (8192 tokens) |
| Cons | Новее, меньше production deployments, нет русской специализации |
| Why rejected | Рассмотреть в Phase 3 после независимого benchmark на нашем corpus |

**Вердикт:** Возможен как future alternative. Требуется benchmark.

### Alternative E: Custom fine-tuned model с нуля

| Аспект | Оценка |
|---|---|
| Pros | Максимальная адаптация под security domain |
| Cons | NRE cost: ~$50K–100K + 3–6 месяцев ML-работы, нужен большой labeled corpus |
| Why rejected | На текущем этапе overengineering. Используем pre-trained + fine-tune поверх (Phase 3) |

**Вердикт:** Отклонено на MVP. Возможно Phase 4.

## Related Decisions

- **ADR-0002** (Fast/Slow Path) — embedding только в slow path.
- **ADR-0005** (Qdrant) — embeddings хранятся в Qdrant.
- **ADR-0008** (Circuit Breaker) — fallback на rule-only при недоступности embedding service.
- **ADR-0014** (On-prem) — GPU node обязательно on-prem.

## References

- [multilingual-e5 on HuggingFace](https://huggingface.co/intfloat/multilingual-e5-large)
- [MTEB Benchmark](https://github.com/beir-cellar/mteb) — multilingual embeddings evaluation
- [NVIDIA Triton Inference Server](https://docs.nvidia.com/deeplearning/triton-inference-server/)
- [ONNX Runtime](https://onnxruntime.ai/)
- ARCHITECT.md, раздел 7.4 — Embedding Service
