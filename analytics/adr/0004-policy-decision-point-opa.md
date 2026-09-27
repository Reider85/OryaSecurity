# ADR-0004: Open Policy Agent как Policy Decision Point

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-27 |
| **Deciders** | Architecture Team, SecEng, Compliance |
| **Related** | ADR-0011, ADR-0013 |

## Context

После того как детекторы (regex, vector search, ML classifier) выдали risk scores и метаданные, система должна принять **решение**: что делать с запросом. Доступные действия:

| Действие | Описание |
|---|---|
| `ALLOW` | Пропустить без модификаций |
| `REDACT` | Маскировать PII/secrets, пропустить |
| `BLOCK` | Отклонить с указанием причины |
| `ROUTE_TO_HUMAN` | Поставить в очередь ручной проверки |
| `LOG_ONLY` | Пропустить, отправить в SecOps (canary-режим новой политики) |

Это решение зависит от:
- **Risk score** от детекторов,
- **Tenant** (разные клиенты — разные политики),
- **App context** (приложение, пользователь, окружение),
- **Policy version** (политики эволюционируют, нужны версии и A/B),
- **Mode** (dev / staging / prod),
- **Time-of-day / day-of-week** (опционально, для специальных политик).

### Forces

- **Гибкость**: SecOps-команда должна менять политики без редеплоя сканера.
- **Версионирование**: нужна возможность отката политики и A/B-тестирования.
- **Производительность**: p99 < 2 ms на решение.
- **Audit**: для compliance нужно фиксировать, какая именно политика и версия применялась.
- **Expressiveness**: нужны сложные правила (risk > 0.7 AND tenant = "acme" AND NOT user_in_allowlist).
- **Multi-tenancy**: изоляция политик между tenant'ами.
- **Reviewability**: политики как код — PR, review, sign.

## Decision

**Принять Open Policy Agent (OPA) с политиками на Rego.**

```
[Detectors] ──> risk scores, metadata ─┐
[Tenant context] ──────────────────────┤──> [OPA Engine] ──> Decision
[Policy bundle vN] ────────────────────┘
```

### Структура хранения политик

```
policies/
├── tenants/
│   ├── acme/                  # tenant-specific
│   │   ├── base.rego          # базовая политика tenant'a
│   │   ├── pii.rego           # PII-правила
│   │   └── experimental/      # A/B-версии
│   │       └── v2_strict.rego
│   └── globex/
│       └── ...
├── global/                    # общие политики
│   ├── owasp_top10.rego
│   └── default_actions.rego
└── manifest.json              # bundle manifest с версиями
```

### Пример политики (Rego)

```rego
package scanner.tenant.acme

import data.scanner.global.owasp_top10

# Default: allow
default allow := true

# Block high-risk prompt injection
deny[msg] {
    input.detectors.prompt_injection.score > 0.8
    input.context.app = "customer-support-bot"
    msg := sprintf("Blocked: prompt injection score %.2f in app %s", [input.detectors.prompt_injection.score, input.context.app])
}

# Redact PII instead of blocking
redact[pii_type] {
    input.detectors.pii.found[_].type = pii_type
    input.context.user.role = "support_agent"
}

# Route to human for high-risk low-confidence
route_to_human {
    input.detectors.prompt_injection.score > 0.5
    input.detectors.prompt_injection.score < 0.8
    input.context.app = "legal-advisor"
}
```

### Деплой политик

```
Git PR (signed) ──> CI: opa check + unit tests ──> Bundle build ──> OPA Bundle Service ──> Scanner pulls (poll every 30s)
```

### Версии и A/B

```json
// manifest.json
{
  "bundles": [
    {"id": "acme-v3", "version": "3.0.0", "sha256": "...", "rollout": 100},
    {"id": "acme-v4-experimental", "version": "4.0.0-rc1", "sha256": "...", "rollout": 5,
     "cohort": {"user_id_hash": "mod100", "range": [0, 5]}}
  ]
}
```

## Consequences

### Positive

- ✅ Политики as code — Git review, sign, audit trail изменений.
- ✅ Версионирование bundle'ов — A/B-тестирование, мгновенный откат.
- ✅ Sandbox: Rego — декларативный, безопасный (no I/O, no network).
- ✅ Производительность: типичная политика — < 1 ms (компилируется в Go bytecode).
- ✅ Лёгкая интеграция с K8s (OPA Gatekeeper) — единый control plane.
- ✅ Audit trail содержит policy_version — compliance-friendly.

### Negative

- ❌ Rego learning curve для SecOps-аналитиков. Mitigation: внутренний DSL-конструктор (UI) → генерация Rego.
- ❌ Bundle distribution требует инфраструктуры (OPA Bundle Service или CDN).
- ❌ Ошибки в политике могут мгновенно заблокировать весь трафик. Mitigation: canary rollout 5% → 50% → 100%, auto-rollback по FP-метрике.
- ❌ Регулярные expression'ы в Rego ограничены — для сложных regex придётся выносить в Python-слой.

### Neutral

- ➖ OPA — отдельный сервис (sidecar или remote), нужен monitoring.
- ➖ Политики логически разделены tenant/global — но компилируются в один bundle per tenant.

## Alternatives Considered

### Alternative A: Cedar (Amazon)

| Аспект | Оценка |
|---|---|
| Pros | Простой синтаксис, разработан Amazon для authz, лучше DX для не-программистов |
| Cons | Моложе ecosystem'а, меньше интеграций с K8s/Envoy, нет bundle distribution |
| Why rejected | Недостаточная зрелость для production critical-path. Перейти можно через 2-3 года |

**Вердикт:** Рассмотреть в Phase 4.

### Alternative B: Custom rules engine (Python / Go)

| Аспект | Оценка |
|---|---|
| Pros | Полный контроль, любая логика, легко нанять разработчиков |
| Cons | Re-inventing wheel: версионирование, A/B, audit, sandbox — всё это надо строить. Высокий риск ошибок в безопасности |
| Why rejected | NRE cost несопоставим с выгодой. Использование proven solution (OPA) предпочтительнее для security-critical component |

**Вердикт:** Отклонено.

### Alternative C: Hardcoded в коде сканера

| Аспект | Оценка |
|---|---|
| Pros | Максимальная простота, нулевая задержка |
| Cons | Изменение политики = редеплой сканера. Нет версионирования. Невозможен A/B. Невозможен compliance audit «какая политика действовала в момент T» |
| Why rejected | Противоречит требованиям гибкости, версионирования, audit |

**Вердикт:** Отклонено.

### Alternative D: AWS EventBridge / DynamoDB Streams + custom rules

| Аспект | Оценка |
|---|---|
| Pros | Serverless, интеграция с AWS-стеком |
| Cons | Vendor lock-in, противоречит требованию локального развёртывания |
| Why rejected | Не работает on-prem |

**Вердикт:** Отклонено.

## Related Decisions

- **ADR-0011** (Multi-tenancy) — per-tenant bundle изоляция.
- **ADR-0013** (Feedback Loop) — A/B-тестирование политик через canary.
- **ADR-0006** (Hashchain Audit) — audit log содержит policy_version.

## References

- [Open Policy Agent documentation](https://www.openpolicyagent.org/docs/)
- [Rego playground](https://play.openpolicyagent.org/)
- [OPA Bundle Service](https://www.openpolicyagent.org/docs/latest/management-bundles/)
- [Cedar policy language (Amazon)](https://www.cedarpolicy.com/)
- ARCHITECT.md, раздел 6.4 — PDP с A/B
