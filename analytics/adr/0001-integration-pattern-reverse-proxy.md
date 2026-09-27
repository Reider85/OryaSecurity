# ADR-0001: Шаблон интеграции — Reverse Proxy как primary, Sidecar как опция

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-27 |
| **Deciders** | Architecture Team, SecEng, Platform Team |
| **Related** | ADR-0002, ADR-0003, ADR-0008, ADR-0014 |

## Context

Сканер должен располагаться строго между пользователем (AI-приложением) и LLM-провайдером. Это требование продиктовано самой природой системы: инспекция должна происходить **до** отправки данных в LLM (для prompt-side) и **до** возврата ответа пользователю (для generation-side).

Рассматривались четыре варианта топологии:

1. **Reverse Proxy** — отдельный сетевой сервис, через который проксируется трафик AI-приложений.
2. **Sidecar** — контейнер рядом с AI-приложением в каждом K8s Pod.
3. **SDK / Library** — библиотека, встраиваемая в код AI-приложения.
4. **eBPF / Network Layer** — инспекция на уровне ядра ОС.

### Forces (силы, влияющие на решение)

- **Задержка**: каждый дополнительный network hop добавляет 0.5–2 ms. Для чат-продуктов критичен p99 < 50 ms.
- **Прозрачность для приложения**: AI-приложения не должны переписываться для работы со сканером.
- **Обновляемость детекторов**: правила и ML-модели обновляются еженедельно, без передеплоя AI-приложений.
- **Языко-агностичность**: AI-приложения могут быть на Python / Node.js / Go / Java.
- **Multi-tenancy**: один сканер обслуживает несколько команд/клиентов.
- **Локальное развёртывание**: требование из презентации (on-prem / air-gapped).
- **Observability**: трассировка запроса должна проходить через сканер.

## Decision

**Принять Reverse Proxy как primary шаблон интеграции**, с Sidecar как опцией для latency-sensitive сценариев (Enterprise Phase 3).

```
                    [AI App] ──HTTP/gRPC──> [Reverse Proxy Scanner] ──> [LLM]
                                          │
                                          └─ inspects both directions
```

### Обоснование

Reverse Proxy обеспечивает:
1. Единая точка контроля — все AI-приложения одного контура идут через один сканер.
2. Языко-агностичность — AI-приложению всё равно, на чём написан сканер.
3. Независимое обновление детекторов — rollout scanner не требует редеплоя приложений.
4. Multi-tenancy через routing и per-tenant policy.

Для Sidecar добавлен как опция, когда:
- Latency budget очень жёсткий (< 20 ms end-to-end),
- AI-приложение уже в K8s,
- Per-app пользовательские политики необходимы (а не только per-tenant).

## Consequences

### Positive

- ✅ Единая точка применения политик, аудита, rate-limit.
- ✅ Языко-агностичность: AI-приложение просто переключает endpoint на scanner proxy.
- ✅ Независимый release cycle scanner'а и AI-приложений.
- ✅ Проще реализовать observability: один сервисinstrumented.
- ✅ Multi-tenancy через Host header / API-key routing.

### Negative

- ❌ Дополнительный network hop (~1–2 ms) для всех запросов.
- ❌ Reverse Proxy — потенциальный SPOF (митигируется ADR-0008: circuit breaker + fallback + HA-деплой).
- ❌ Требуется поддержка streaming protocols (SSE, WebSocket, gRPC-stream) — усложняет реализацию (ADR-0003).
- ❌ Требуется mTLS между AI App и Scanner для zero-trust (дополнительная PKI).

### Neutral

- ➖ Reverse Proxy может стать «умным» — добавлять метаданные (X-Scanner-Verdict, X-Scan-Latency), что упрощает observability, но требует контракта между сканером и приложением.
- ➖ Сетевой путь: AI App → Scanner → LLM vs AI App → LLM. Трафик удваивается на стороне сканера.

## Alternatives Considered

### Alternative A: Sidecar-only

```
[K8s Pod]
  ├── [AI App container]
  └── [Scanner sidecar]   <- localhost only
```

| Аспект | Оценка |
|---|---|
| Pros | Минимальная задержка (no network hop), нет SPOF, изоляция per-app |
| Cons | Обновление детекторов требует restart всех подов, расход ресурсов × N подов, cross-app политик нет |
| Why rejected | Невозможно применить общие политики (rate-limit, threat-intel) без отдельного control plane. Усложняется обновление. |

**Вердикт:** Sidecar — дополняющий паттерн, не primary. Принят как опция для Enterprise Phase 3.

### Alternative B: SDK / Library

```
[AI App code] -> [import scanner-sdk] -> [LLM]
```

| Аспект | Оценка |
|---|---|
| Pros | Минимальная задержка, нет сетевого слоя, простота для прототипов |
| Cons | Привязка к языку (нужен SDK на Python + Node + Go + Java), обновление требует редеплоя приложений, сложнее поддерживать наблюдаемость |
| Why rejected | Каскадная поддержка 4+ языковых SDK непропорционально дорога для команды. Обновление threat-intel потребовало бы редеплоя всех приложений. |

**Вердикт:** Возможен как вспомогательный режим для edge-cases (edge-устройства с air-gapped деплоем), но не primary.

### Alternative C: eBPF / Network Layer

```
[Kernel eBPF program inspects socket traffic]
```

| Аспект | Оценка |
|---|---|
| Pros | Полная прозрачность для приложения, нулевая задержка на прикладном уровне |
| Cons | Нельзя инспектировать семантику (нужен full text + ML), eBPF программы ограничены по сложности, привязка к Linux kernel версии |
| Why rejected | eBPF хорош для L3-L4 инспекции (rate-limit, geo-block), но не подходит для семантического анализа. Можно использовать как дополнительный слой. |

**Вердикт:** Не подходит как primary. Возможно применение в Enterprise для network-level политик (rate-limit per IP, DDoS).

### Alternative D: Combined: Reverse Proxy + eBPF

Reverse Proxy для семантической инспекции + eBPF для сетевых политик (rate-limit, geo). Принято как target для Enterprise.

## Related Decisions

- **ADR-0002** (Two-tier Pipeline) — реализация fast path внутри reverse proxy.
- **ADR-0003** (Streaming Inspection) — поддержка streaming-протоколов в proxy.
- **ADR-0008** (Circuit Breaker) — митигация SPOF-рисков reverse proxy.
- **ADR-0014** (Deployment Topology) — где физически разворачивается proxy.

## References

- [Pattern: Reverse Proxy](https://martinfowler.com/articles/enterprise-architectures.html) — Martin Fowler
- [Envoy Proxy](https://www.envoyproxy.io/) — рассматривается как реализация ingress layer
- OWASP LLM Top 10 — обоснование необходимости inline-инспекции
- ARCHITECT.md, раздел 2.1 — топологические варианты интеграции
