# Grafana Dashboards (EP-15, Prompt 43)

Операционные Grafana-дашборды LLM Security Scanner: метрики (Prometheus),
логи (Loki), трейсы (Jaeger). Дашборды провижинятся автоматически при
старте контейнера `grafana`.

## Архитектура

```
scanner (:8000)
   │  /metrics (prometheus_client)
   ▼
Prometheus (:9090)  ◄── observability/prometheus-rules.yml (alerts)
   │                        │
   │  scrape                │ ALERTS metric
   ▼                        ▼
Grafana (:3001) ────── scanner_operational ──┐
   ▲                                         │
   │  /var/lib/grafana/dashboards (ro)       │
   │  ◄── observability/grafana/dashboards/*.json
   │
   ├── Loki (:3100)     ← structured JSON logs (app="scanner")
   └── Jaeger (:16686)  ← OTel traces (service.name="scanner")
```

## Дашборды

| Дашборд | UID | Файл | Панели |
|---|---|---|---|
| Scanner Operational | `scanner-operational` | `scanner_operational.json` | RPS, Latency p50/95/99, Error rate %, Cache hit ratio, Circuit breaker states, Top-5 alerts (Loki) |
| Scanner Path Distribution | `scanner-path-distribution` | `scanner.json` | path distribution (fast/slow/cache_hit) |
| Circuit Breakers | `circuit-breakers` | `circuit_breakers.json` | CB state, transitions, failures, fallbacks |

## Scanner Operational — панели

1. **RPS (requests/sec)** — `rate(scanner_requests_total)` по вердиктам
   (allow/block/review), фильтр по `$tenant_id`.
2. **Latency p50 / p95 / p99** — `histogram_quantile` по
   `scanner_request_duration_seconds_bucket`.
3. **Error Rate (% of RPS, per type)** — отношение ошибочных событий к RPS:
   OPA errors (`scanner_pdp_opa_errors_total`), post-factum errors,
   stream blocks (`scanner_stream_blocks_total`), slow-path degradations,
   circuit breaker failures, redaction/detokenization failures.
   Деление через `/ on() group_left` — каждая серия делится на общий RPS.
4. **Cache Hit Ratio** (gauge) —
   `hits / (hits + misses)` по `scanner_cache_{hits,misses}_total{type="decision"}`.
   Пороги: red < 10%, yellow ≥ 10%, green ≥ 30% (ALPHA exit criterion).
5. **Circuit Breaker States** (table) — `scanner_circuit_breaker_state`
   с маппингом 0=CLOSED (green), 1=OPEN (red), 2=HALF_OPEN (orange).
6. **Top-5 Alerts (Loki)** (table) — 5 последних WARN/ERROR/CRITICAL
   событий: `{app="scanner"} | json | level=~"WARN|ERROR|CRITICAL"`.
   Derived field «Trace» (настроен в Loki datasource) ведёт в Jaeger UI.

## Переменные

| Переменная | Источник | Назначение |
|---|---|---|
| `tenant_id` | `label_values(scanner_requests_total, tenant_id)` (Prometheus, refresh on time range change) | Фильтрует RPS и denominator панели Error Rate. Значение `All` → `.*` |

## Datasource-зависимости

| Datasource | Тип | Provisioning |
|---|---|---|
| `Prometheus` | prometheus | `observability/grafana/provisioning/datasources/prometheus.yml` |
| `Loki` | loki (derived field → Jaeger) | `observability/grafana/provisioning/datasources/loki.yml` |
| `Jaeger` | jaeger (`uid: jaeger`) | `observability/grafana/provisioning/datasources/jaeger.yml` |

Ссылки на Jaeger и Loki добавлены в dashboard links (верхняя панель
дашборда). Jaeger/Loki доступны только в ALPHA-стеке
(`docker-compose.alpha.yml`); без них панель «Top-5 Alerts» будет пустой,
остальные панели работают на одном Prometheus.

## Запуск

```bash
# Базовый стек (Prometheus + Grafana)
docker compose --profile observability up -d

# Полный ALPHA-стек (+ Jaeger, Loki, OTel collector)
docker compose -f docker-compose.yml -f docker-compose.alpha.yml --profile observability up -d

# Grafana: http://localhost:3001 (admin / admin)
# Дашборды: http://localhost:3001/d/scanner-operational
```

## Провижининг

`observability/grafana/provisioning/dashboards/scanner.yml` монтируется
в `/etc/grafana/provisioning/dashboards/` и указывает provider'у на путь
`/var/lib/grafana/dashboards` — туда же compose монтирует каталог
`observability/grafana/dashboards/` (ro). Все `*.json` в этом каталоге
подхватываются автоматически, перескан — каждые 10 сек.

## Как добавить панель

1. Оформи query в Prometheus Explore (или Loki Explore).
2. Добавь panel JSON в `observability/grafana/dashboards/scanner_operational.json`
   (поля `gridPos` — сетка 24 колонки, `id` — уникальный).
3. Datasource указывай строкой: `"Prometheus"` / `"Loki"` (имена из
   provisioning). Для rate'ов используй `$__rate_interval`.
4. Проверь: `docker compose restart grafana`, обнови страницу дашборда.

## Полезные PromQL / LogQL

```promql
# RPS по вердиктам
sum by (verdict) (rate(scanner_requests_total[5m]))

# p99 latency
histogram_quantile(0.99, sum by (le) (rate(scanner_request_duration_seconds_bucket[5m])))

# Cache hit ratio (decision cache)
sum(rate(scanner_cache_hits_total{type="decision"}[5m]))
  / (sum(rate(scanner_cache_hits_total{type="decision"}[5m]))
   + sum(rate(scanner_cache_misses_total{type="decision"}[5m])))

# Открытые circuit breaker'ы
scanner_circuit_breaker_state == 1

# Какие tenants сейчас активны
label_values(scanner_requests_total, tenant_id)
```

```logql
# Ошибки сканера
{app="scanner"} |= "error"

# Заблокированные запросы
{app="scanner", event="scan_complete", verdict="block"}

# Rate ошибок
rate({app="scanner", level="ERROR"}[5m])
```

## Связанные ADR

- [ADR-0015: Observability — OpenTelemetry + Prometheus + Grafana + Loki](../analytics/adr-final/0015-observability-otel-prometheus.md)
