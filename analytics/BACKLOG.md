# BACKLOG.md — LLM Security Scanner

| | |
|---|---|
| **Версия документа** | 1.0 |
| **Дата** | 2026-09-27 |
| **Источник** | [ROADMAP.md](ROADMAP.md), [ARCHITECT.md](ARCHITECT.md), [ADR.md](ADR.md) |
| **Формат** | Epic → Story → Task; приоритет P0–P3; оценка S/M/L/XL |
| **Язык** | Русский |

---

## 0. Соглашения

### 0.1. Приоритеты

| Приоритет | Значение |
|---|---|
| **P0** | Blocker — обязательно для текущей фазы, без него phase gate не пройден |
| **P1** | High — должно быть в текущей фазе |
| **P2** | Medium — желательно в текущей фазе, может переноситься |
| **P3** | Low — backlog, кандидат на следующую фазу |

### 0.2. Оценки (story points)

| Метка | Story points | Часы (приблизительно) |
|---|---|---|
| **S** | 1 | 4–8 часов |
| **M** | 3 | 1–3 дня |
| **L** | 5 | 1 неделя |
| **XL** | 8 | 2 недели |
| **XXL** | 13 | 3+ недели |

### 0.3. Типы задач

| Тип | Обозначение |
|---|---|
| FEAT | Новая функциональность |
| INFRA | Инфраструктура / DevOps |
| ML | ML / Data |
| SEC | Security |
| DOC | Документация |
| TEST | Тестирование |
| COMPL | Compliance |
| RES | Research / PoC |

### 0.4. Цветовая привязка к фазе

- 🟦 MVP
- 🟩 ALPHA
- 🟨 BETA
- 🟪 PRODUCTION
- 🟧 EXTENDED

---

## 1. Обзор Epic'ов

| Epic # | Название | Phase | Кол-во stories | Story points |
|---|---|---|---|---|
| EP-01 | Reverse Proxy Core | 🟦 MVP | 8 | 30 |
| EP-02 | Rule Engine | 🟦 MVP | 6 | 18 |
| EP-03 | Decision Cache | 🟦 MVP | 4 | 12 |
| EP-04 | Basic Audit | 🟦 MVP | 4 | 10 |
| EP-05 | Hardcoded PDP | 🟦 MVP | 3 | 8 |
| EP-06 | MVP Deployment | 🟦 MVP | 5 | 14 |
| EP-07 | Streaming Inspector | 🟩 ALPHA | 6 | 24 |
| EP-08 | Embedding Service | 🟩 ALPHA | 5 | 20 |
| EP-09 | Vector DB (Qdrant) | 🟩 ALPHA | 5 | 18 |
| EP-10 | ML Classifier | 🟩 ALPHA | 6 | 26 |
| EP-11 | Two-tier Pipeline | 🟩 ALPHA | 4 | 14 |
| EP-12 | OPA Policy Decision Point | 🟩 ALPHA | 6 | 20 |
| EP-13 | Vault PII Redaction | 🟩 ALPHA | 5 | 22 |
| EP-14 | Circuit Breaker | 🟩 ALPHA | 4 | 12 |
| EP-15 | Observability | 🟩 ALPHA | 6 | 18 |
| EP-16 | Multi-tenancy | 🟨 BETA | 7 | 28 |
| EP-17 | Hashchain Audit | 🟨 BETA | 5 | 20 |
| EP-18 | Threat Intel Sync | 🟨 BETA | 5 | 18 |
| EP-19 | Feedback Loop | 🟨 BETA | 6 | 24 |
| EP-20 | Retrain Pipeline | 🟨 BETA | 5 | 22 |
| EP-21 | Helm Chart & K8s | 🟨 BETA | 4 | 14 |
| EP-22 | HA Deployment | 🟪 PROD | 5 | 16 |
| EP-23 | Air-gapped Deployment | 🟪 PROD | 4 | 18 |
| EP-24 | Compliance & Audit | 🟪 PROD | 5 | 16 |
| EP-25 | SOC 2 Preparation | 🟪 PROD | 4 | 20 |
| EP-26 | Bloom Filter Cache | 🟧 EXT | 4 | 14 |
| EP-27 | Adversarial Reinforcement | 🟧 EXT | 5 | 20 |
| EP-28 | Detector Fan-out | 🟧 EXT | 4 | 16 |
| EP-29 | LLM-as-judge Fallback | 🟧 EXT | 4 | 16 |
| EP-30 | Unified Model | 🟧 EXT | 5 | 20 |
| EP-31 | Predictive Streaming | 🟧 EXT | 4 | 18 |
| EP-32 | RAG Security Module | 🟧 EXT | 6 | 24 |
| EP-33 | Vector DB Security Module | 🟧 EXT | 5 | 22 |
| EP-34 | Behavioral Analysis | 🟧 EXT | 5 | 26 |
| EP-35 | Ingress Gateway & Rate Limiting | 🟩🟨🟪 | 7 | 25 |
| EP-36 | Report Engine & SecOps Dashboard | 🟩🟨🟪 | 7 | 27 |
| EP-37 | Generation Analyzer (Extended Detection) | 🟩🟧 | 5 | 24 |
| EP-38 | DR, Backup & Restore | 🟨🟪 | 6 | 28 |
| EP-39 | Cost & Capacity Management (FinOps) | 🟨🟪 | 5 | 16 |
| EP-40 | Scanner Self-Security | 🟩🟨🟧 | 10 | 36 |
| EP-41 | Multi-Language SDKs & API Versioning | 🟩🟨🟪 | 7 | 30 |
| EP-42 | Cross-Model Policies & Data Residency | 🟪🟧 | 7 | 34 |

**Итого: 42 epic, ~205 stories, ~1080 story points.**

---

## 2. 🟦 MVP — Epic и Stories

### EP-01: Reverse Proxy Core

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 1.1 | Как разработчик, я хочу HTTP endpoint `/scan` для проверки промпта, чтобы интегрировать сканер в AI-приложение | FEAT | P0 | M | 0001 |
| 1.2 | Как система, я хочу проксировать запрос от AI-приложения к LLM-провайдеру с инспекцией обоих направлений | FEAT | P0 | L | 0001 |
| 1.3 | Как SRE, я хочу healthcheck endpoint `/health` для monitoring | INFRA | P0 | S | 0001 |
| 1.4 | Как SecOps, я хочу видеть request_id в каждом ответе для отладки | FEAT | P1 | S | 0001 |
| 1.5 | Как система, я хочу поддерживать OpenAI-совместимый API (chat/completions) для drop-in замены | FEAT | P0 | L | 0001 |
| 1.6 | Как разработчик, я хочу Python SDK для интеграции сканера | FEAT | P1 | M | 0001 |
| 1.7 | Как SRE, я хочу graceful shutdown (drain connections) при deploy | INFRA | P1 | M | 0001 |
| 1.8 | Как разработчик, я хочу поддерживать basic auth / API-key для client-auth | SEC | P0 | S | 0001 |

### EP-02: Rule Engine

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 2.1 | Как SecOps, я хочу regex-правила для SSN (US), паспорта (RU), email | FEAT | P0 | M | 0002 |
| 2.2 | Как SecOps, я хочу regex-правила для AWS keys, JWT, credit cards | FEAT | P0 | M | 0002 |
| 2.3 | Как SecOps, я хочу regex-правила для API keys (generic patterns) | FEAT | P1 | S | 0002 |
| 2.4 | Как система, я хочу YAML-формат для правил (легкое редактирование) | FEAT | P0 | S | 0002 |
| 2.5 | Как разработчик, я хочу unit-тесты для каждого правила | TEST | P0 | M | 0002 |
| 2.6 | Как SecOps, я хочу metrics: count matches per rule | FEAT | P1 | S | 0002 |

### EP-03: Decision Cache

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 3.1 | Как система, я хочу Redis-backed cache с key=SHA256(prompt), TTL 5min | FEAT | P0 | M | 0002 |
| 3.2 | Как SRE, я хочу метрику cache hit rate | INFRA | P0 | S | 0002 |
| 3.3 | Как SecOps, я хочу manual cache flush endpoint (для debugging) | INFRA | P1 | S | 0002 |
| 3.4 | Как SRE, я хочу monitor Redis memory usage | INFRA | P1 | S | 0002 |

### EP-04: Basic Audit

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 4.1 | Как SecOps, я хочу PostgreSQL table для audit log: ts, tenant, prompt_hash, verdict | FEAT | P0 | M | 0006 (basic) |
| 4.2 | Как SecOps, я хочу endpoint `/audit?from=...&to=...` для выгрузки | FEAT | P1 | M | 0006 |
| 4.3 | Как SRE, я хочу retention policy: delete после 30 дней (MVP) | INFRA | P1 | S | 0006 |
| 4.4 | Как SecOps, я хочу PII redaction перед записью в audit (basic, regex only) | SEC | P0 | S | 0006 |

### EP-05: Hardcoded PDP

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 5.1 | Как система, я хочу hardcoded PDP: если сработал regex → BLOCK | FEAT | P0 | S | 0004 (basic) |
| 5.2 | Как система, я хочу возвращать `X-Scanner-Verdict` header | FEAT | P0 | S | 0004 |
| 5.3 | Как SecOps, я хочу конфиг: какие правила → BLOCK, какие → LOG_ONLY | FEAT | P1 | S | 0004 |

### EP-06: MVP Deployment

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 6.1 | Как SRE, я хочу docker-compose.yml с scanner + Redis + PostgreSQL | INFRA | P0 | M | 0014 |
| 6.2 | Как SRE, я хочу Dockerfile для scanner (multi-stage, slim) | INFRA | P0 | S | 0014 |
| 6.3 | Как разработчик, я хочу README с Quick Start (5 минут до запуска) | DOC | P0 | S | 0014 |
| 6.4 | Как SRE, я хочу basic Grafana dashboard (RPS, latency, block rate) | INFRA | P1 | M | 0015 (basic) |
| 6.5 | Как SRE, я хочу Prometheus metrics endpoint `/metrics` | INFRA | P0 | S | 0015 |

---

## 3. 🟩 ALPHA — Epic и Stories

### EP-07: Streaming Inspector

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 7.1 | Как система, я хочу поддерживать SSE streaming от LLM | FEAT | P0 | L | 0003 |
| 7.2 | Как система, я хочу инспектировать чанки по N=8 токенов | FEAT | P0 | L | 0003 |
| 7.3 | Как система, я хочу terminate stream при BLOCK с event `{type:"blocked"}` | FEAT | P0 | M | 0003 |
| 7.4 | Как система, я хочу regex-check на каждом чанке (PII, secrets) | FEAT | P0 | M | 0003 |
| 7.5 | Как система, я хочу lightweight ML toxicity classifier на чанках | ML | P1 | L | 0003 |
| 7.6 | Как система, я хочу async post-factum deep-analysis полной генерации | FEAT | P1 | M | 0003 |

### EP-08: Embedding Service

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 8.1 | Как ML engineer, я хочу Triton Inference Server с multilingual-e5-large | ML | P0 | L | 0009 |
| 8.2 | Как система, я хочу ONNX int8 модель для CPU-fallback | ML | P1 | M | 0009 |
| 8.3 | Как система, я хочу embedding cache в Redis (key=SHA256(text), TTL 24h) | FEAT | P0 | M | 0009 |
| 8.4 | Как SRE, я хочу metrics: embedding latency p50/p99, cache hit rate | INFRA | P0 | S | 0009 |
| 8.5 | Как ML engineer, я хочу batching: буфер 5ms, batch=32 | ML | P1 | M | 0009 |

### EP-09: Vector DB (Qdrant)

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 9.1 | Как SRE, я хочу Qdrant deployment (3 ноды, RF=2) | INFRA | P0 | M | 0005 |
| 9.2 | Как ML engineer, я хочу collection `prompt_attacks` с 10K векторов | ML | P0 | M | 0005 |
| 9.3 | Как система, я хочу search endpoint с HNSW, top-K=10, latency < 30ms | FEAT | P0 | L | 0005 |
| 9.4 | Как ML engineer, я хочу scalar quantization (int8) для экономии RAM | ML | P1 | M | 0005 |
| 9.5 | Как SRE, я хочу backup/restore: snapshot каждые 6 часов | INFRA | P1 | M | 0005 |

### EP-10: ML Classifier

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 10.1 | Как ML engineer, я хочу fine-tune DeBERTa-v3-small на AdvBench + JailbreakBench corpus | ML | P0 | XL | 0010 |
| 10.2 | Как ML engineer, я хочу ONNX int8 export | ML | P0 | M | 0010 |
| 10.3 | Как система, я хочу inference service: input=text → output={class, confidence} | FEAT | P0 | M | 0010 |
| 10.4 | Как ML engineer, я хочу SHAP explainability для top-5 tokens | ML | P1 | L | 0010 |
| 10.5 | Как ML engineer, я хочу hold-out test set + F1 evaluation | ML | P0 | M | 0010 |
| 10.6 | Как SecOps, я хочу метрику: detection rate by attack class | FEAT | P1 | S | 0010 |

### EP-11: Two-tier Pipeline

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 11.1 | Как система, я хочу fast path: cache lookup → regex | FEAT | P0 | M | 0002 |
| 11.2 | Как система, я хочу suspicion tagger (length, regex match) | FEAT | P0 | M | 0002 |
| 11.3 | Как система, я хочу slow path: embedding → vector search → ML | FEAT | P0 | L | 0002 |
| 11.4 | Как SRE, я хочу метрику: % трафика в fast vs slow path | INFRA | P1 | S | 0002 |

### EP-12: OPA Policy Decision Point

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 12.1 | Как SecOps, я хочу OPA deployment с bundle service | INFRA | P0 | L | 0004 |
| 12.2 | Как SecOps, я хочу Rego policies: allow/block/redact/log_only | FEAT | P0 | L | 0004 |
| 12.3 | Как SecOps, я хочу Git repo для policies + signed commits | SEC | P0 | M | 0004 |
| 12.4 | Как SecOps, я хочу policy versions + bundle deployment | FEAT | P1 | M | 0004 |
| 12.5 | Как система, я хочу audit log: какая policy_version применена | FEAT | P0 | S | 0004 |
| 12.6 | Как SecOps, я хочу unit-тесты для Rego policies | TEST | P1 | M | 0004 |

### EP-13: Vault PII Redaction

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 13.1 | Как SecOps, я хочу HashiCorp Vault deployment (HA mode) | INFRA | P0 | L | 0007 |
| 13.2 | Как система, я хочу tokenize PII: SSN/email/passport → `<TYPE_T_ID>` | FEAT | P0 | L | 0007 |
| 13.3 | Как система, я хочу reverse lookup: token → original value (для LLM response) | FEAT | P0 | M | 0007 |
| 13.4 | Как SecOps, я хочу TTL=5min для токенов, auto-cleanup | SEC | P0 | S | 0007 |
| 13.5 | Как система, я хочу integration с Presidio NER для нестандартных PII | FEAT | P1 | L | 0007 |

### EP-14: Circuit Breaker

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 14.1 | Как система, я хочу CB для vector DB (degrade → rule-only) | FEAT | P0 | M | 0008 |
| 14.2 | Как система, я хочу CB для ML classifier (degrade → rule-only) | FEAT | P0 | M | 0008 |
| 14.3 | Как система, я хочу CB для Vault (fail-closed для PII policy) | FEAT | P0 | M | 0008 |
| 14.4 | Как SRE, я хочу метрику CB state + alert на OPEN | INFRA | P0 | S | 0008 |

### EP-15: Observability

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 15.1 | Как SRE, я хочу OTel instrumentation всех компонентов | INFRA | P0 | L | 0015 |
| 15.2 | Как SRE, я хочу Jaeger deployment + trace sampling | INFRA | P0 | M | 0015 |
| 15.3 | Как SRE, я хочу Loki для structured JSON logs | INFRA | P0 | M | 0015 |
| 15.4 | Как SRE, я хочу operational Grafana dashboard (latency, RPS, errors, cache, CB) | INFRA | P0 | M | 0015 |
| 15.5 | Как SRE, я хочу Alertmanager + 5 базовых алертов | INFRA | P0 | M | 0015 |
| 15.6 | Как SRE, я хочу PagerDuty integration для critical alerts | INFRA | P1 | S | 0015 |

---

## 4. 🟨 BETA — Epic и Stories

### EP-16: Multi-tenancy

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 16.1 | Как система, я хочу tenant_id routing (hostname / API-key) | FEAT | P0 | L | 0011 |
| 16.2 | Как SecOps, я хочу per-tenant KMS keys | SEC | P0 | L | 0011 |
| 16.3 | Как система, я хочу payload filter в Qdrant по tenant_id | FEAT | P0 | M | 0011 |
| 16.4 | Как SecOps, я хочу per-tenant Vault namespaces | SEC | P1 | M | 0011 |
| 16.5 | Как SecOps, я хочу per-tenant audit buckets (S3) | SEC | P0 | M | 0011 |
| 16.6 | Как система, я хочу tiered isolation: T1 (SMB) + T2 (Enterprise) | FEAT | P0 | L | 0018 |
| 16.7 | Как SecOps, я хочу admin API для tenant management | FEAT | P1 | M | 0011 |

### EP-17: Hashchain Audit

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 17.1 | Как система, я хочу hashchain engine: каждый event содержит prev_hash | FEAT | P0 | L | 0006 |
| 17.2 | Как SecOps, я хочу WORM storage: S3 + Object Lock (или MinIO on-prem) | INFRA | P0 | L | 0006 |
| 17.3 | Как SecOps, я хочу verification tool: проверка целостности цепочки | SEC | P0 | M | 0006 |
| 17.4 | Как SecOps, я хочу compliance export (PDF/JSON) для auditor | COMPL | P1 | M | 0006 |
| 17.5 | Как SRE, я хочу tiered storage: hot 30d / cold 1y / archive 7y | INFRA | P1 | L | 0006 |

### EP-18: Threat Intel Sync

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 18.1 | Как SecOps, я хочу central threat-intel feed (signed JSON manifest) | INFRA | P0 | L | 0012 |
| 18.2 | Как система, я хочу pull каждые 15 мин + Ed25519 signature verification | FEAT | P0 | L | 0012 |
| 18.3 | Как система, я хочу atomic Qdrant update + snapshot для rollback | FEAT | P0 | M | 0012 |
| 18.4 | Как система, я хочу cache invalidation при threat-intel update | FEAT | P0 | M | 0012 |
| 18.5 | Как SecOps, я хочу emergency push API + 2FA | FEAT | P1 | M | 0012 |

### EP-19: Feedback Loop

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 19.1 | Как SecOps, я хочу feedback API: `POST /feedback` (FP/FN label) | FEAT | P0 | M | 0013 |
| 19.2 | Как SecOps, я хочу SecOps dashboard для разметки FP/FN | FEAT | P0 | L | 0013 |
| 19.3 | Как система, я хочу sampler: 1% random + 100% blocked → label queue | FEAT | P0 | M | 0013 |
| 19.4 | Как SecOps, я хочу Kafka topic `feedback-labels` | INFRA | P1 | M | 0013 |
| 19.5 | Как SecOps, я хочу anonymization: PII → Vault tokens перед label queue | SEC | P0 | M | 0007, 0013 |
| 19.6 | Как ML engineer, я хочу metrics: FP rate / FN rate trend | ML | P1 | S | 0013 |

### EP-20: Retrain Pipeline

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 20.1 | Как ML engineer, я хочу MLflow model registry | ML | P0 | M | 0013 |
| 20.2 | Как ML engineer, я хочу weekly retrain job (Kubeflow/Argo) | ML | P0 | L | 0013 |
| 20.3 | Как ML engineer, я хочу hold-out validation: auto-rollback при F1 regression > 5% | ML | P0 | M | 0013 |
| 20.4 | Как ML engineer, я хочу canary deploy: 5% traffic × 24h | ML | P0 | L | 0013 |
| 20.5 | Как ML engineer, я хочу auto-rollback metrics (FP, FN, latency, error rate) | ML | P0 | M | 0013 |

### EP-21: Helm Chart & K8s

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 21.1 | Как SRE, я хочу Helm chart v1 (scanner + Redis + Qdrant) | INFRA | P0 | L | 0014 |
| 21.2 | Как SRE, я хочу values.yaml per environment (dev/staging/prod) | INFRA | P0 | M | 0014 |
| 21.3 | Как SRE, я хочу NetworkPolicies для zero-trust между подами | SEC | P1 | M | 0014 |
| 21.4 | Как SRE, я хочу PodDisruptionBudgets для HA | INFRA | P1 | S | 0014 |

---

## 5. 🟪 PRODUCTION — Epic и Stories

### EP-22: HA Deployment

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 22.1 | Как SRE, я хочу multi-AZ deployment (3 AZ) | INFRA | P0 | L | 0001, 0008 |
| 22.2 | Как SRE, я хочу replica=3 для scanner pods, round-robin LB | INFRA | P0 | M | 0001 |
| 22.3 | Как SRE, я хочу Qdrant RF=3 для multi-AZ | INFRA | P0 | M | 0005 |
| 22.4 | Как SRE, я хочу Postgres streaming replication | INFRA | P0 | M | 0006 |
| 22.5 | Как SRE, я хочу Vault HA (Raft consensus, 3 nodes) | INFRA | P0 | M | 0007 |

### EP-23: Air-gapped Deployment

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 23.1 | Как SRE, я хочу offline Docker image bundle (.tar.gz signed) | INFRA | P0 | L | 0012, 0014 |
| 23.2 | Как SecOps, я хочу offline license file (signed) | SEC | P0 | M | 0014 |
| 23.3 | Как SecOps, я хочу threat-intel offline bundle import | FEAT | P0 | M | 0012 |
| 23.4 | Как SRE, я хочу internal NTP server requirement documented | DOC | P1 | S | 0014 |

### EP-24: Compliance & Audit

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 24.1 | Как SecOps, я хочу external notary snapshots (daily, signed) | SEC | P0 | L | 0006 |
| 24.2 | Как SecOps, я хочу GDPR review: data flow diagram, DPIA | COMPL | P0 | L | compliance |
| 24.3 | Как SecOps, я хочу ФЗ-152 review (для RU customers) | COMPL | P0 | M | compliance |
| 24.4 | Как SecOps, я хочу SIEM integration (Splunk forwarder) | FEAT | P1 | M | 0006 |
| 24.5 | Как SecOps, я хочу audit retention policy: 7 years cold | COMPL | P0 | M | 0006 |

### EP-25: SOC 2 Preparation

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 25.1 | Как SecOps, я хочу SOC 2 scope document | COMPL | P0 | L | compliance |
| 25.2 | Как SecOps, я хочу policy: access control, change management | DOC | P0 | L | compliance |
| 25.3 | Как SecOps, я хочу pen-test (external vendor) | SEC | P0 | XL | security |
| 25.4 | Как SecOps, я хочу incident response runbook | DOC | P0 | M | compliance |

---

## 6. 🟧 EXTENDED — Epic и Stories

### EP-26: Bloom Filter Cache

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 26.1 | Как ML engineer, я хочу PoC: Bloom filter для benign prompts | RES | P0 | M | 0017 |
| 26.2 | Как система, я хочу in-memory Bloom (10M bits, 1MB) | FEAT | P1 | M | 0017 |
| 26.3 | Как система, я хочу daily batch job: top-K benign → Bloom | FEAT | P1 | M | 0017 |
| 26.4 | Как SRE, я хочу метрику: Bloom hit rate vs slow path | INFRA | P1 | S | 0017 |

### EP-27: Adversarial Reinforcement Loop

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 27.1 | Как система, я хочу auto-route blocked prompts → label queue | FEAT | P0 | M | 0025 |
| 27.2 | Как ML engineer, я хочу dedup pipeline (не дублировать corpus) | ML | P0 | M | 0025 |
| 27.3 | Как SecOps, я хочу verify queue (1-click approve) | FEAT | P0 | M | 0025 |
| 27.4 | Как ML engineer, я хочу integration с retrain pipeline (ADR-0013) | ML | P0 | M | 0025 |
| 27.5 | Как ML engineer, я хочу metrics: novel attacks caught per week | ML | P1 | S | 0025 |

### EP-28: Detector Fan-out

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 28.1 | Как система, я хочу parallel execution of N detectors (asyncio) | FEAT | P0 | L | 0016 |
| 28.2 | Как система, я хочу early-exit при high-confidence BLOCK | FEAT | P0 | M | 0016 |
| 28.3 | Как система, я хочу timeout per detector (default 20ms) | FEAT | P1 | S | 0016 |
| 28.4 | Как SecOps, я хочу audit: какие детекторы отработали, какие отменены | SEC | P1 | M | 0016 |

### EP-29: LLM-as-judge Fallback

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 29.1 | Как ML engineer, я хочу PoC: LLM-as-judge для high-risk low-confidence | RES | P0 | L | 0020 |
| 29.2 | Как система, я хочу async post-factum invocation (< 1% traffic) | FEAT | P0 | M | 0020 |
| 29.3 | Как система, я хочу PII redaction до отправки в LLM-as-judge | SEC | P0 | M | 0007, 0020 |
| 29.4 | Как ML engineer, я хочу metrics: LLM-as-judge accuracy vs ML classifier | ML | P1 | M | 0020 |

### EP-30: Unified Model

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 30.1 | Как ML engineer, я хочу multi-task model: e5 + classification head | ML | P0 | XL | 0019 |
| 30.2 | Как ML engineer, я хочу 2-phase training: frozen → joint | ML | P1 | L | 0019 |
| 30.3 | Как ML engineer, я хочу benchmark: unified vs separate models | ML | P0 | L | 0019 |
| 30.4 | Как ML engineer, я хочу migration plan (zero-downtime switch) | ML | P0 | M | 0019 |
| 30.5 | Как ML engineer, я хочу rollback plan (если unified хуже) | ML | P0 | S | 0019 |

### EP-31: Predictive Streaming

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 31.1 | Как система, я хочу buffer 1-chunk-ahead в streaming inspector | FEAT | P0 | L | 0022 |
| 31.2 | Как система, я хочу cancellation propagation на BLOCK | FEAT | P0 | M | 0022 |
| 31.3 | Как SRE, я хочу metrics: latency overhead до первого токена | INFRA | P1 | S | 0022 |
| 31.4 | Как SecOps, я хочу comparison: ADR-0022 vs ADR-0003 baseline | RES | P0 | M | 0022 |

### EP-32: RAG Security Module

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 32.1 | Как SecOps, я хочу валидацию источников RAG (URL/domain whitelist) | FEAT | P0 | L | Roadmap презентации |
| 32.2 | Как система, я хочу контроль качества retrieved docs (relevance score) | FEAT | P0 | L | Roadmap презентации |
| 32.3 | Как SecOps, я хочу проверка актуальности (timestamp, staleness alert) | FEAT | P1 | M | Roadmap презентации |
| 32.4 | Как SecOps, я хочу data security: PII в RAG docs → redaction | SEC | P0 | L | 0007 + RAG |
| 32.5 | Как SecOps, я хочу RAG poisoning detection | SEC | P1 | XL | novel |
| 32.6 | Как SecOps, я хочу metrics: RAG source distribution, relevance trend | INFRA | P1 | M | novel |

### EP-33: Vector DB Security Module

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 33.1 | Как SecOps, я хочу integrity проверки векторной БД (hash сравнение) | SEC | P0 | L | Roadmap презентации |
| 33.2 | Как ML engineer, я хочу poisoning detection: anomaly в embeddings | ML | P1 | XL | novel |
| 33.3 | Как ML engineer, я хочу мониторинг дрейфа embeddings (vs baseline) | ML | P0 | L | novel |
| 33.4 | Как SecOps, я хочу integrity scan scheduled (daily) | INFRA | P1 | M | novel |
| 33.5 | Как SRE, я хочу alert: integrity failure → critical | INFRA | P0 | S | novel |

### EP-34: Behavioral Analysis

| # | Story | Тип | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|
| 34.1 | Как ML engineer, я хочу probing framework для LLM (auto-jailbreak attempts) | ML | P0 | XL | Roadmap презентации |
| 34.2 | Как ML engineer, я хочу behavioral fingerprinting (LLM response patterns) | ML | P1 | XL | novel |
| 34.3 | Как SecOps, я хочу reverse-engineering dashboard | FEAT | P1 | L | novel |
| 34.4 | Как SecOps, я хочу automated GCG-attack generator | SEC | P0 | XL | novel |
| 34.5 | Как ML engineer, я хочу continuous red-team corpus growth | ML | P1 | L | 0025 |

---

## 6.1. 🟪🟧 Дополнительные Epic'и (gap-анализ)

> Созданы в результате gap-анализа между ARCHITECT.md/ROADMAP.md и BACKLOG. Покрывают элементы архитектуры, которые были упомянуты, но не имели отдельных stories.

### EP-35: Ingress Gateway & Rate Limiting

**Источник gap:** ARCHITECT.md раздел 7.1 (Ingress Gateway: TLS, mTLS, rate-limit) не покрыт отдельным epic'ом. EP-01 покрывает только scanner HTTP endpoint, но не Envoy/Nginx layer.

| # | Story | Тип | Фаза | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|---|
| 35.1 | Как SRE, я хочу Envoy sidecar/frontend с TLS termination | INFRA | 🟩 ALPHA | P0 | L | 0001 |
| 35.2 | Как SecOps, я хочу per-tenant rate limit (RPS + token budget) | SEC | 🟨 BETA | P0 | M | 0011 |
| 35.3 | Как SecOps, я хочу global rate limit (защита от DDoS) | SEC | 🟪 PROD | P0 | M | 0011 |
| 35.4 | Как SRE, я хочу JWT short-lived token auth (5min TTL, refresh) | SEC | 🟩 ALPHA | P1 | M | 0011, ARCHITECT 11 |
| 35.5 | Как SecOps, я хочу token length limit + complexity score (DoS protection) | SEC | 🟩 ALPHA | P0 | M | ARCHITECT 11 |
| 35.6 | Как SRE, я хочу mTLS enforcement между scanner и Ingress | INFRA | 🟨 BETA | P1 | M | 0001 |
| 35.7 | Как SRE, я хочу IP-based geo-block (опционально per tenant) | SEC | 🟪 PROD | P2 | M | 0011 |

### EP-36: Report Engine & SecOps Dashboard

**Источник gap:** ARCHITECT.md раздел 7.11 описывает Report Engine (JSON API, HTML dashboard, PDF compliance export, WebSocket live-updates). В BACKLOG был только PDF export (EP-17.4). WebSocket live-updates и SecOps HTML dashboard не покрыты.

| # | Story | Тип | Фаза | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|---|
| 36.1 | Как SecOps, я хочу WebSocket endpoint для live audit events | FEAT | 🟨 BETA | P1 | L | 0015 |
| 36.2 | Как SecOps, я хочу HTML dashboard: top-10 flagged prompts, FP/FN trend | FEAT | 🟨 BETA | P0 | L | 0015 |
| 36.3 | Как SecOps, я хочу security Grafana dashboard (detection rate by type) | INFRA | 🟨 BETA | P0 | M | 0015 |
| 36.4 | Как SecOps, я хочу compliance Grafana dashboard (audit completeness, hashchain) | INFRA | 🟪 PROD | P0 | M | 0015 |
| 36.5 | Как SecOps, я хочу notification: post-factum BLOCK на уже отправленной генерации | FEAT | 🟩 ALPHA | P1 | M | 0003 |
| 36.6 | Как SecOps, я хочу Slack/Teams integration для critical alerts | INFRA | 🟨 BETA | P1 | M | 0015 |
| 36.7 | Как SecOps, я хочу export audit data to S3/BigQuery для BI analysis | FEAT | 🟪 PROD | P2 | L | 0006 |

### EP-37: Generation Analyzer (Extended Detection)

**Источник gap:** ARCHITECT.md раздел 7.7 описывает Generation Analyzer (PII/secret leakage, toxicity, **hallucination indicators**, **topic-policy violation**). В BACKLOG есть EP-07 streaming (PII/secrets на чанках), но hallucination detection и topic policy не покрыты.

| # | Story | Тип | Фаза | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|---|
| 37.1 | Как ML engineer, я хочу hallucination indicator detection (citation check) | ML | 🟧 EXT | P1 | XL | novel |
| 37.2 | Как SecOps, я хочу topic-policy enforcement (medical/legal/financial advice restriction) | FEAT | 🟧 EXT | P0 | L | ARCHITECT 1.1 |
| 37.3 | Как ML engineer, я хочу toxicity ML classifier (RoBERTa-toxic) на generation chunks | ML | 🟩 ALPHA | P0 | L | 0010 |
| 37.4 | Как SecOps, я хочу configurable topic whitelist/blacklist per tenant | FEAT | 🟧 EXT | P0 | M | 0011 |
| 37.5 | Как ML engineer, я хочу source attribution check (LLM claims "from X" → verify X) | ML | 🟧 EXT | P2 | XL | novel |

### EP-38: DR, Backup & Restore

**Источник gap:** ARCHITECT.md раздел 10.2 (RTO/RPO per component), ROADMAP DOC-08 (Disaster Recovery Plan) — упомянуты, но без детальных stories.

| # | Story | Тип | Фаза | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|---|
| 38.1 | Как SRE, я хочу automated backup: Qdrant snapshots каждые 6 часов → S3 | INFRA | 🟨 BETA | P0 | M | 0005 |
| 38.2 | Как SRE, я хочу automated backup: PostgreSQL pg_basebackup + WAL archiving | INFRA | 🟨 BETA | P0 | M | 0006 |
| 38.3 | Как SRE, я хочу Vault snapshots (Raft snapshots каждые 1 час) | INFRA | 🟨 BETA | P0 | M | 0007 |
| 38.4 | Как SRE, я хочу DR runbook: tested recovery procedure (RTO 5min, RPO 0 для audit) | DOC | 🟪 PROD | P0 | L | 0014 |
| 38.5 | Как SRE, я хочу quarterly DR drill (chaos engineering: simulate multi-AZ failure) | TEST | 🟪 PROD | P1 | XL | 0008 |
| 38.6 | Как SRE, я хочу cross-region replication для audit store (disaster recovery) | INFRA | 🟪 PROD | P1 | L | 0006 |

### EP-39: Cost & Capacity Management (FinOps)

**Источник gap:** ROADMAP раздел 9 (бюджет), ARCHITECT.md 10.3 (sizing). Нет monitoring cost per tenant, нет capacity calculator.

| # | Story | Тип | Фаза | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|---|
| 39.1 | Как SRE, я хочу cost dashboard: GPU hours, traffic cost, storage per tenant | INFRA | 🟨 BETA | P1 | M | novel |
| 39.2 | Как SecOps, я хочу per-tenant token budget (count requests, RPS per tenant) | FEAT | 🟨 BETA | P0 | M | 0011 |
| 39.3 | Как PM, я хочу capacity planning calculator (веб-инструмент: RPS → vCPU/GPU/RAM) | FEAT | 🟪 PROD | P1 | M | ARCHITECT 10.3 |
| 39.4 | Как SRE, я хочу alert: GPU utilization > 80% (scale up trigger) | INFRA | 🟪 PROD | P0 | S | 0015 |
| 39.5 | Как SRE, я хочу alert: storage growth rate anomaly (audit storage explosion) | INFRA | 🟪 PROD | P1 | M | 0015 |

### EP-40: Scanner Self-Security

**Источник gap:** ARCHITECT.md раздел 11 «Безопасность самого сканера» — 8 угроз перечислены, но без отдельных stories. Часть покрыта SEC-01..07, но не все.

| # | Story | Тип | Фаза | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|---|
| 40.1 | Как SecOps, я хочу input sanitization на ML classifier inputs (anti-injection в scanner) | SEC | 🟩 ALPHA | P0 | M | ARCHITECT 11 |
| 40.2 | Как SecOps, я хочу structured logging с PII redaction layer (PII → token перед log) | SEC | 🟩 ALPHA | P0 | M | ARCHITECT 11 |
| 40.3 | Как SecOps, я хочу 2-eyes approval на policy merge (CODEOWNERS + signed PRs) | SEC | 🟨 BETA | P0 | S | ARCHITECT 11 |
| 40.4 | Как SecOps, я хочу source whitelist для threat-intel bundles (trusted publishers only) | SEC | 🟨 BETA | P0 | M | 0012 |
| 40.5 | Как ML engineer, я хочу model watermarking (detect model extraction attacks) | ML | 🟧 EXT | P2 | XL | ARCHITECT 11 |
| 40.6 | Как SecOps, я хочу replay attack protection (nonce + idempotency keys) | SEC | 🟨 BETA | P0 | M | novel |
| 40.7 | Как SecOps, я хочу API rate limit на embedding/classifier endpoints (model extraction defense) | SEC | 🟩 ALPHA | P0 | S | ARCHITECT 11 |
| 40.8 | Как SecOps, я хочу dependency vulnerability scanning (weekly Trivy/snyk cron) | SEC | all | P0 | S | SEC-07 |
| 40.9 | Как SecOps, я хочу prompt DB poisoning detection (integrity hash на corpus) | SEC | 🟨 BETA | P1 | M | 0012 |
| 40.10 | Как SecOps, я хочу scanner admin API RBAC (только SecOps role может config) | SEC | 🟨 BETA | P0 | M | 0011 |

### EP-41: Multi-Language SDKs & API Versioning

**Источник gap:** EP-01.6 только Python SDK. ARCHITECT.md ADR-0001 упоминает «языко-агностичность». Нет Node.js, Go, Java SDK. Нет API versioning.

| # | Story | Тип | Фаза | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|---|
| 41.1 | Как разработчик, я хочу Node.js SDK (TypeScript) | FEAT | 🟨 BETA | P1 | L | 0001 |
| 41.2 | Как разработчик, я хочу Go SDK | FEAT | 🟨 BETA | P2 | L | 0001 |
| 41.3 | Как разработчик, я хочу Java SDK | FEAT | 🟪 PROD | P2 | L | 0001 |
| 41.4 | Как система, я хочу API versioning (URL `/v1/scan`, deprecation headers) | FEAT | 🟨 BETA | P0 | M | novel |
| 41.5 | Как система, я хочу API deprecation policy (6 months notice + sunset header) | DOC | 🟨 BETA | P1 | S | novel |
| 41.6 | Как разработчик, я хочу OpenAPI 3.1 spec (auto-generated client SDKs) | DOC | 🟩 ALPHA | P0 | M | DOC-02 |
| 41.7 | Как разработчик, я хочу Postman collection + sample requests | DOC | 🟩 ALPHA | P2 | S | DOC-02 |

### EP-42: Cross-Model Policies & Data Residency

**Источник gap:** ARCHITECT.md Phase 4 (cross-model policies — политики для нескольких LLM-провайдеров). ADR-0011 compliance (data residency для GDPR). Не покрыты.

| # | Story | Тип | Фаза | Приоритет | Оценка | ADR |
|---|---|---|---|---|---|---|
| 42.1 | Как SecOps, я хочу cross-model policy translation (Rego → per-LLM-provider safety rules) | FEAT | 🟧 EXT | P1 | XL | ARCHITECT 14 |
| 42.2 | Как SecOps, я хочу provider-specific detectors (OpenAI moderation API integration) | FEAT | 🟧 EXT | P2 | M | novel |
| 42.3 | Как SecOps, я хочу data residency enforcement (EU tenant → EU LLM endpoint only) | SEC | 🟪 PROD | P0 | L | 0011 |
| 42.4 | Как SecOps, я хочу geo-routing (IP-based, EU/US/RU traffic → respective infra) | INFRA | 🟪 PROD | P1 | L | 0011 |
| 42.5 | Как SecOps, я хочу cross-tenant learning opt-in (consent-based data sharing) | FEAT | 🟧 EXT | P2 | M | 0011 |
| 42.6 | Как SecOps, я хочу tenant data deletion API (GDPR right to be forgotten) | SEC | 🟪 PROD | P0 | L | 0011 |
| 42.7 | Как SecOps, я хочу audit data residency (EU tenant → EU audit bucket only) | SEC | 🟪 PROD | P0 | M | 0011 |

---

## 7. Cross-cutting concerns

### 7.1. Документация (сквозная задача)

| # | Story | Фаза | Приоритет | Оценка |
|---|---|---|---|---|
| DOC-01 | README + Quick Start | MVP | P0 | S |
| DOC-02 | API Reference (OpenAPI spec) | ALPHA | P0 | M |
| DOC-03 | Integration Guide (SDK, proxy setup) | ALPHA | P1 | M |
| DOC-04 | Admin Guide (tenants, policies) | BETA | P0 | L |
| DOC-05 | Compliance Documentation Pack | PROD | P0 | L |
| DOC-06 | Runbooks (5 scenarios) | PROD | P0 | L |
| DOC-07 | Customer Onboarding Playbook | BETA | P0 | M |
| DOC-08 | Disaster Recovery Plan | PROD | P0 | L |

### 7.2. Тестирование (сквозная задача)

| # | Story | Фаза | Приоритет | Оценка |
|---|---|---|---|---|
| TEST-01 | Unit tests (90% coverage) | MVP | P0 | L |
| TEST-02 | Integration tests (component) | ALPHA | P0 | L |
| TEST-03 | E2E tests (user scenarios) | ALPHA | P0 | L |
| TEST-04 | Load testing (k6, 1000 RPS) | BETA | P0 | L |
| TEST-05 | Chaos engineering (CB testing) | BETA | P1 | L |
| TEST-06 | Pen-test (external) | PROD | P0 | XL |
| TEST-07 | Red-team automation | EXT | P0 | XL |

### 7.3. Security hardening (сквозная задача)

| # | Story | Фаза | Приоритет | Оценка |
|---|---|---|---|---|
| SEC-01 | mTLS между scanner и AI App | ALPHA | P0 | M |
| SEC-02 | mTLS между scanner и LLM provider | ALPHA | P1 | M |
| SEC-03 | Secrets management (Vault для scanner's own secrets) | ALPHA | P0 | M |
| SEC-04 | RBAC для admin API | BETA | P0 | M |
| SEC-05 | Audit log access control | BETA | P0 | M |
| SEC-06 | Threat model review (per phase) | all | P0 | M |
| SEC-07 | Dependency audit (weekly cron) | all | P0 | S |

---

## 8. Приоритизация — Top 20 ближайших задач

Топ-20 stories для Sprint 1 (MVP Phase, недели 1–4):

| # | Story | Epic | Приоритет | Оценка |
|---|---|---|---|---|
| 1 | 1.1 HTTP endpoint `/scan` | EP-01 | P0 | M |
| 2 | 1.2 Proxy с инспекцией обоих направлений | EP-01 | P0 | L |
| 3 | 1.5 OpenAI-совместимый API | EP-01 | P0 | L |
| 4 | 1.3 `/health` endpoint | EP-01 | P0 | S |
| 5 | 1.8 API-key auth | EP-01 | P0 | S |
| 6 | 2.1 Regex для SSN/email/passport | EP-02 | P0 | M |
| 7 | 2.2 Regex для AWS keys/JWT/credit cards | EP-02 | P0 | M |
| 8 | 2.4 YAML-формат правил | EP-02 | P0 | S |
| 9 | 2.5 Unit-тесты для правил | EP-02 | P0 | M |
| 10 | 3.1 Redis decision cache | EP-03 | P0 | M |
| 11 | 3.2 Метрика cache hit rate | EP-03 | P0 | S |
| 12 | 4.1 PostgreSQL audit table | EP-04 | P0 | M |
| 13 | 4.4 PII redaction в audit | EP-04 | P0 | S |
| 14 | 5.1 Hardcoded PDP | EP-05 | P0 | S |
| 15 | 5.2 `X-Scanner-Verdict` header | EP-05 | P0 | S |
| 16 | 6.1 docker-compose.yml | EP-06 | P0 | M |
| 17 | 6.2 Dockerfile | EP-06 | P0 | S |
| 18 | 6.3 README Quick Start | EP-06 | P0 | S |
| 19 | 6.5 `/metrics` Prometheus endpoint | EP-06 | P0 | S |
| 20 | TEST-01 Unit tests 90% coverage | cross | P0 | L |

**Итого Sprint 1**: ~50 SP, команда 5 человек × 2 недели = 10 человеко-недель = ~40–50 SP. Реалистично.

---

## 9. Матрица Epic × Phase × ADR

| Epic | MVP | ALPHA | BETA | PROD | EXT | ADR |
|---|---|---|---|---|---|---|
| EP-01 Reverse Proxy | ✅ | | | | | 0001 |
| EP-02 Rule Engine | ✅ | | | | | 0002 |
| EP-03 Decision Cache | ✅ | | | | | 0002 |
| EP-04 Basic Audit | ✅ | | | | | 0006 basic |
| EP-05 Hardcoded PDP | ✅ | | | | | 0004 basic |
| EP-06 MVP Deploy | ✅ | | | | | 0014, 0015 basic |
| EP-07 Streaming | | ✅ | | | | 0003 |
| EP-08 Embedding | | ✅ | | | | 0009 |
| EP-09 Qdrant | | ✅ | | | | 0005 |
| EP-10 ML Classifier | | ✅ | | | | 0010 |
| EP-11 Two-tier | | ✅ | | | | 0002 |
| EP-12 OPA PDP | | ✅ | | | | 0004 |
| EP-13 Vault | | ✅ | | | | 0007 |
| EP-14 Circuit Breaker | | ✅ | | | | 0008 |
| EP-15 Observability | | ✅ | | | | 0015 |
| EP-16 Multi-tenancy | | | ✅ | | | 0011, 0018 |
| EP-17 Hashchain Audit | | | ✅ | | | 0006 |
| EP-18 Threat Intel | | | ✅ | | | 0012 |
| EP-19 Feedback Loop | | | ✅ | | | 0013 |
| EP-20 Retrain | | | ✅ | | | 0013 |
| EP-21 Helm/K8s | | | ✅ | | | 0014 |
| EP-22 HA | | | | ✅ | | 0001, 0008 |
| EP-23 Air-gapped | | | | ✅ | | 0012, 0014 |
| EP-24 Compliance | | | | ✅ | | 0006 |
| EP-25 SOC 2 | | | | ✅ | | compliance |
| EP-26 Bloom Cache | | | | | ✅ | 0017 |
| EP-27 Adversarial Loop | | | | | ✅ | 0025 |
| EP-28 Fan-out | | | | | ✅ | 0016 |
| EP-29 LLM-as-judge | | | | | ✅ | 0020 |
| EP-30 Unified Model | | | | | ✅ | 0019 |
| EP-31 Predictive Streaming | | | | | ✅ | 0022 |
| EP-32 RAG Security | | | | | ✅ | Roadmap |
| EP-33 Vector DB Security | | | | | ✅ | Roadmap |
| EP-34 Behavioral Analysis | | | | | ✅ | Roadmap |
| EP-35 Ingress Gateway | | ✅ | ✅ | ✅ | | 0001, 0011 |
| EP-36 Report Engine & Dashboard | | ✅ | ✅ | ✅ | | 0015 |
| EP-37 Generation Analyzer (Extended) | | ✅ | | | ✅ | 0010, ARCHITECT 1.1 |
| EP-38 DR, Backup & Restore | | | ✅ | ✅ | | 0005, 0006, 0007 |
| EP-39 FinOps | | | ✅ | ✅ | | 0011, ARCHITECT 10.3 |
| EP-40 Scanner Self-Security | | ✅ | ✅ | | ✅ | ARCHITECT 11 |
| EP-41 Multi-lang SDK & API Versioning | | ✅ | ✅ | ✅ | | 0001 |
| EP-42 Cross-Model Policies & Residency | | | | ✅ | ✅ | 0011, ARCHITECT 14 |

---

## 10. Definition of Done (DoD) для каждой Story

- [ ] Код написан и закоммичен в Git (PR approved 2 reviewers).
- [ ] Unit-тесты добавлены, coverage ≥ 90% для новых строк.
- [ ] Integration tests проходят в CI.
- [ ] Документация обновлена (API ref, README, runbook если нужно).
- [ ] ADR updated, если изменилось архитектурное решение.
- [ ] Метрики добавлены в Grafana, если применимо.
- [ ] Логи структурированы (JSON, OTel trace context).
- [ ] Security review (если touching auth, PII, audit).
- [ ] Performance test, если touching hot path (latency budget).
- [ ] Backward compatibility: не сломало ли existing API.

---

## 11. Definition of Ready (DoR) для Story перед Sprint

- [ ] Описана user story («как ... я хочу ... чтобы ...»).
- [ ] Acceptance criteria (2+ проверяемых условий).
- [ ] Привязка к Epic и ADR.
- [ ] Оценка в SP проведена командой.
- [ ] Зависимости выявлены (другие stories / external).
- [ ] UX mockup (если UI).
- [ ] Security implications рассмотрены.

---

## 12. Каденция спринтов

- **Длительность спринта**: 2 недели.
- **Ceremonies**:
  - Sprint Planning (понедельник, 1 час).
  - Daily Standup (15 мин).
  - Sprint Review (пятница, 1 час).
  - Sprint Retrospective (пятница, 30 мин).
- **Intra-phase releases**: в конце каждого спринта — internal release.
- **Phase releases**: в конце фазы — external release (для BETA — design partners, для PROD — GA).

---

## 13. Gap-анализ (какие пробелы закрыты)

В результате gap-анализа между ARCHITECT.md/ROADMAP.md и BACKLOG.md были выявлены и закрыты следующие пробелы:

### 13.1. Закрытые пробелы

| # | Что было пробелом | Источник | EP | Stories |
|---|---|---|---|---|
| 1 | Ingress Gateway (Envoy/Nginx, TLS, rate-limit) | ARCHITECT 7.1 | EP-35 | 35.1–35.7 (7 stories) |
| 2 | Report Engine (WebSocket, HTML dashboard, SecOps Grafana) | ARCHITECT 7.11 | EP-36 | 36.1–36.7 (7 stories) |
| 3 | Hallucination detection, topic-policy, source attribution | ARCHITECT 7.7, 1.1 | EP-37 | 37.1–37.5 (5 stories) |
| 4 | DR, Backup, Restore (RTO/RPO, DR drill) | ARCHITECT 10.2, ROADMAP DOC-08 | EP-38 | 38.1–38.6 (6 stories) |
| 5 | Cost monitoring, per-tenant budget, capacity calculator | ARCHITECT 10.3, ROADMAP 9 | EP-39 | 39.1–39.5 (5 stories) |
| 6 | Scanner self-security (input sanitization, log redaction, 2-eyes, source whitelist, model watermarking, replay protection) | ARCHITECT 11 | EP-40 | 40.1–40.10 (10 stories) |
| 7 | Multi-language SDKs (Node.js, Go, Java), API versioning, deprecation policy | ADR-0001 | EP-41 | 41.1–41.7 (7 stories) |
| 8 | Cross-model policies, data residency, geo-routing, GDPR right to be forgotten | ARCHITECT 14, ADR-0011 | EP-42 | 42.1–42.7 (7 stories) |

**Итого закрыто пробелов:** 8 группировок, 54 новые stories, ~260 SP.

### 13.2. Элементы, не требующие отдельных stories

Некоторые элементы ARCHITECT/ROADMAP уже покрыты cross-cutting concerns или другими EP:

| Элемент | Где покрыт |
|---|---|
| mTLS между scanner и LLM | SEC-02 (cross-cutting) |
| mTLS между scanner и Ingress | EP-35.6 |
| Dependency vulnerability scanning | EP-40.8 (= SEC-07) |
| Pen-test external | TEST-06 (cross-cutting) |
| Load testing | TEST-04 (cross-cutting) |
| Chaos engineering | EP-38.5 (DR drill) + TEST-05 |
| OpenAPI spec | EP-41.6 (= DOC-02) |
| Customer onboarding playbook | DOC-07 |
| Disaster Recovery Plan | EP-38.4 (runbook) |

### 13.3. Намеренно отложенные элементы (Phase 5+, не входят в текущий roadmap)

| Элемент | Причина откладывания |
|---|---|
| LLM-Native PII Restraint через RLHF | Очень дорогой ($100K+), требует LLM-fine-tune capability — Phase 5+ |
| Neural Policy Engine (замена OPA) | Теряет explainability, compliance risk — Phase 5+ research |
| Serverless scanner (Lambda) | Cold start latency incompatible с inline — Phase 5+ для edge deployments |
| Multi-modal embeddings (image/audio) | Не входит в текущий scope (text only) — Phase 6+ |
| Custom fine-tuned embedding model с нуля | NRE cost несопоставим с выгодой — Phase 6+ |

Эти элементы зафиксированы в ADR-0023, 0028, 0029 (ТРИЗ-блок) со статусом **Proposed**, но не имеют stories в BACKLOG — это сознательное решение отложить до получения empirical evidence на PoC.

### 13.4. Verification checklist

- [x] Все 14 компонентов из ARCHITECT.md раздела 7 (Компонентная модель) покрыты stories.
- [x] Все 10 NFR из ARCHITECT.md раздела 10 имеют мониторинг stories.
- [x] Все 8 угроз из ARCHITECT.md раздела 11 (Security самого сканера) покрыты EP-40.
- [x] Все 4 ADR для baseline (0001–0004) покрыты stories.
- [x] Все 11 ADR для ALPHA+BETA (0005–0015) покрыты stories.
- [x] Все 14 ТРИЗ-ADR (0016–0029) со статусом Accepted/Proposed покрыты stories, кроме 3 отложенных (0023, 0028, 0029).
- [x] Все deliverables из ROADMAP (M1.x, A2.x, B3.x, P4.x, E5.x) имеют связанные stories.
- [x] Cross-cutting concerns (документация, тестирование, security) покрывают все фазы.

---

## 14. Связанные документы

- [ROADMAP.md](ROADMAP.md) — фазы и таймлайн.
- [ARCHITECT.md](ARCHITECT.md) — архитектура системы.
- [ADR.md](ADR.md) — архитектурные решения.
- [ATRIZ.md](ATRIZ.md) — ТРИЗ-анализ.
- [worklog.md](../worklog.md) — журнал работ.

---

*Backlog — живой документ. Приоритеты пересматриваются на Sprint Planning.*
