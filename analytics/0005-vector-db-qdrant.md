# ADR-0005: Qdrant как primary Vector DB

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-27 |
| **Deciders** | Architecture Team, Data Engineering |
| **Related** | ADR-0002, ADR-0009, ADR-0011 |

## Context

Сканер использует векторный поиск для двух задач:
1. **Поиск похожих атак** в базе известных prompt injections (1M+ векторов, 1024-dim).
2. **Поиск аномальных генераций** в базе normal/anomalous LLM outputs (10M+ векторов).

Требования:
- p99 latency поиска top-K=10: < 30 ms.
- Filter по tenant_id (мультиарендность).
- Filter по типу атаки (prompt_injection / jailbreak / pii / secret).
- Обновляемость (add/update без перестроения индекса).
- Локальное развёртывание (on-prem / air-gapped).
- Поддержка 1024-dim векторов (multilingual-e5-large).
- HA: репликация, automatic failover.

### Forces

- **Latency**: критично для inline-проверок (slow path).
- **Throughput**: 500 RPS × 1–2 search per request = 500–1000 searches/sec.
- **Filterability**: tenant_id filter нужен всегда (мультиарендность).
- **Operability**: русская команда ops должна уметь эксплуатировать.
- **Cost**: 1M векторов × 1024-dim × 4 bytes = 4 GB. Нужна эффективная компрессия.
- **Migration risk**: если选择的 БД не подойдёт, миграция 10M векторов = болезненно.

## Decision

**Принять Qdrant как primary Vector DB.**

```
[Scanner] ──> [Qdrant REST/gRPC] ──> HNSW index ──> top-K results
                    │
                    └── payload filter (tenant_id, attack_type, ...)
```

### Архитектура инстанса

- Collection `prompt_attacks` — 1M+ известных атак, шардованная по tenant_id.
- Collection `generations_normal` — нормальные генерации для anomaly detection.
- Collection `generations_anomalous` — известные вредоносные генерации.
- HNSW индекс (M=16, ef_construct=128, ef_search=64) для low-latency.
- Scalar quantization (int8) для экономии памяти (4× компрессия, recall > 95%).

### Deployment

- HA: 3 ноды с replication factor = 2 (each shard duplicated).
- Backup: snapshot каждые 6 часов в S3-совместимое хранилище (MinIO on-prem).
- Update: streaming upserts без даунтайма.

## Consequences

### Positive

- ✅ Rust-реализация — низкая задержка, предсказуемый p99.
- ✅ Поддержка payload filtering — tenant isolation в одной коллекции.
- ✅ REST + gRPC API — легко интегрируется.
- ✅ Built-in quantization — экономия RAM в 4×.
- ✅ Docker-friendly — деплоится on-prem без vendor lock-in.
- ✅ Веб-UI (Qdrant Web Dashboard) — отладка запросов визуально.
- ✅ Active community, частые релизы.

### Negative

- ❌ Меньше ecosystem чем у Milvus (меньше коннекторов, tutorials).
- ❌ Нет built-in embedding-сервиса (нужно отдельно, см. ADR-0009).
- ❌ На очень больших масштабах (> 1B векторов) проигрывает Milvus по throughput. Для наших масштабов (10M) — не критично.
- ❌ Backup API моложе, чем у PostgreSQL — нужны external cron-based snapshots.

### Neutral

- ➖ Qdrant — относительно молодая (2021), но production-ready (заявленные юзеры: Bosch, Disney, IBM).
- ➖ Лицензия Apache 2.0 — нет рисков коммерческого использования.

## Alternatives Considered

### Alternative A: Milvus

| Аспект | Оценка |
|---|---|
| Pros | Лучше на больших масштабах (> 1B), мощный ecosystem, поддерживает IVF-PQ для экономии памяти |
| Cons | Сложнее в эксплуатации (etcd, MinIO, Pulsar как зависимости), больше moving parts, выше SRE overhead |
| Why rejected | Для нашего масштаба (10M) overhead эксплуатации Milvus неоправдан. Если вырастем > 1B — пересмотреть |

**Вердикт:** Возможен в Enterprise Phase 4 при росте corpus до сотен миллионов.

### Alternative B: pgvector (PostgreSQL extension)

| Аспект | Оценка |
|---|---|
| Pros | Минимальный ops-overhead (один PostgreSQL), ACID, SQL, простая интеграция с остальной схемой |
| Cons | Не масштабируется > 10M векторов, HNSW в pgvector менее оптимизирован, p99 latency выше (50–100 ms вместо 30) |
| Why rejected | Probe p99 latency на 1M векторов показал 60 ms — не уложиться в SLO |

**Вердикт:** Возможен для dev/staging окружений. На prod — Qdrant.

### Alternative C: Weaviate

| Аспект | Оценка |
|---|---|
| Pros | Built-in embedding module (можно не поднимать отдельно), GraphQL API, развитый query language |
| Cons | GraphQL-first не удобен для нас (REST/gRPC более стандартен). Built-in embedding — lock-in на конкретную модель |
| Why rejected | Хотим гибкость в выборе embedding-модели (ADR-0009). GraphQL — лишний слой |

**Вердикт:** Отклонено.

### Alternative D: Pinecone (SaaS)

| Аспект | Оценка |
|---|---|
| Pros | Zero-ops, мгновенный scale |
| Cons | Cloud-only, противоречит требованию локального развёртывания. Vendor lock-in |
| Why rejected | Не работает on-prem. Air-gapped клиенты не смогут использовать |

**Вердикт:** Отклонено для on-prem. Возможно для SaaS-режима Enterprise Phase 3.

### Alternative E: ElasticSearch with dense_vector

| Аспект | Оценка |
|---|---|
| Pros | Если уже есть ES-кластер — переиспользование infra |
| Cons | HNSW в ES появился недавно, производительность ниже специализированных решений. ES — тяжёлый (JVM) |
| Why rejected | Overhead эксплуатации для одной задачи — не оправдан |

**Вердикт:** Отклонено.

## Related Decisions

- **ADR-0002** (Fast/Slow Path) — slow path использует Qdrant для vector search.
- **ADR-0009** (Embedding Service) — embeddings хранятся в Qdrant.
- **ADR-0011** (Multi-tenancy) — tenant isolation через payload filter.

## References

- [Qdrant documentation](https://qdrant.tech/documentation/)
- [ANN benchmarks (ann-benchmarks.com)](http://ann-benchmarks.com/) — сравнение производительности
- [Vector DB comparison (2024)](https://benchmark.vectorview.ai/) — независимый benchmark
- ARCHITECT.md, раздел 7.5 — компонент Vector DB
