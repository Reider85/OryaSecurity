# MVP-PROMPTS.md — Промпты для фазы MVP

| | |
|---|---|
| **Версия** | 1.0 |
| **Дата** | 2026-09-27 |
| **Источник** | [BACKLOG.md](BACKLOG.md), [ROADMAP.md](ROADMAP.md), [ARCHITECT.md](ARCHITECT.md) |
| **Назначение** | Готовые промпты для AI-ассистентов (Cursor / Claude Code / Copilot) под каждую задачу MVP |
| **Покрытие** | EP-01..06 + EP-43 (Web UI Dashboard) |
| **Язык** | Русский |

---

## 0. Как пользоваться этим файлом

1. Откройте файл в IDE с AI-ассистентом (Cursor / Claude Code / Windsurf / Copilot Chat).
2. Скопируйте **System Prompt** (раздел 1) в системные настройки ассистента один раз на сессию.
3. Для каждой задачи из BACKLOG копируйте соответствующий промпт из раздела 2 в чат ассистента.
4. После завершения — проверьте результат против Acceptance Criteria в BACKLOG.
5. Все промпты содержат: контекст, цель, технические требования, файлы для создания/изменения, проверочные шаги.

---

## 1. System Prompt (общий для всей фазы MVP)

```
Ты — Senior Python/TypeScript инженер, работающий над проектом LLM Security Scanner (MVP фаза).

КОНТЕКСТ ПРОЕКТА:
LLM Security Scanner — инлайн-сервис безопасности между пользователем и LLM-провайдером.
Проверяет входящие промпты на PII/secrets/prompt-injection, исходящие генерации — на утечки.
Принимает решения: allow / block. Возвращает отчёт SecOps.

ТЕХНОЛОГИЧЕСКИЙ СТЕК MVP:
- Backend: Python 3.12 + FastAPI + asyncio + uvicorn
- Database: PostgreSQL 16 (audit) + Redis 7 (cache)
- ML: нет на MVP (появится в ALPHA)
- Frontend: Next.js 15 + TypeScript + Tailwind CSS 4 + shadcn/ui
- Infra: Docker Compose, single-node deployment
- Observability: Prometheus metrics endpoint + basic Grafana dashboard
- Tests: pytest (backend), vitest (frontend), 90% coverage required

АРХИТЕКТУРНЫЕ ПРИНЦИПЫ:
- Reverse Proxy pattern: сканер стоит между AI App и LLM Provider
- Stateless scanner, external state (Redis/Postgres)
- Decision Cache: SHA256(prompt) → verdict, TTL 5min
- Hardcoded PDP: regex match → BLOCK, otherwise → ALLOW
- Web UI: dashboard для SecOps, не заменяет Grafana
- PII redaction в audit log (basic regex)

СТРУКТУРА РЕПОЗИТОРИЯ:
llm-security-scanner/
├── backend/                 # FastAPI scanner
│   ├── app/
│   │   ├── main.py          # FastAPI app
│   │   ├── api/             # endpoints
│   │   ├── core/            # scanner pipeline, rules, cache
│   │   ├── models/          # Pydantic schemas
│   │   ├── db/              # Postgres, Redis clients
│   │   └── config.py        # settings
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/                # Next.js UI
│   ├── app/                 # App Router
│   │   ├── (dashboard)/      # protected routes
│   │   │   ├── page.tsx      # home: metrics
│   │   │   ├── audit/page.tsx
│   │   │   ├── rules/page.tsx
│   │   │   ├── test/page.tsx
│   │   │   ├── cache/page.tsx
│   │   │   └── config/page.tsx
│   │   └── layout.tsx
│   ├── components/ui/       # shadcn components
│   ├── lib/
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml
├── README.md
└── docs/

ПРАВИЛА:
- Каждый PR должен иметь 2 approvers
- 90% coverage для нового кода
- Все PII в логах должны быть замаскированы (regex before log)
- API-key auth на всех endpoints, кроме /health и /metrics
- Используй type hints + Pydantic для всех API contracts
- shadcn/ui компоненты для UI, не пиши кастомный CSS
- Все ошибки логируй в structured JSON (loguru или structlog)
- Не используй синхронные I/O в hot path

При ответе:
- Всегда показывай полные файлы, не псевдокод
- Объясняй архитектурные решения в комментариях
- Предлагай тесты вместе с реализацией
- Указывай, какие ADR затронуты
```

---

## 2. Промпты под каждый Epic

### EP-01: Reverse Proxy Core

#### Промпт 1 — HTTP endpoint `/scan`

```
Реализуй endpoint POST /scan в backend/app/api/scan.py.

Требования:
- Принимает JSON body: {prompt: str, tenant_id?: str, metadata?: dict}
- Возвращает: {verdict: "allow"|"block", reason: str, latency_ms: float, request_id: str}
- request_id — UUID v4 для трассировки
- Добавляет header X-Scanner-Verdict в ответ
- Логирует каждый запрос в structured JSON (ts, request_id, tenant_id, prompt_hash, verdict, latency_ms)
- prompt_hash = SHA256(prompt), НЕ сам prompt (privacy)
- Поддержи API-key auth через header Authorization: Bearer <key>
- На ошибки возвращай 422 (validation), 401 (auth), 500 (internal)

Файлы:
- backend/app/api/scan.py — endpoint
- backend/app/models/scan.py — Pydantic schemas
- backend/app/core/auth.py — API-key verification
- backend/tests/api/test_scan.py — unit tests

Acceptance criteria (BACKLOG story 1.1):
- Endpoint работает на /scan
- Возвращает verdict + reason + latency_ms + request_id
- 401 без API-key
- Логирует в structured JSON
- 90% test coverage

Связанные ADR: ADR-0001 (Reverse Proxy), ADR-0004 partial (PDP)
```

#### Промпт 2 — Прокси с инспекцией обоих направлений

```
Реализуй прокси-логику в backend/app/core/proxy.py.

Сценарий:
1. AI App отправляет POST /v1/chat/completions на scanner
2. Scanner проверяет промпт (на PII/secrets)
3. Если BLOCK — возвращает 403 с причиной
4. Если ALLOW — форвардит запрос к LLM Provider (OpenAI-совместимый)
5. LLM отвечает — scanner проверяет генерацию (на PII/secrets)
6. Если BLOCK в генерации — возвращает partial response + warning
7. Иначе — форвардит ответ AI App

Требования:
- Поддержи OpenAI Chat Completions API format (запрос/ответ)
- LLM_PROVIDER_URL из env (default: http://localhost:8001)
- Timeout на LLM: 30 секунд
- При timeout LLM — возвращай 502 с понятной ошибкой
- Streaming: на MVP — НЕ поддерживаем (появится в ALPHA), возвращай полную генерацию
- Логируй оба направления (prompt inspection, generation inspection)

Файлы:
- backend/app/core/proxy.py — прокси-логика
- backend/app/api/openai_compat.py — OpenAI-совместимый endpoint
- backend/tests/core/test_proxy.py — unit tests
- backend/tests/fixtures/sample_prompts.json — тестовые промпты

Связанные ADR: ADR-0001 (Reverse Proxy)
```

#### Промпт 3 — `/health` endpoint

```
Реализуй GET /health endpoint в backend/app/api/health.py.

Возвращает:
{
  "status": "ok" | "degraded" | "down",
  "version": "0.1.0",
  "uptime_seconds": 12345,
  "dependencies": {
    "postgres": "ok" | "fail",
    "redis": "ok" | "fail"
  }
}

- Status "ok" если все deps ok
- "degraded" если хотя бы один dep fail
- "down" если оба fail
- HTTP 200 для ok/degraded, 503 для down
- Не требует auth (public)

Файлы:
- backend/app/api/health.py
- backend/app/core/deps.py — check_postgres(), check_redis()
- backend/tests/api/test_health.py
```

#### Промпт 4 — OpenAI-совместимый API

```
Реализуй endpoint POST /v1/chat/completions в backend/app/api/openai_compat.py.

Это drop-in замена для OpenAI API — клиентское приложение просто меняет base_url.

Требования:
- Принимает OpenAI Chat Completions request (model, messages, temperature, etc.)
- Передаёт через scanner pipeline (proxy.py)
- Возвращает OpenAI-format response
- Поддерживает поле "model" — игнорируем, передаём всем в один configured LLM_PROVIDER
- API-key auth (header Authorization: Bearer)
- Rate-limit: 100 RPS per API-key (return 429 если превышен)
- Логирует request_id в response headers

Файлы:
- backend/app/api/openai_compat.py
- backend/app/models/openai.py — OpenAI schemas
- backend/app/core/rate_limit.py — simple in-memory rate limiter
- backend/tests/api/test_openai_compat.py

Связанные ADR: ADR-0001
```

#### Промпт 5 — Python SDK

```
Создай Python SDK в backend/sdk/python/.

Требования:
- Имя пакета: llm-security-scanner
- Класс ScannerClient с методами:
  - scan(prompt: str, tenant_id: str = None) -> ScanResult
  - proxy_chat(messages: list[dict], model: str = None, **kwargs) -> ChatResponse
- Auth: API-key (передаётся в конструкторе)
- HTTP client: httpx (async) + requests (sync)
- Type hints + Pydantic schemas
- README с примерами
- Tests с pytest + respx

Файлы:
- backend/sdk/python/llm_security_scanner/__init__.py
- backend/sdk/python/llm_security_scanner/client.py
- backend/sdk/python/llm_security_scanner/models.py
- backend/sdk/python/tests/test_client.py
- backend/sdk/python/README.md
- backend/sdk/python/pyproject.toml

Пример использования:
```python
from llm_security_scanner import ScannerClient
client = ScannerClient(url="http://localhost:8000", api_key="...")
result = client.scan("What is the weather?")
print(result.verdict)  # "allow"
```
```

#### Промпт 6 — API-key auth

```
Реализуй API-key auth в backend/app/core/auth.py.

Требования:
- API-keys хранятся в PostgreSQL таблице api_keys (id, key_hash, tenant_id, created_at, active)
- key_hash = bcrypt(key)
- Header: Authorization: Bearer <api_key>
- При request — верифицируем key, добавляем tenant_id в request.state
- Endpoints: /health, /metrics — public
- Все остальные — auth required
- 401 Unauthorized если key invalid
- 403 Forbidden если key inactive

Файлы:
- backend/app/core/auth.py — verification logic
- backend/app/db/migrations/001_api_keys.sql — table
- backend/app/api/admin_apikey.py — admin endpoints для управления keys
- backend/tests/core/test_auth.py
- backend/tests/api/test_admin_apikey.py
```

---

### EP-02: Rule Engine

#### Промпт 7 — Regex для SSN, паспорта RU, email

```
Реализуй regex-правила в backend/app/core/rules/pii.py.

Требования:
- 3 типа PII:
  1. US SSN: \b\d{3}-\d{2}-\d{4}\b
  2. RU Passport: \b\d{4}\s?\d{6}\b
  3. Email: RFC 5322 simplified
- Каждое правило = класс Rule с полями: id, name, pattern, severity, action
- YAML-конфиг rules/pii.yaml со списком правил
- Возвращает: list[RuleMatch] где RuleMatch = {rule_id, value, position, severity}
- Не возвращает сам PII в логи (только hash + position)

Файлы:
- backend/app/core/rules/pii.py — реализации правил
- backend/app/core/rules/base.py — Rule base class, RuleMatch dataclass
- backend/rules/pii.yaml — конфиг
- backend/tests/core/rules/test_pii.py — unit tests со всеми вариантами

Acceptance criteria (BACKLOG 2.1):
- Ловит все тестовые примеры из tests/fixtures/pii_samples.json
- Не даёт false positives на обычные числа
- YAML-формат для редактирования
- 90% coverage
```

#### Промпт 8 — Regex для AWS keys, JWT, credit cards

```
Реализуй regex-правила в backend/app/core/rules/secrets.py.

Требования:
- 3 типа secrets:
  1. AWS Access Key: AKIA[0-9A-Z]{16}
  2. JWT: eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+
  3. Credit card (Visa/MC/Amex): with Luhn checksum validation
- Luhn validation в отдельной функции validate_luhn(number_str) -> bool
- Те же поля: id, name, pattern, severity, action
- YAML rules/secrets.yaml

Файлы:
- backend/app/core/rules/secrets.py
- backend/rules/secrets.yaml
- backend/tests/core/rules/test_secrets.py

Acceptance criteria (BACKLOG 2.2):
- AWS keys: ловит валидные и пропускает random строки
- JWT: ловит полные трёхсегментные
- Credit cards: Luhn валидация, пропускает невалидные номера
- 90% coverage
```

#### Промпт 9 — YAML-формат правил

```
Реализуй загрузку правил из YAML в backend/app/core/rules/loader.py.

Требования:
- Загрузка всех .yaml файлов из директории rules/
- Валидация схемы (jsonschema)
- Hot-reload: при изменении файлов правила обновляются без рестарта
- Versioning: каждое правило имеет version
- Returns: RuleSet с методами match(text) -> list[RuleMatch]

YAML схема:
rules:
  - id: pii_ssn_us
    name: "US Social Security Number"
    type: regex
    pattern: '\b\d{3}-\d{2}-\d{4}\b'
    severity: high
    action: block
    version: "1.0.0"

Файлы:
- backend/app/core/rules/loader.py
- backend/app/core/rules/schema.json — jsonschema
- backend/rules/README.md — документация
- backend/tests/core/rules/test_loader.py
```

#### Промпт 10 — Unit-тесты для правил

```
Создай comprehensive test suite для всех regex-правил в backend/tests/core/rules/.

Требования:
- Для каждого правила:
  - 5+ positive examples (ловит)
  - 5+ negative examples (пропускает)
  - Edge cases (пустая строка, очень длинная, unicode)
- Test fixtures в JSON формате
- Parametrized tests (pytest.mark.parametrize)
- Coverage report в CI

Файлы:
- backend/tests/core/rules/test_pii.py
- backend/tests/core/rules/test_secrets.py
- backend/tests/core/rules/test_loader.py
- backend/tests/fixtures/pii_positive.json
- backend/tests/fixtures/pii_negative.json
- backend/tests/fixtures/secrets_positive.json
- backend/tests/fixtures/secrets_negative.json
```

---

### EP-03: Decision Cache

#### Промпт 11 — Redis decision cache

```
Реализуй decision cache в backend/app/core/cache.py.

Требования:
- Key: SHA256(prompt), hex string
- Value: JSON {verdict, reason, rules_matched, ts}
- TTL: 5 минут (настраивается в config)
- Redis client: redis-py async
- При cache hit — добавляй header X-Scanner-Cache: HIT
- Cache miss — X-Scanner-Cache: MISS
- Errors (Redis down) — fallback to scan (log warning)

Файлы:
- backend/app/core/cache.py — DecisionCache class
- backend/app/db/redis_client.py — Redis connection
- backend/tests/core/test_cache.py — unit tests with fakeredis
- backend/tests/integration/test_cache_redis.py — integration с реальным Redis
```

#### Промпт 12 — Cache hit rate метрика

```
Добавь Prometheus метрики для cache в backend/app/core/metrics.py.

Метрики:
- scanner_cache_hits_total{type="decision"}
- scanner_cache_misses_total{type="decision"}
- scanner_cache_hit_ratio (computed in Grafana)
- scanner_cache_size (gauge)
- scanner_cache_latency_seconds (histogram)

Используй prometheus_client.

Endpoint /metrics в backend/app/api/metrics.py — без auth, для Prometheus scrape.

Файлы:
- backend/app/core/metrics.py
- backend/app/api/metrics.py
- backend/tests/core/test_metrics.py
```

---

### EP-04: Basic Audit

#### Промпт 13 — PostgreSQL audit table

```
Создай PostgreSQL схему для audit log.

Таблица audit_events:
- id BIGSERIAL PRIMARY KEY
- ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
- request_id UUID NOT NULL
- tenant_id VARCHAR(64)
- prompt_hash VARCHAR(64) NOT NULL  -- SHA256, не сам prompt
- prompt_text_redacted TEXT  -- PII замаскированы
- verdict VARCHAR(16) NOT NULL  -- allow/block
- reason TEXT
- rules_matched JSONB  -- [{rule_id, position}]
- policy_version VARCHAR(16)
- latency_ms REAL
- INDEX на (tenant_id, ts DESC)
- INDEX на (prompt_hash)
- INDEX на (verdict)

Миграции через alembic.

Файлы:
- backend/app/db/models.py — SQLAlchemy model
- backend/alembic/versions/001_create_audit_events.py
- backend/app/db/session.py — async SQLAlchemy session
- backend/app/core/audit.py — write_event(), query_events()
```

#### Промпт 14 — PII redaction в audit

```
Реализуй PII redaction layer в backend/app/core/redactor.py.

Требования:
- Перед записью в audit log — заменяй все PII на плейсхолдеры:
  - SSN → <SSN_HASH_XXXX>
  - Email → <EMAIL_HASH_XXXX>
  - AWS key → <AWS_KEY_HASH_XXXX>
- HASH = первые 8 символов SHA256(значение)
- Сохраняй только redacted_text в audit
- Функция redact(text, rules) -> redacted_text

На MVP — простая regex-замена. Vault появится в ALPHA.

Файлы:
- backend/app/core/redactor.py
- backend/tests/core/test_redactor.py
```

---

### EP-05: Hardcoded PDP

#### Промпт 15 — Hardcoded PDP

```
Реализуй hardcoded Policy Decision Point в backend/app/core/pdp.py.

Логика:
- input: list[RuleMatch]
- output: Verdict(action="allow"|"block", reason=str)
- Если хотя бы одно rule с action="block" → BLOCK
- Если только action="log_only" → ALLOW (но log)
- Иначе → ALLOW

Конфиг в config.py:
PDP_CONFIG = {
  "default_action": "allow",
  "block_on_severity": ["high", "critical"],
}

Файлы:
- backend/app/core/pdp.py
- backend/app/config.py — settings
- backend/tests/core/test_pdp.py
```

#### Промпт 16 — `X-Scanner-Verdict` header

```
Добавь header X-Scanner-Verdict во все responses.

Значение: "allow" или "block"
Дополнительные headers:
- X-Scanner-Verdict: allow|block
- X-Scanner-Reason: <short reason>
- X-Scanner-Latency-Ms: <number>
- X-Scanner-Request-Id: <uuid>
- X-Scanner-Cache: HIT|MISS

Реализуй как FastAPI middleware или response decorator.

Файлы:
- backend/app/core/headers.py
- backend/tests/api/test_headers.py
```

---

### EP-06: MVP Deployment

#### Промпт 17 — docker-compose.yml

```
Создай docker-compose.yml в корне репозитория.

Services:
1. postgres:
   - image: postgres:16-alpine
   - volume: pg_data
   - env: POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
   - healthcheck

2. redis:
   - image: redis:7-alpine
   - command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
   - healthcheck

3. scanner:
   - build: ./backend
   - depends_on: postgres, redis
   - env: DATABASE_URL, REDIS_URL, LLM_PROVIDER_URL, API_KEY_DEFAULT
   - ports: 8000:8000
   - healthcheck

4. ui:
   - build: ./frontend
   - depends_on: scanner
   - ports: 3000:3000
   - env: NEXT_PUBLIC_API_URL=http://localhost:8000

5. prometheus (optional, для MVP):
   - image: prom/prometheus
   - volume: ./prometheus.yml
   - ports: 9090:9090

6. grafana (optional):
   - image: grafana/grafana
   - ports: 3001:3000

Volumes: pg_data, redis_data

Файлы:
- docker-compose.yml
- .env.example
- prometheus.yml
```

#### Промпт 18 — Dockerfile для scanner

```
Создай Dockerfile в backend/.

Multi-stage build:
1. Builder: python:3.12-slim, install uv, install deps, compile to .whl
2. Runtime: python:3.12-slim, copy .whl, install, expose 8000

Требования:
- Non-root user (scanner:scanner)
- Healthcheck: curl /health каждые 30s
- .dockerignore для __pycache__, tests, .git
- Размер образа < 200MB

Файлы:
- backend/Dockerfile
- backend/.dockerignore
- backend/entrypoint.sh — DB migrations перед стартом
```

#### Промпт 19 — `/metrics` Prometheus endpoint

```
Добавь /metrics endpoint в backend/app/api/metrics.py.

Метрики (prometheus_client):
- scanner_requests_total{verdict, tenant_id} (counter)
- scanner_request_duration_seconds (histogram, buckets: 1ms-1s)
- scanner_cache_hits_total, scanner_cache_misses_total
- scanner_rules_matched_total{rule_id} (counter)
- scanner_uptime_seconds (gauge)
- python_info (gauge)

Endpoint:
- GET /metrics
- Без auth (для Prometheus scrape)
- Content-Type: text/plain; version=0.0.4

Файлы:
- backend/app/api/metrics.py
- backend/app/core/metrics.py — initialize all metrics
- prometheus.yml — scrape config
```

---

### EP-43: Web UI Dashboard (Next.js + shadcn/ui)

#### Промпт 20 — Next.js app + layout

```
Создай Next.js приложение в frontend/.

Стек:
- Next.js 15 (App Router)
- TypeScript 5
- Tailwind CSS 4
- shadcn/ui компоненты
- TanStack Query для data fetching
- next-themes для light/dark
- Lucide icons

Структура:
frontend/
├── app/
│   ├── layout.tsx              # root layout, theme provider
│   ├── page.tsx                # redirect to /dashboard
│   ├── (auth)/login/page.tsx   # login form
│   ├── (dashboard)/
│   │   ├── layout.tsx          # sidebar + topbar
│   │   ├── page.tsx            # home: metrics
│   │   ├── audit/page.tsx
│   │   ├── rules/page.tsx
│   │   ├── test/page.tsx
│   │   ├── cache/page.tsx
│   │   └── config/page.tsx
│   └── globals.css
├── components/
│   ├── ui/                     # shadcn components (button, card, table, etc.)
│   ├── sidebar.tsx             # navigation
│   ├── topbar.tsx
│   └── theme-toggle.tsx
├── lib/
│   ├── api.ts                  # API client
│   ├── auth.ts                 # auth context
│   └── utils.ts
├── package.json
├── tsconfig.json
├── tailwind.config.ts
├── next.config.js
└── Dockerfile

Требования:
- Server components by default, client components где нужно (use client)
- Sidebar с навигацией: Dashboard, Audit Log, Rules, Test, Cache, Config
- Topbar с user info + theme toggle
- Защищённые routes через middleware (redirect to /login если не auth)
- Adaptтивная вёрстка (mobile-friendly)

Команды:
npx create-next-app@latest frontend --typescript --tailwind --app
cd frontend && npx shadcn@latest init
npx shadcn@latest add button card table input textarea badge dialog tabs

Файлы: все вышеуказанные
```

#### Промпт 21 — Главная страница с live-метриками

```
Реализуй главную страницу frontend/app/(dashboard)/page.tsx.

Содержимое:
1. 4 KPI cards (TanStack Query, обновление каждые 5с):
   - Requests/min (сегодня)
   - Block rate (%)
   - Avg latency (ms)
   - Cache hit rate (%)

2. Line chart: requests/block-rate за последние 24 часа (recharts или visx)

3. Bar chart: top-5 срабатываемых правил (за сегодня)

4. Recent activity table: последние 10 audit events (refresh каждые 10с)

API endpoints (создай если их нет):
- GET /api/v1/metrics/summary → {rps, block_rate, avg_latency, cache_hit_rate}
- GET /api/v1/audit?limit=10 → list[AuditEvent]

Компоненты shadcn/ui: Card, CardHeader, CardContent, Table, Tabs.

Файлы:
- frontend/app/(dashboard)/page.tsx
- frontend/components/metrics/kpi-card.tsx
- frontend/components/metrics/requests-chart.tsx
- frontend/components/metrics/top-rules-chart.tsx
- frontend/components/audit/recent-activity.tsx
- frontend/lib/api.ts — fetchMetricsSummary, fetchRecentAudit
- backend/app/api/v1/metrics.py — backend endpoints
- backend/tests/api/test_metrics_v1.py
```

#### Промпт 22 — Audit Log страница с фильтрами

```
Реализуй страницу frontend/app/(dashboard)/audit/page.tsx.

Содержимое:
1. Filters bar (sticky top):
   - Tenant select (если multi-tenant, на MVP — single tenant)
   - Verdict select (all/allow/block)
   - Date range picker (от и до)
   - Prompt hash search input
   - "Apply" button
   - "Reset" button
   - "Export CSV" button

2. Table с пагинацией (50 per page):
   - ts, request_id, prompt_hash, verdict, reason, rules_matched, latency_ms
   - Click row → expand → детали (full redacted_text, rule matches с позициями)
   - Sortable columns (ts, latency_ms)

3. Empty state если нет данных

API: GET /api/v1/audit?filters...&page=1&per_page=50

Используй shadcn Table, DataTable (TanStack Table), DatePicker.

Файлы:
- frontend/app/(dashboard)/audit/page.tsx
- frontend/components/audit/audit-filters.tsx
- frontend/components/audit/audit-table.tsx
- frontend/components/audit/audit-row-detail.tsx
- frontend/lib/api.ts — fetchAudit(params)
- backend/app/api/v1/audit.py — endpoint с пагинацией и фильтрами
- backend/tests/api/test_audit_v1.py
```

#### Промпт 23 — Rules Editor (YAML)

```
Реализуй страницу frontend/app/(dashboard)/rules/page.tsx.

Содержимое:
1. Список правил слева (sidebar):
   - Tree: pii/ (3 rules), secrets/ (3 rules)
   - Click → opens rule в редакторе справа

2. Editor справа:
   - YAML editor (используй Monaco Editor или CodeMirror)
   - Syntax highlighting для YAML
   - Live validation: показывает ошибки схемы
   - "Save" button → POST /api/v1/rules/{id}
   - "Test" button → открывает modal с textarea + "Run" → показывает matches

3. Top bar:
   - "Add new rule" button
   - "Reload from disk" button
   - Version display

API:
- GET /api/v1/rules → list[Rule]
- GET /api/v1/rules/{id} → Rule
- POST /api/v1/rules/{id} → updated Rule
- POST /api/v1/rules/test → {text: str} → list[RuleMatch]

Используй @uiw/react-codemirror для редактора.

Файлы:
- frontend/app/(dashboard)/rules/page.tsx
- frontend/components/rules/rules-list.tsx
- frontend/components/rules/rule-editor.tsx
- frontend/components/rules/test-modal.tsx
- frontend/lib/api.ts — fetchRules, fetchRule, saveRule, testRule
- backend/app/api/v1/rules.py
- backend/tests/api/test_rules_v1.py
```

#### Промпт 24 — Test page (отправить промпт → вердикт)

```
Реализуй страницу frontend/app/(dashboard)/test/page.tsx.

Содержимое:
1. Большой textarea (минимум 10 строк)
2. Кнопка "Scan" (с loading state)
3. Результат:
   - Verdict badge (зелёный "Allow" или красный "Block")
   - Reason text
   - Latency ms
   - Cache HIT/MISS badge
   - Request ID (с кнопкой "Copy")
   - Rules matched (список с позициями в тексте)
   - Подсветка в исходном тексте (где matched)

Hot-key: Ctrl+Enter для отправки.

API: POST /scan с {prompt: str}

Используй shadcn Textarea, Button, Badge, Card.

Файлы:
- frontend/app/(dashboard)/test/page.tsx
- frontend/components/test/scan-form.tsx
- frontend/components/test/scan-result.tsx
- frontend/components/test/text-highlighter.tsx
- frontend/lib/api.ts — scanPrompt(text)
```

#### Промпт 25 — Cache Management page

```
Реализуй страницу frontend/app/(dashboard)/cache/page.tsx.

Содержимое:
1. Stats card:
   - Total entries
   - Hit rate (24h)
   - Memory usage (bytes)
   - TTL average

2. Table с последними 50 записями:
   - prompt_hash (truncated)
   - verdict
   - ts
   - expires_at (relative: "in 4 min")

3. Actions:
   - "Flush all" button (с confirmation modal)
   - "Delete entry" per row
   - "Search by hash" input

API:
- GET /api/v1/cache/stats → CacheStats
- GET /api/v1/cache?limit=50 → list[CacheEntry]
- DELETE /api/v1/cache/{hash}
- DELETE /api/v1/cache (flush all)

Файлы:
- frontend/app/(dashboard)/cache/page.tsx
- frontend/components/cache/cache-stats.tsx
- frontend/components/cache/cache-table.tsx
- frontend/lib/api.ts — fetchCacheStats, fetchCacheEntries, deleteEntry, flushAll
- backend/app/api/v1/cache.py
- backend/tests/api/test_cache_v1.py
```

#### Промпт 26 — Config page

```
Реализуй страницу frontend/app/(dashboard)/config/page.tsx.

Содержимое:
1. LLM Provider section:
   - Provider URL (input)
   - Default model (input)
   - Timeout seconds (number)
   - "Test connection" button

2. Auth section:
   - Default API key (masked, click to reveal)
   - "Generate new key" button
   - Active keys list

3. Cache section:
   - TTL (number, minutes)
   - Max size (number)

4. Scanner section:
   - Max prompt length (chars)
   - Rate limit (RPS per key)

Кнопка "Save" — POST /api/v1/config
Кнопка "Reset to defaults"

API:
- GET /api/v1/config → Config
- POST /api/v1/config → updated Config

Файлы:
- frontend/app/(dashboard)/config/page.tsx
- frontend/components/config/llm-provider-form.tsx
- frontend/components/config/auth-form.tsx
- frontend/components/config/cache-form.tsx
- frontend/components/config/scanner-form.tsx
- frontend/lib/api.ts — fetchConfig, saveConfig
- backend/app/api/v1/config.py
- backend/tests/api/test_config_v1.py
```

#### Промпт 27 — Authentication для UI

```
Реализуй auth для UI.

Сценарий:
1. /login — форма с API key
2. При submit — POST /api/v1/auth/login с {api_key}
3. Backend верифицирует, возвращает JWT (24h TTL)
4. UI сохраняет JWT в httpOnly cookie (или localStorage если SSR нет)
5. Middleware: если нет JWT → redirect /login
6. Logout button в topbar

На MVP — простая API-key → JWT exchange. OPA появится в ALPHA.

Файлы:
- backend/app/api/v1/auth.py — login endpoint
- backend/app/core/jwt.py — encode/decode JWT
- frontend/middleware.ts — protect routes
- frontend/app/(auth)/login/page.tsx
- frontend/lib/auth.tsx — AuthProvider context
- backend/tests/api/test_auth_v1.py
```

#### Промпт 28 — Dockerfile для UI

```
Создай Dockerfile для frontend/.

Multi-stage build:
1. Builder: node:20-alpine, install pnpm, npm ci, npm run build
2. Runtime: nginx:alpine, copy .next/standalone + .next/static + public
- Expose 3000
- Healthcheck: curl http://localhost:3000/

next.config.js: output: 'standalone'

Файлы:
- frontend/Dockerfile
- frontend/.dockerignore
- frontend/next.config.js (добавь output: 'standalone')
```

#### Промпт 29 — Decision Explorer page

```
Реализуй страницу frontend/app/(dashboard)/decisions/page.tsx.

Содержимое:
1. Список последних 50 решений (как audit, но с детализацией):
   - Click row → expand:
     - Full redacted prompt text
     - Rules matched (с подсветкой в тексте)
     - Latency breakdown (fast_path_ms, slow_path_ms, pdp_ms, audit_ms)
     - Cache HIT/MISS
     - Tenant, request_id, ts

2. Filter: by tenant, by verdict, by rule matched

API: GET /api/v1/decisions?limit=50 — то же что audit, но с расширенным breakdown.

Файлы:
- frontend/app/(dashboard)/decisions/page.tsx
- frontend/components/decisions/decisions-table.tsx
- frontend/components/decisions/decision-detail.tsx
- frontend/components/decisions/latency-breakdown.tsx
- frontend/lib/api.ts — fetchDecisions
- backend/app/api/v1/decisions.py
- backend/tests/api/test_decisions_v1.py
```

---

## 3. Промпты для тестирования (cross-cutting)

#### Промпт 30 — Unit tests 90% coverage

```
Добейся 90% test coverage для всего backend кода.

Используй:
- pytest + pytest-asyncio + pytest-cov
- fakeredis для Redis tests
- testcontainers-postgres для DB tests
- respx для HTTP mocking

Структура тестов:
- backend/tests/unit/ — unit tests
- backend/tests/integration/ — integration с реальными deps
- backend/tests/fixtures/ — JSON fixtures

Configuration в pyproject.toml:
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "--cov=app --cov-report=term-missing --cov-report=html --cov-fail-under=90"

Команды:
- pytest (run all)
- pytest tests/unit/ (unit only)
- pytest --cov (coverage report)

Файлы:
- backend/pyproject.toml — добавить test deps
- backend/tests/conftest.py — fixtures
- backend/pytest.ini
- backend/tests/README.md
```

#### Промпт 31 — Integration tests

```
Создай integration tests для всего scanner pipeline в backend/tests/integration/.

Сценарии:
1. test_scan_clean_prompt: промпт без PII → ALLOW
2. test_scan_pii_ssn: с SSN → BLOCK
3. test_scan_aws_key: с AWS key → BLOCK
4. test_cache_hit: повторный промпт → cache HIT
5. test_cache_ttl_expiry: после 5min → cache MISS
6. test_openai_compat_endpoint: chat/completions → response
7. test_llm_provider_timeout: timeout → 502
8. test_audit_log_written: после scan → запись в Postgres
9. test_pii_redacted_in_audit: PII в логах замаскированы
10. test_metrics_exposed: /metrics возвращает корректные метрики

Запуск через testcontainers (поднимает Postgres + Redis).

Файлы:
- backend/tests/integration/test_scan_pipeline.py
- backend/tests/integration/test_proxy.py
- backend/tests/integration/conftest.py — testcontainers fixtures
```

---

## 4. Промпты для документации

#### Промпт 32 — README + Quick Start

```
Создай README.md в корне репозитория.

Структура:
1. Title + tagline: "LLM Security Scanner — защитный прокси между вашим AI-приложением и LLM"
2. Badges: Python 3.12, Next.js 15, License, Tests status
3. Что это? (1 параграф)
4. Возможности (bullet list: PII/secrets detection, cache, audit, Web UI)
5. Quick Start (5 минут):
   ```bash
   git clone <repo>
   cd llm-security-scanner
   cp .env.example .env
   docker compose up -d
   # UI: http://localhost:3000
   # API: http://localhost:8000
   ```
6. Конфигурация (ключевые env vars)
7. API Reference (link на docs/api.md)
8. SDK usage (Python example)
9. Development (как поднять dev env)
10. Testing (как запустить тесты)
11. Architecture (link на ARCHITECT.md)
12. Roadmap (link на ROADMAP.md)
13. License

Файлы:
- README.md
- docs/api.md — auto-generated из OpenAPI (см. EP-41.6)
```

#### Промпт 33 — API Reference (OpenAPI)

```
Сгенерируй OpenAPI 3.1 spec для backend.

FastAPI автоматически генерирует /openapi.json и /docs (Swagger UI).

Требования:
- Все endpoints описаны с tags, descriptions, examples
- Schemas в components/schemas с описаниями полей
- Security schemes: API-key (Authorization: Bearer)
- Servers: localhost, production placeholder
- Export в docs/openapi.yaml (через redocly CLI)

Команды:
redocly bundle backend/app/openapi.json --output docs/openapi.yaml

Также:
- docs/api.md — Markdown version (через widdershins)
- README.md ссылается на docs/api.md

Файлы:
- backend/app/main.py — добавить tags, descriptions
- backend/app/api/*.py — docstrings для всех endpoints
- docs/openapi.yaml — генерируется
- docs/api.md — генерируется
```

---

## 5. Финальная интеграция (финальный спринт MVP)

#### Промпт 34 — E2E тест

```
Создай E2E тест для проверки всего MVP pipeline.

Сценарий (Playwright):
1. Открыть http://localhost:3000 → redirect на /login
2. Ввести API key → submit → redirect на /dashboard
3. Проверить: KPI cards показывают числа, recent activity table есть
4. Перейти /test → ввести "My SSN is 123-45-6789" → click Scan
5. Проверить: badge "Block", reason "PII: US SSN"
6. Перейти /audit → проверить, что событие записано
7. Перейти /rules → редактировать правило, сохранить, проверить что применилось
8. Перейти /cache → проверить, что есть запись с вердиктом block
9. Перейти /config → изменить TTL cache на 10 минут, сохранить
10. Logout

Файлы:
- frontend/tests/e2e/mvp-flow.spec.ts
- frontend/playwright.config.ts
- frontend/tests/e2e/fixtures/test-data.json

Команда: cd frontend && npx playwright test
```

---

## 6. Checklist завершения MVP

```
MVP exit checklist (для phase gate review):

[ ] Reverse proxy работает на 100 RPS
[ ] p99 latency < 10ms
[ ] Rule engine ловит 100% тестового корпуса PII
[ ] Docker Compose разворачивается < 10 минут
[ ] Web UI:
    [ ] /dashboard показывает live-метрики
    [ ] /audit с фильтрами работает
    [ ] /rules editor сохраняет YAML
    [ ] /test показывает вердикт
    [ ] /cache management работает
    [ ] /config редактируется
    [ ] Login + logout работают
[ ] /metrics endpoint возвращает Prometheus метрики
[ ] Grafana dashboard настроен (5 базовых панелей)
[ ] 1 dogfooding-приложение использует сканер
[ ] Audit log сохраняет все решения с PII redaction
[ ] 90% test coverage
[ ] E2E тест проходит
[ ] README с Quick Start готов
[ ] OpenAPI spec сгенерирован
```

---

## 7. Связанные документы

- [ROADMAP.md](ROADMAP.md) — фазы и таймлайн
- [BACKLOG.md](BACKLOG.md) — детальный backlog (EP-01..06, EP-43)
- [ARCHITECT.md](ARCHITECT.md) — архитектура
- [ADR.md](ADR.md) — архитектурные решения (0001, 0002, 0004, 0014, 0015 basic)
- [ALPHA-PROMPTS.md](ALPHA-PROMPTS.md) — промпты для следующей фазы

---

*Промпты — живой документ. Обновляй после каждого спринта на основе feedback.*
