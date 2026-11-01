# 🛡️ OryaSecurity

**LLM Security Scanner — a protective inline proxy between your AI app and the LLM provider**

Inspects incoming prompts for PII / secrets / prompt-injection, outgoing generations for leaks,
returns an `allow` / `block` decision and a full report for SecOps.

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-15-000000?logo=next.js&logoColor=white)](https://nextjs.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)](https://redis.io/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue)](./LICENSE)
[![Coverage](https://img.shields.io/badge/coverage-90%25%20target-brightgreen)](./backend/tests)
[![Phase](https://img.shields.io/badge/phase-MVP%20%2B%20ALPHA-orange)](./analytics/ROADMAP.md)

[Features](#-features) · [Architecture](#-architecture) · [Quick Start](#-quick-start) · [MVP](#-mvp-phase) · [ALPHA](#-alpha-phase) · [API](#-api) · [Roadmap](#-roadmap) · [Contributing](#-contributing)

---

## 📖 About

**OryaSecurity** (internal name — *LLM Security Scanner*) is a security reverse-proxy that sits
between a client AI application and an OpenAI-compatible LLM provider. The scanner inspects traffic
in both directions — incoming prompts and outgoing generations — blocking PII / secret leaks and
prompt-injection attacks, caching decisions, and writing a complete audit log.

> **Principles:** stateless scanner with external state in Redis/PostgreSQL, decision cache keyed by
> SHA256(prompt), two-tier pipeline (fast path — regex + cache, slow path — embedding + vector + ML).

Development is staged: the **MVP** (regex engine, cache, audit, Web UI) is ~85% complete, and the
**ALPHA** (streaming, ML classifier, OPA PDP, Vault, vector DB, observability) is implemented module
by module.

---

## ✨ Features

| | Feature | Description |
|---|---|---|
| 🔍 | **PII Detection** | US SSN, RU passports, emails, phones — regex + Luhn for cards |
| 🔑 | **Secret Detection** | AWS Access Keys, JWT, credit cards (Visa/MC/Amex with Luhn) |
| 🛡️ | **Prompt Injection Protection** | Regex patterns + ML classifier (DeBERTa-v3, 4 classes) |
| 🎯 | **Decision Cache** | SHA256(prompt) → verdict, TTL 5 min, fallback when Redis is down |
| 📊 | **Audit Log** | PostgreSQL, PII-redacted, indexes on tenant_id / verdict / hash |
| 🎨 | **Web Dashboard** | Next.js 15 + shadcn/ui: metrics, audit, rules editor, test, cache, config |
| ⚡ | **High Performance** | 100 RPS, p99 < 10 ms (fast path), < 50 ms (slow path) |
| 🔒 | **API Key Auth** | bcrypt hashes in PostgreSQL, JWT 24h for the UI |
| 🌊 | **SSE Streaming Inspection** | Chunked inspection of N=8 tokens, terminate-on-block |
| 🧠 | **ML Pipeline** | multilingual-e5 embeddings + DeBERTa classifier, ONNX int8 |
| 🗄️ | **Vector DB (Qdrant)** | 10K seed attacks, HNSW, scalar int8 quantization, recall > 95% |
| 📜 | **OPA Policy Decision Point** | Rego policies, versioned bundles, cosign-signed |
| 🗝️ | **Vault Redaction** | HashiCorp Vault + Presidio NER, PII tokenization |
| 🔌 | **Circuit Breaker** | Per-upstream (vector_db, ml, vault): fail-closed / fail-open / degrade |
| 📈 | **Observability** | OTel (OTLP) → Jaeger + Prometheus + Loki + Grafana |

---

## 🏗️ Architecture

```
                          ┌──────────────────────────────────────────────────────┐
                          │                   OryaSecurity                        │
                          │            (FastAPI + asyncio + uvicorn)             │
                          │                                                      │
   ┌──────────┐   POST    │  ┌──────────┐   ┌────────────┐   ┌───────────────┐    │   POST    ┌──────────────┐
   │  AI App  │──────────▶│  │ Reverse  │──▶│  Pipeline  │──▶│  LLM Provider │───────────────▶│  OpenAI API  │
   │ (Next.js)│◀──────────│  │  Proxy   │◀──│  (fast/slow)│◀──│   (inspected) │◀───────────────│  (gpt-3.5)   │
   └──────────┘  response │  └──────────┘   └────────────┘   └───────────────┘    │  response └──────────────┘
                          │        │             │    │                              │
                          │        ▼             ▼    ▼                              │
                          │  ┌─────────┐  ┌────────┐ ┌──────────┐ ┌──────────────┐  │
                          │  │  Redis  │  │ PDP    │ │ Audit    │ │  ML / Vector │  │
                          │  │ (cache) │  │ (OPA)  │ │ (PgSQL)  │ │  (Qdrant+Triton) │
                          │  └─────────┘  └────────┘ └──────────┘ └──────────────┘  │
                          └──────────────────────────────────────────────────────┘
                                                    │
                          ┌─────────────────────────┴────────────────────────────┐
                          │  Observability: OTel → Jaeger · Prometheus · Loki · Grafana  │
                          └─────────────────────────────────────────────────────────────┘
```

### Two-tier pipeline (ALPHA)

```
                ┌────────────────── FAST PATH (< 1 ms) ──────────────────┐
   request ───▶ │ Decision Cache → Rule Engine (regex) → Suspicion Tagger │ ──▶ ALLOW (no slow path)
                └────────────────────────────┬───────────────────────────┘
                                             │ suspicious
                ┌────────────────── SLOW PATH (p99 < 50 ms) ───────────────┐
   request ───▶ │ Embedding (e5) → Vector Search (Qdrant) → ML Classifier │ ──▶ OPA PDP → verdict
                │              [ circuit breakers per upstream ]          │
                └─────────────────────────────────────────────────────────┘
```

### Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12 · FastAPI 0.115 · asyncio · uvicorn · Pydantic v2 · structlog |
| Frontend | Next.js 15 (App Router) · TypeScript 5 · Tailwind CSS 4 · shadcn/ui · TanStack Query |
| Database | PostgreSQL 16 (audit) · Redis 7 (decision + embedding cache) |
| ML | Triton Inference Server · ONNX Runtime (int8) · Transformers · multilingual-e5-large · DeBERTa-v3-small |
| Vector DB | Qdrant (HNSW, scalar int8 quantization) |
| Policy | Open Policy Agent + Rego (versioned, cosign-signed bundles) |
| Secrets | HashiCorp Vault · Microsoft Presidio (NER) |
| Observability | OpenTelemetry (OTLP) · Jaeger · Prometheus · Loki · Grafana · Alertmanager |
| Infra | Docker Compose · Kubernetes + Helm (basic) · Nginx / Envoy ingress |
| Tests | pytest (≥ 90% coverage) · vitest · Playwright (E2E) |

---

## 🚀 Quick Start

### Option A — Docker Compose (5 minutes, recommended)

```bash
# 1. Clone
git clone https://github.com/reider85/OryaSecurity.git
cd OryaSecurity

# 2. Configure
cp .env.example .env
# edit .env: LLM_API_KEY, API_KEY_DEFAULT, JWT_SECRET, DB passwords

# 3. Start the full stack (backend + frontend + DB + cache)
docker compose --profile ui up -d

# 4. Verify
curl http://localhost:8000/health   # → {"status":"ok", ...}
curl http://localhost:3000          # → Web Dashboard
```

<details>
<summary><b>🐳 Available docker-compose profiles</b></summary>

```bash
# Backend only (scanner + postgres + redis)
docker compose up -d scanner

# With Web UI
docker compose --profile ui up -d

# With observability (Prometheus + Grafana)
docker compose --profile observability up -d

# Full ALPHA stack: + Triton, Qdrant, OPA, Vault, OTel, Jaeger, Loki
docker compose -f docker-compose.alpha.yml up -d
```

| Service | Port | Description |
|---|---|---|
| Web UI | http://localhost:3000 | Dashboard for SecOps |
| API | http://localhost:8000 | FastAPI scanner |
| API Docs | http://localhost:8000/docs | Swagger UI |
| Prometheus | http://localhost:9090 | Metrics (optional) |
| Grafana | http://localhost:3001 | Dashboards (admin / admin) |
| Jaeger | http://localhost:16686 | Traces (ALPHA) |
| OPA | http://localhost:8181 | Policy decisions (ALPHA) |
| Qdrant | http://localhost:6333 | Vector DB UI (ALPHA) |

</details>

### Option B — Manual setup (dev)

<details>
<summary><b>🔧 Backend + Frontend locally</b></summary>

```bash
# ── Backend ──────────────────────────────────────────────
cd backend
python -m venv venv && source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -e .
# PostgreSQL + Redis (via docker)
docker run -d --name postgres -e POSTGRES_DB=scanner -e POSTGRES_USER=scanner \
  -e POSTGRES_PASSWORD=scanner -p 5432:5432 postgres:16-alpine
docker run -d --name redis -p 6379:6379 redis:7-alpine
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# ── Frontend ─────────────────────────────────────────────
cd ../frontend
npm install
npm run dev     # http://localhost:3000
```

</details>

---

## 📦 MVP Phase

> **Scope:** EP-01..06 + EP-43 (Web UI Dashboard). Status — **~85% implemented** (29 / 34 prompts).
> Sources: [analytics/MVP-PROMPTS.md](./analytics/MVP-PROMPTS.md), [IMPLEMENTATION_STATUS.md](./IMPLEMENTATION_STATUS.md).

### ✅ EP-01 — Reverse Proxy Core

| # | Prompt | Status | What was done |
|---|---|:--:|---|
| 1 | HTTP endpoint `/scan` | ✅ | `POST /scan` → `{verdict, reason, latency_ms, request_id}`, `X-Scanner-Verdict` header, structured JSON logs, `prompt_hash = SHA256(prompt)` |
| 2 | Bidirectional inspection proxy | ✅ | `core/proxy.py` — prompt + generation inspection, OpenAI Chat Completions format, timeout 30s → 502 |
| 3 | `/health` endpoint | ✅ | DB/Redis status, HTTP 200/503, no auth |
| 4 | OpenAI-compatible API | ✅ | `POST /v1/chat/completions` — drop-in OpenAI base_url replacement |
| 5 | Python SDK | ✅ | `backend/sdk/python/` — sync + async clients (`ScannerClient` / `AsyncScannerClient`) |
| 6 | API-key auth | ✅ | bcrypt hashes in PostgreSQL, `Authorization: Bearer`, JWT 24h for the UI |

### ✅ EP-02 — Rule Engine

| # | Prompt | Status | What was done |
|---|---|:--:|---|
| 7 | Regex: SSN, RU passport, email | ✅ | `core/rules/pii.py` — `Rule` / `RuleMatch` dataclass, YAML config |
| 8 | Regex: AWS keys, JWT, credit cards | ✅ | `core/rules/secrets.py` + `validate_luhn()` for Visa/MC/Amex |
| 9 | YAML rule format | ✅ | `core/rules/loader.py` — hot-reload, jsonschema validation, versioning |
| 10 | Unit tests for rules | ✅ | Parametrized tests, 5+ positive/negative per rule, JSON fixtures |

<details>
<summary><b>Example rule (YAML)</b></summary>

```yaml
# backend/rules/pii.yaml
rules:
  - id: pii_ssn_us
    name: "US Social Security Number"
    type: regex
    pattern: '\b\d{3}-\d{2}-\d{4}\b'
    severity: high
    action: block
    version: "1.0.0"
```

</details>

### ✅ EP-03 — Decision Cache

| # | Prompt | Status | What was done |
|---|---|:--:|---|
| 11 | Redis decision cache | ✅ | `core/cache.py` — key = SHA256(prompt), TTL 5 min, fallback-to-scan on Redis failure, `X-Scanner-Cache: HIT/MISS` |
| 12 | Cache hit-rate metric | ✅ | `scanner_cache_hits_total`, `scanner_cache_misses_total`, `scanner_cache_size`, latency histogram |

### ✅ EP-04 — Basic Audit

| # | Prompt | Status | What was done |
|---|---|:--:|---|
| 13 | PostgreSQL audit table | ✅ | `audit_events` (BIGSERIAL, request_id, tenant_id, prompt_hash, prompt_text_redacted, verdict, rules_matched JSONB, policy_version, latency_ms), Alembic migrations, indexes |
| 14 | PII redaction in audit | ✅ | `core/redactor.py` — replaces SSN/email/AWS-key with `<SSN:HASH8>`, logs keep only hash + position |

### ✅ EP-05 — Hardcoded PDP

| # | Prompt | Status | What was done |
|---|---|:--:|---|
| 15 | Hardcoded PDP | ✅ | `core/pdp.py` — `block_on_severity: [high, critical]`, `log_only` → ALLOW+log |
| 16 | `X-Scanner-*` headers | ✅ | `Verdict`, `Reason`, `Latency-Ms`, `Request-Id`, `Cache` — via middleware |

### ✅ EP-06 — MVP Deployment

| # | Prompt | Status | What was done |
|---|---|:--:|---|
| 17 | docker-compose.yml | ✅ | postgres, redis, scanner, ui, prometheus, grafana (optional), healthchecks |
| 18 | Dockerfile for scanner | ✅ | Multi-stage, non-root `scanner:scanner`, `/health` healthcheck, image < 200 MB |
| 19 | `/metrics` Prometheus endpoint | ✅ | `scanner_requests_total`, `request_duration_seconds`, `rules_matched_total`, `uptime_seconds`, no auth |

### ✅ EP-43 — Web UI Dashboard (Next.js + shadcn/ui)

| # | Prompt | Status | What was done |
|---|---|:--:|---|
| 20 | Next.js app + layout | ✅ | App Router, Tailwind 4, shadcn/ui, TanStack Query, next-themes, protected routes |
| 21 | Home page: live metrics | ✅ | 4 KPI cards (refresh 5s), line + bar charts, recent activity table (refresh 10s) |
| 22 | Audit Log with filters | ✅ | Filters (verdict, date range, hash), 50/page pagination, expandable rows, CSV export |
| 23 | Rules Editor (YAML) | ✅ | CodeMirror editor, live schema validation, "Test" modal, reload-from-disk |
| 24 | Test page (scan → verdict) | ✅ | Textarea + Ctrl+Enter, verdict badge, match highlighting in text |
| 25 | Cache Management | ✅ | Stats, last 50 entries, flush all / delete entry, search by hash |
| 26 | Config page | ✅ | LLM Provider, Auth (generate key), Cache TTL/size, Scanner (max length, rate limit) |
| 27 | Authentication for UI | ✅ | `/login` → JWT 24h, middleware-protected routes, logout |

---

## 🧪 ALPHA Phase

> **Scope:** EP-07..15 + EP-35 (Ingress), EP-37.3 (toxicity), EP-40.x (security), EP-41.6 (OpenAPI).
> Source: [analytics/ALPHA-PROMPTS.md](./analytics/ALPHA-PROMPTS.md).
> Orchestration: [`docker-compose.alpha.yml`](./docker-compose.alpha.yml). Per-module docs in [`docs/`](./docs).

### 🌊 EP-07 — Streaming Inspector

| # | Prompt | Implementation |
|---|---|---|
| 1 | SSE streaming from LLM | `api/v2/openai_stream.py` — `POST /v2/chat/completions` (stream:true), OpenAI SSE format, `[DONE]` terminator → [docs/api-v2.md](./docs/api-v2.md) |
| 2 | Chunked inspection of N=8 tokens | `core/streaming/inspector.py` — buffering, regex + lightweight ML per chunk |
| 3 | Terminate stream on BLOCK | `core/streaming/block_handler.py` — cancel LLM stream, `scanner_blocked` event, sync audit |
| 4 | Regex check on chunks | `core/detectors/streaming_rules.py` — cross-boundary matching via pre-buffer (last 20 chars) |
| 5 | Lightweight ML toxicity classifier | `core/detectors/toxicity.py` — int8 ONNX, < 2 ms per chunk, threshold 0.7 → BLOCK |
| 6 | Async post-factum deep analysis | `core/streaming/post_factum.py` — embedding + vector search + ML in background, WebSocket notify UI, 5s timeout |

### 🧠 EP-08 — Embedding Service

| # | Prompt | Implementation |
|---|---|---|
| 7 | Triton Inference Server + multilingual-e5 | `ml-models/embeddings/` (config.pbtxt), gRPC client `app/ml/triton_client.py`, dynamic batching (8/16/32) |
| 8 | ONNX int8 for CPU fallback | `app/ml/export_onnx_int8.py` + `validate_onnx.py` — cosine similarity > 0.95 vs FP32 → [docs/ml-model-export.md](./docs/ml-model-export.md) |
| 9 | Embedding cache in Redis | `core/cache_embedding.py` — SHA256(text) → 4096 bytes FP32, TTL 24h, LRU 1M entries |
| 10 | Batching | `app/ml/batcher.py` — `EmbeddingBatcher`, flush at 32 requests or 5 ms, futures, metrics, graceful drain |

### 🗄️ EP-09 — Vector DB (Qdrant)

| # | Prompt | Implementation |
|---|---|---|
| 11 | Qdrant deployment | `docker-compose.alpha.yml` — 3-node cluster (RF=2) / single-node for dev |
| 12 | Collection with 10K attack vectors | `app/ml/seed_qdrant.py` — AdvBench + JailbreakBench, payload `{type, severity, source, text_hash}` (no raw text) |
| 13 | Search endpoint with HNSW | `core/detectors/vector_search.py` — top-K=10, p99 < 30 ms |
| 14 | Scalar int8 quantization | `app/db/qdrant_setup.py` — quantile 0.99, `always_ram: true`, recall > 95% |
| 15 | Backup/restore snapshots | `scripts/qdrant_backup.sh` + `qdrant_restore.sh` — every 6h to S3/MinIO, retention 7 → [docs/qdrant-backup.md](./docs/qdrant-backup.md) |

### 🤖 EP-10 — ML Classifier

| # | Prompt | Implementation |
|---|---|---|
| 16 | Fine-tune DeBERTa-v3-small | `app/ml/train_classifier.py` — 4 classes (benign / prompt_injection / jailbreak / suspicious), AdamW lr=2e-5, fp16, early stopping, F1 macro > 0.85 → [docs/ml-training.md](./docs/ml-training.md) |
| 17 | ONNX int8 export | `app/ml/export_classifier_onnx.py` + `validate_onnx_classifier.py` — F1 degradation < 2% vs FP32, `model_int8.onnx` (~110 MB) |
| 18 | Inference service | `core/detectors/ml_classifier.py` — `PromptClassifier`, onnxruntime CPU, < 15 ms (batch=1) / < 5 ms (batch=32) |
| 19 | SHAP explainability | `core/detectors/classifier_explainability.py` — top-5 tokens with SHAP values, cached per (model_version, text_hash) |
| 20 | Hold-out test set + F1 | `app/ml/evaluate.py` — F1 macro/per-class, confusion matrix, ROC AUC, latency p50/p99, CI regression gate |

### ⚡ EP-11 — Two-tier Pipeline

| # | Prompt | Implementation |
|---|---|---|
| 21 | Fast path | `core/pipeline/fast_path.py` — cache → rules → suspicion tagger, < 1 ms |
| 22 | Suspicion tagger | `core/pipeline/suspicion_tagger.py` — length > 200 tokens, base64 > 200 chars, "ignore previous instructions" |
| 23 | Slow path | `core/pipeline/slow_path.py` — embed → vector search → ML classifier → aggregate, p99 < 50 ms, circuit breakers |
| 24 | Fast/slow ratio metric | `scanner_path_distribution{path="fast"|"slow"|"cache_hit"}` counter + Grafana panel |

### 📜 EP-12 — OPA Policy Decision Point

| # | Prompt | Implementation |
|---|---|---|
| 25 | OPA deployment | `docker-compose.alpha.yml` — OPA 0.68 + bundle-service (nginx), 30s polling → [docs/opa-bundles.md](./docs/opa-bundles.md) |
| 26 | Rego policies | [`policies/`](./policies) — `global/default_actions.rego`, `owasp_top10.rego`, `tenants/default.rego` (deny on prompt_injection confidence > 0.85) |
| 27 | Git repo for policies + signed commits | CODEOWNERS `@secops-team`, pre-commit `opa check`, CI `opa test` + `opa fmt --fail` |
| 28 | Policy versions + bundle deployment | `policies/manifest.json` (sha256, git_commit), `scripts/build_opa_bundle.sh`, cosign-signed, atomic switch |
| 29 | Audit log: policy_version | `ALTER TABLE audit_events ADD COLUMN policy_version` — every decision carries the policy version |

### 🗝️ EP-13 — PII Redaction (Vault + Presidio)

| # | Prompt | Implementation |
|---|---|---|
| 30 | Vault deployment | `docker-compose.alpha.yml` + [docs/vault-setup.md](./docs/vault-setup.md) — 5-min TTL, auto-cleanup |
| 31 | Vault client | `core/redaction/vault_client.py` — PII tokenization → deterministic token |
| 32 | Presidio NER | `core/redaction/presidio_ner.py` — named-entity recognition for non-regex PII |
| 33 | Redaction layer in logs | Structured JSON logs with PII tokenization → token before log |

### 🔌 EP-14 — Circuit Breaker

| # | Prompt | Implementation |
|---|---|---|
| 34 | Per-upstream circuit breaker | `core/circuit_breaker.py` — vector_db / ml_classifier / vault, modes fail-closed / fail-open / degrade, Prometheus metrics |

### 📈 EP-15 — Observability

| # | Prompt | Implementation |
|---|---|---|
| 35 | OTel setup | `core/observability.py` — W3C traceparent, OTLP exporter → [docs/grafana-dashboards.md](./docs/grafana-dashboards.md) |
| 36 | Jaeger traces | `observability/otel-collector.yml` — distributed traces for scan / proxy / slow_path |
| 37 | Loki logs | Structured JSON logs → Loki, correlation by trace_id |
| 38 | Grafana dashboards | `observability/grafana/dashboards/` — RPS, latency, cache, rules, path distribution |
| 39 | Alerting | `docs/alerting-setup.md` + Alertmanager — high block rate, cache miss spike, circuit open |

### 🌐 EP-35 — Ingress (Envoy)

| # | Prompt | Implementation |
|---|---|---|
| 40 | Envoy ingress | [docs/envoy-setup.md](./docs/envoy-setup.md) — TLS termination, rate limiting, trace context propagation |

### 🛡️ EP-37.3 / EP-40.x / EP-41.6 — Security & API

| Epic | Implementation |
|---|---|
| EP-37.3 Toxicity | `core/detectors/toxicity.py` — generation analyzer, streaming chunk classifier |
| EP-40.1/40.2 Security | PII redaction in logs, API-key auth on all endpoints except `/health` and `/metrics` |
| EP-40.7/40.8 Hardening | Non-root containers, read-only rootfs, K8s network policies |
| EP-41.6 OpenAPI | [docs/openapi.yaml](./docs/openapi.yaml) + [docs/api-v2.md](./docs/api-v2.md) — v1 and v2 endpoints |

---

## 🔌 API

### Main endpoints

| Method | Path | Auth | Description |
|:--:|---|:--:|---|
| `POST` | `/scan` | ✅ | Scan a prompt → `{verdict, reason, latency_ms, request_id}` |
| `POST` | `/v1/chat/completions` | ✅ | OpenAI-compatible proxy (non-streaming) |
| `POST` | `/v2/chat/completions` | ✅ | OpenAI-compatible proxy (**SSE streaming**, ALPHA) |
| `GET` | `/health` | ❌ | Dependency status (Postgres/Redis) |
| `GET` | `/metrics` | ❌ | Prometheus metrics |
| `GET` | `/api/v1/metrics/summary` | ✅ | KPIs for the dashboard |
| `GET` | `/api/v1/audit` | ✅ | Audit log with filters + pagination |
| `GET/POST` | `/api/v1/rules/{id}` | ✅ | Rule CRUD (YAML) |
| `POST` | `/api/v1/rules/test` | ✅ | Test a rule against text |
| `GET` | `/api/v1/cache/stats` | ✅ | Decision cache stats |
| `GET/POST` | `/api/v1/config` | ✅ | System configuration |
| `POST` | `/api/v1/auth/login` | ❌ | Login by API key → JWT 24h |

<details>
<summary><b>📖 Request examples</b></summary>

```bash
# Scan a prompt
curl -X POST "http://localhost:8000/scan" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -d '{"prompt": "What is the weather?", "tenant_id": "default"}'

# OpenAI-compatible API (drop-in replacement)
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Hello"}]
  }'

# Streaming (ALPHA)
curl -N -X POST "http://localhost:8000/v2/chat/completions" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-3.5-turbo","stream":true,"messages":[{"role":"user","content":"..."}]}'
```

```python
# Python SDK
from llm_security_scanner import ScannerClient

client = ScannerClient(url="http://localhost:8000", api_key="...")
result = client.scan("What is the weather?")
print(result.verdict, result.reason, result.latency_ms)

# Async
import asyncio
from llm_security_scanner import AsyncScannerClient

async def main():
    async with AsyncScannerClient(url="http://localhost:8000", api_key="...") as c:
        r = await c.scan("Hello world")
        print(r.verdict)
asyncio.run(main())
```

</details>

Full OpenAPI spec: [`docs/openapi.yaml`](./docs/openapi.yaml) and Swagger UI at `http://localhost:8000/docs`.

---

## ⚙️ Configuration

<details>
<summary><b>🔧 Environment variables (.env)</b></summary>

```env
# ── Database ───────────────────────────────────────────
POSTGRES_DB=scanner
POSTGRES_USER=scanner
POSTGRES_PASSWORD=your_secure_password
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

# ── Redis ──────────────────────────────────────────────
REDIS_URL=redis://localhost:6379/0
REDIS_PASSWORD=

# ── LLM Provider ──────────────────────────────────────
LLM_PROVIDER_URL=https://api.openai.com/v1/chat/completions
LLM_MODEL=gpt-3.5-turbo
LLM_API_KEY=your_llm_api_key_here
LLM_TIMEOUT_SECONDS=30

# ── Security ──────────────────────────────────────────
API_KEY_DEFAULT=your_api_key_here
JWT_SECRET=your_jwt_secret_here
JWT_TTL_SECONDS=86400

# ── Cache ─────────────────────────────────────────────
CACHE_TTL_SECONDS=300
CACHE_MAX_SIZE=10000

# ── Scanner / PDP ─────────────────────────────────────
PDP_DEFAULT_ACTION=allow
PDP_BLOCK_ON_SEVERITY=high,critical
MAX_PROMPT_LENGTH=10000
RATE_LIMIT_RPS=100

# ── Audit ─────────────────────────────────────────────
AUDIT_ENABLED=true
REDACT_AUDIT_PROMPT=true
POLICY_VERSION=prod-1.0

# ── Observability ────────────────────────────────────
LOG_LEVEL=INFO
LOG_FORMAT=json
ENABLE_METRICS=true
```

</details>

Production configuration (SSL, Nginx, K8s, network policies, backups): [`PRODUCTION.md`](./PRODUCTION.md).

---

## 🧪 Testing

```bash
# Backend — pytest with a 90% coverage gate
cd backend
python -m pytest --cov=app --cov-report=term-missing --cov-fail-under=90

# Individual ML modules (without the global coverage gate)
uv run pytest tests/ml/test_batcher.py tests/ml/test_classifier_onnx.py -v --no-cov

# Integration (requires Docker: PostgreSQL + Redis)
uv run pytest tests/integration/ -v

# Frontend — vitest + Playwright E2E
cd ../frontend
npm run test:e2e
npm run test:e2e:ui
```

| Level | Coverage |
|---|---|
| Unit (rules, cache, pdp, redactor) | 90%+ |
| Integration (PostgreSQL + Redis + Qdrant) | critical paths |
| ML (train/export/validate, batcher, triton) | smoke + integration markers |
| E2E (Playwright) | full user journey |

---

## 📊 Monitoring

Metrics at `http://localhost:8000/metrics` (no auth, for Prometheus scrape):

```
scanner_requests_total{verdict="allow", tenant_id="default"} 1234
scanner_request_duration_seconds_bucket{le="0.01"} 456
scanner_cache_hits_total{type="decision"} 789
scanner_cache_misses_total{type="decision"} 234
scanner_rules_matched_total{rule_id="pii_ssn_us"} 12
scanner_path_distribution{path="fast"} 1100
scanner_path_distribution{path="slow"} 134
scanner_embedding_batcher_batches_total 45
```

Grafana dashboard: `http://localhost:3001` (admin / admin). Ready-made dashboards in [`observability/grafana/dashboards/`](./observability/grafana/dashboards).

---

## 📁 Project Structure

```
OryaSecurity/
├── backend/                      # FastAPI scanner
│   ├── app/
│   │   ├── api/                  # v1 + v2 endpoints (scan, openai_compat, stream, metrics, charts)
│   │   ├── core/
│   │   │   ├── pipeline/         # two-tier: fast_path, slow_path, suspicion_tagger, orchestrator
│   │   │   ├── detectors/        # rules, embedding, vector_search, ml_classifier, toxicity
│   │   │   ├── streaming/        # inspector, block_handler, post_factum, sse
│   │   │   ├── pdp/              # opa_client + rego bundles
│   │   │   ├── redaction/        # vault_client, presidio_ner
│   │   │   ├── rules/            # pii, secrets, loader
│   │   │   ├── cache.py, cache_embedding.py
│   │   │   ├── circuit_breaker.py, observability.py, metrics.py
│   │   │   ├── proxy.py, auth.py, pdp.py, redactor.py, headers.py
│   │   ├── ml/                   # train_classifier, export_onnx, batcher, triton_client, metrics
│   │   ├── models/               # Pydantic schemas (scan, openai, audit)
│   │   ├── db/                   # postgres, redis, qdrant clients
│   │   ├── main.py, config.py
│   ├── rules/                    # YAML: pii.yaml, secrets.yaml
│   ├── sdk/python/               # llm-security-scanner SDK
│   ├── alembic/                  # DB migrations
│   ├── tests/                    # api, core, ml, integration, policies
│   ├── ml-models/classifier/v1/  # train/val/test.jsonl + ONNX artifacts (.gitignored)
│   ├── pyproject.toml, Dockerfile
├── frontend/                     # Next.js 15 + shadcn/ui dashboard
│   ├── app/(dashboard)/          # audit, rules, test, cache, config pages
│   ├── components/               # ui (shadcn), metrics, audit, rules, test, cache, config
│   ├── lib/                      # api.ts, auth.ts, utils.ts
│   ├── Dockerfile
├── ml-models/                    # versioned artifacts: embeddings/, classifier/
├── policies/                     # Rego: global/, tenants/, manifest.json
├── observability/                # otel-collector, prometheus-rules, grafana/dashboards
├── docs/                         # api-v2, ml-training, opa-bundles, vault-setup, envoy-setup, ...
├── analytics/                    # MVP-PROMPTS, ALPHA-PROMPTS, ROADMAP, ARCHITECT, ADR, BACKLOG
├── scripts/                      # build_opa_bundle.sh, qdrant_backup.sh, qdrant_restore.sh
├── certs/                        # TLS certs (dev)
├── docker-compose.yml            # MVP stack
├── docker-compose.alpha.yml      # full ALPHA stack (Triton, Qdrant, OPA, Vault, OTel)
├── prometheus.yml
├── PRODUCTION.md, IMPLEMENTATION_STATUS.md
├── .env.example
└── README.md
```

---

## 🗺️ Roadmap

| Phase | Status | Contents |
|---|:--:|---|
| **MVP** (EP-01..06, EP-43) | 🟡 ~85% | Reverse proxy, rule engine, cache, audit, PDP, Web UI |
| **ALPHA** (EP-07..15, EP-35/37.3/40/41.6) | 🟢 modular | Streaming, ML, Qdrant, OPA, Vault, circuit breaker, observability |
| **BETA** | ⏳ planned | Multi-tenancy, HA, LLM-as-judge (async post-factum), advanced policies |
| **GA** | ⏳ planned | Production hardening, performance validation (100 RPS, p99 < 10 ms), compliance |

In detail: [`analytics/ROADMAP.md`](./analytics/ROADMAP.md), [`analytics/BACKLOG.md`](./analytics/BACKLOG.md),
current progress — [`IMPLEMENTATION_STATUS.md`](./IMPLEMENTATION_STATUS.md).

### Known technical debt

- Phone number and Russian passport detection — regex needs refinement (false positives)
- Test coverage — currently below the 90% target, work in progress
- ML classifier — smoke training done, full training to reach F1 > 0.85 in progress

---

## 📚 Documentation

| Document | Description |
|---|---|
| [docs/api-v2.md](./docs/api-v2.md) | v1 + v2 API reference (SSE streaming, OPA decisions) |
| [docs/openapi.yaml](./docs/openapi.yaml) | OpenAPI specification |
| [docs/ml-training.md](./docs/ml-training.md) | Fine-tuning the DeBERTa classifier |
| [docs/ml-model-export.md](./docs/ml-model-export.md) | ONNX int8 export + validation |
| [docs/opa-bundles.md](./docs/opa-bundles.md) | Rego policies, bundle build & distribution |
| [docs/vault-setup.md](./docs/vault-setup.md) | HashiCorp Vault for PII tokenization |
| [docs/qdrant-backup.md](./docs/qdrant-backup.md) | Vector DB snapshots & restore |
| [docs/envoy-setup.md](./docs/envoy-setup.md) | Envoy ingress configuration |
| [docs/grafana-dashboards.md](./docs/grafana-dashboards.md) | Observability dashboards |
| [docs/alerting-setup.md](./docs/alerting-setup.md) | Alertmanager rules |
| [PRODUCTION.md](./PRODUCTION.md) | Production deployment (Nginx, K8s, backups, hardening) |
| [analytics/ARCHITECT.md](./analytics/ARCHITECT.md) | Detailed architecture |
| [analytics/ADR.md](./analytics/ADR.md) | Architecture Decision Records |

---

## 🤝 Contributing

Contributions are welcome! Please read [**CONTRIBUTING.md**](./CONTRIBUTING.md) before starting work.

In short:

- Fork the repo, create a `feature/<name>` or `fix/<name>` branch
- Every change ships with tests, ≥ 90% coverage for new code
- PII in logs is always masked (regex before log)
- Rego policies live in a separate Git repo, signed commits, 2-eyes review
- A PR needs 2 approvers and green CI (pytest + opa test + eslint)

---

## 📄 License

Licensed under the **Apache License 2.0** — see [`LICENSE`](./LICENSE).

```
Copyright 2026 OryaSecurity Contributors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

---

## 🆘 Support

- 🐛 **Bug / feature request** — [open a GitHub issue](https://github.com/reider85/OryaSecurity/issues)
- 📖 **FAQ / troubleshooting** — [analytics/BACKLOG.md](./analytics/BACKLOG.md), [IMPLEMENTATION_STATUS.md](./IMPLEMENTATION_STATUS.md)
- 💬 **Contact** — via Issues or Discussions

---

**OryaSecurity** — protect your AI applications from data leaks and prompt-injection attacks.

Made with 🛡️ by the OryaSecurity contributors

