# ADR-0015: Observability — OpenTelemetry + Prometheus + Grafana + Loki

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-27 |
| **Deciders** | Architecture Team, SRE |
| **Related** | ADR-0008, ADR-0013 |

## Context

Production-ready система требует comprehensive observability для:
1. **Operational**: latency, throughput, error rate, availability.
2. **Security**: detection rate, FP/FN trend, top-10 flagged prompts, audit integrity.
3. **Compliance**: audit completeness, hashchain integrity, policy version coverage.
4. **SRE SLO tracking**: error budget consumption, alerting.

В исходной архитектуре презентации observability-слой не описан.

### Forces

- **Vendor-neutral**: нельзя привязываться к Datadog/New Relic (на on-prem air-gapped нет).
- **Standard protocol**: trace context propagation через HTTP/gRPC headers.
- **Low-overhead**: instrumentation не должна добавлять > 1 ms latency.
- **Multi-tenant**: per-tenant metrics (label `tenant_id`).
- **Cost**: на больших объёмах (10K RPS × 100 spans/request = 1M spans/sec) — нужна sampling.

## Decision

**Принять стек: OpenTelemetry (instrumentation) + Prometheus (metrics) + Loki (logs) + Tempo/Jaeger (traces) + Grafana (dashboards) + Alertmanager (alerts).**

```
[Scanner] ──OTLP──> [OTel Collector] ─┬─> [Prometheus]      (metrics)
                                       ├─> [Loki]            (logs)
                                       ├─> [Tempo/Jaeger]    (traces)
                                       └─> [Alertmanager]    (alerts)
                                                            │
                                                            └─> [PagerDuty / Slack / Email]
[Grafana] ──queries──> Prometheus / Loki / Tempo
```

### Instrumentation: OpenTelemetry

**OTLP (OpenTelemetry Protocol)** — стандартный формат экспорта.

В коде сканера (Python example):

```python
from opentelemetry import trace, metrics
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)

# Auto-instrument FastAPI
FastAPIInstrumentor.instrument_app(app)

# Custom spans for scanner pipeline
@tracer.start_as_current_span("scan_request")
def handle_scan_request(req):
    with tracer.start_as_current_span("fast_path"):
        verdict = fast_path_check(req)
    
    if verdict.is_suspicious:
        with tracer.start_as_current_span("slow_path"):
            embedding = embed(req.text)
            with tracer.start_as_current_span("vector_search"):
                results = qdrant.search(embedding)
            with tracer.start_as_current_span("ml_classify"):
                cls = classifier.classify(req.text)
    
    # Custom metrics
    scan_counter = meter.create_counter("scanner.requests")
    scan_counter.add(1, {"tenant_id": req.tenant_id, "verdict": verdict.action})
    
    return verdict
```

### Метрики (Prometheus)

#### Operational metrics

```promql
# Latency (histogram)
histogram_quantile(0.99, rate(scanner_request_duration_seconds_bucket[5m]))
histogram_quantile(0.95, rate(scanner_request_duration_seconds_bucket[5m]))
histogram_quantile(0.50, rate(scanner_request_duration_seconds_bucket[5m]))

# Throughput
rate(scanner_requests_total[5m])
rate(scanner_requests_total{tenant_id="acme"}[5m])

# Errors
rate(scanner_errors_total{type="upstream_timeout"}[5m])
rate(scanner_errors_total{type="ml_inference_failed"}[5m])

# Cache hit ratio
scanner_decision_cache_hits_total / scanner_decision_cache_lookups_total
scanner_embedding_cache_hits_total / scanner_embedding_cache_lookups_total

# Circuit breaker
scanner_circuit_breaker_state{component="vector_db"}
scanner_circuit_breaker_state{component="ml_classifier"}
scanner_circuit_breaker_state{component="vault"}
```

#### Security metrics

```promql
# Detection rate by type
rate(scanner_detections_total{type="prompt_injection"}[5m])
rate(scanner_detections_total{type="jailbreak"}[5m])
rate(scanner_detections_total{type="pii_leakage"}[5m])
rate(scanner_detections_total{type="secret_leakage"}[5m])
rate(scanner_detections_total{type="toxicity"}[5m])

# Verdict distribution
rate(scanner_verdicts_total{verdict="allow"}[5m])
rate(scanner_verdicts_total{verdict="redact"}[5m])
rate(scanner_verdicts_total{verdict="block"}[5m])
rate(scanner_verdicts_total{verdict="route_to_human"}[5m])
rate(scanner_verdicts_total{verdict="log_only"}[5m])

# FP/FN (from feedback)
rate(scanner_feedback_labels_total{label="fp"}[1h])
rate(scanner_feedback_labels_total{label="fn"}[1h])
scanner_fp_rate = rate(scanner_feedback_labels_total{label="fp"}[1h]) / rate(scanner_verdicts_total{verdict="block"}[1h])

# Top-10 flagged prompts (cardinality limited by hash bucket)
topk(10, sum by (prompt_hash_bucket) (rate(scanner_detections_total[1h])))
```

#### Compliance metrics

```promql
# Audit integrity
scanner_audit_writes_total / scanner_audit_writes_expected_total  # should be 1.0
scanner_audit_hashchain_verifications_failed_total                  # should be 0

# Policy coverage
scanner_policy_versions_active  # number of distinct active policy versions
scanner_policy_decisions_by_version{version="v3.2.1"}              # decisions per policy version

# Threat intel freshness
scanner_threat_intel_last_update_timestamp
time() - scanner_threat_intel_last_update_timestamp  # staleness in seconds
```

### Logs (Loki)

Structured JSON logs:

```json
{
  "ts": "2026-09-27T15:42:13Z",
  "level": "INFO",
  "tenant_id": "acme",
  "request_id": "req_abc123",
  "trace_id": "trace_def456",
  "span_id": "span_ghi789",
  "event": "scan_complete",
  "verdict": "block",
  "reason": "prompt_injection_score_0.92",
  "policy_version": "v3.2.1",
  "latency_ms": 23,
  "scan_path": "slow_path"
}
```

Loki запросы (LogQL):
```logql
# All blocked requests in last hour
{app="scanner", event="scan_complete", verdict="block"} |= "prompt_injection"

# Errors by tenant
{app="scanner", tenant_id="acme", level="ERROR"}

# Slow requests (>50ms)
{app="scanner"} | json | latency_ms > 50
```

### Traces (Tempo / Jaeger)

Каждый запрос → 1 trace, span'ы на каждый компонент:

```
trace_id: trace_abc123
├── span: ingress (1ms)
├── span: orchestrator (0.5ms)
├── span: cache_lookup (0.2ms) ─── miss
├── span: fast_path (0.8ms)
│   ├── span: rule_engine (0.7ms)
│   └── span: suspicion_check (0.1ms) ─── suspicious!
├── span: slow_path (15ms)
│   ├── span: embedding (3ms)
│   ├── span: vector_search (8ms)
│   └── span: ml_classify (4ms) ─── prompt_injection: 0.92
├── span: pdp (1ms) ─── verdict: block
├── span: audit_write (2ms)
└── span: response_send (0.5ms)
```

Sampling: 100% blocked/error requests, 10% normal requests (head-based sampling). Tail-based sampling в OTel Collector для редких интересных паттернов.

### Dashboards (Grafana)

#### Operational dashboard
- Latency p50/p95/p99 (line chart)
- Throughput (req/sec)
- Error rate (%)
- Cache hit ratio (%)
- Circuit breaker states (table)

#### Security dashboard
- Detection rate by attack type (stacked bar)
- Verdict distribution (pie chart)
- FP/FN rate trend (line chart)
- Top-10 flagged prompt hashes (table)
- Threat intel staleness (single stat)

#### Compliance dashboard
- Audit write completeness (%)
- Hashchain integrity (single stat: OK/FAIL)
- Policy version distribution (bar chart)
- Last threat-intel update (single stat)

### Alerts (Alertmanager)

| Alert | Condition | Severity | Notification |
|---|---|---|---|
| HighLatency | p99 > 100ms за 5 мин | Warning | Slack |
| CircuitBreakerOpen | state=open за 1 мин | Critical | PagerDuty |
| AuditWriteFailure | rate > 0 за 1 мин | Critical | PagerDuty |
| HashchainIntegrityFailure | verification_failed > 0 | Critical | PagerDuty + Security on-call |
| HighFP | FP rate > 5% за 1 hour | Warning | Slack #ml-alerts |
| HighFN | FN rate > 3% за 1 hour | Warning | Slack #ml-alerts |
| VectorDBDown | health=fail за 1 мин | Critical | PagerDuty |
| ThreatIntelStale | last_update > 24h | Warning | Slack #security |
| EmbeddingServiceDown | health=fail за 1 мин | Critical | PagerDuty |
| OPABundleFetchFailed | rate > 0 за 5 мин | Warning | Slack #platform |

## Consequences

### Positive

- ✅ Vendor-neutral: работает on-prem (no Datadog dependency).
- ✅ Standard protocols: OTLP, PromQL, LogQL — стандартные, много специалистов.
- ✅ Distributed tracing — отладка cross-component.
- ✅ Multi-tenant: tenant_id label везде.
- ✅ Compliance dashboards: аудит для регуляторов.
- ✅ Open-source stack: нулевая license cost.

### Negative

- ❌ Self-hosted stack — нужен SRE для Prometheus/Loki/Tempo/Grafana.
- ❌ Storage cost: 30-day metrics retention × 10M series = ~100 GB. Traces: ~500 GB/month.
- ❌ Cardinality: `tenant_id × verdict × policy_version` — может взорвать Prometheus. Mitigation: labeling discipline, drop unused labels.

### Neutral

- ➖ Sampling rate для traces — настраивается (10% normal, 100% interesting).
- ➖ Alertmanager routes: separate Slack channels per severity.

## Alternatives Considered

### Alternative A: Datadog / New Relic / Dynatrace (commercial APM)

| Аспект | Оценка |
|---|---|
| Pros | Turnkey, rich features, ML-based anomaly detection |
| Cons | Paid (~$15-30/host/month), cloud-only mostly, vendor lock-in, не работает air-gapped |
| Why rejected | Не работает для on-prem air-gapped клиентов |

**Вердикт:** Возможно для SaaS-режима Enterprise как optional integration.

### Alternative B: ELK Stack (Elasticsearch + Logstash + Kibana)

| Аспект | Оценка |
|---|---|
| Pros | Mature, search-focused, SIEM integration |
| Cons | Heavy (JVM), expensive at scale, no native metrics/tracing (требует доп. integrations) |
| Why rejected | Loki — simpler, cheaper для логов. ELK — для SIEM integration (опционально) |

**Вердикт:** Возможно для SIEM integration в Enterprise (Phase 3).

### Alternative C: Plain logging + manual analysis

| Аспект | Оценка |
|---|---|
| Pros | Минимальная сложность |
| Cons | Не scales, нет real-time alerts, нет distributed tracing, не SRE-compliant |
| Why rejected | Не production-ready |

**Вердикт:** Отклонено.

### Alternative D: Pure OpenTelemetry + Jaeger-only

| Аспект | Оценка |
|---|---|
| Pros | Single vendor (CNCF), minimal stack |
| Cons | OTLP not yet production-stable for all backends. Prometheus всё ещё де-факто для metrics |
| Why rejected | Hybrid (Prometheus + OTel) — current best practice |

**Вердикт:** Текущая схема использует лучшие из обоих миров.

## Related Decisions

- **ADR-0008** (Circuit Breaker) — state метрики экспортируются в Prometheus.
- **ADR-0013** (Feedback Loop) — FP/FN метрики из feedback labels.

## References

- [OpenTelemetry documentation](https://opentelemetry.io/docs/)
- [Prometheus best practices](https://prometheus.io/docs/practices/naming/)
- [Loki: like Prometheus, but for logs](https://grafana.com/oss/loki/)
- [Tempo: distributed tracing](https://grafana.com/oss/tempo/)
- [Google SRE: Monitoring Distributed Systems](https://sre.google/sre-book/monitoring-distributed-systems/)
- [Four Golden Signals](https://sre.google/sre-book/monitoring-distributed-systems/) — latency, traffic, errors, saturation
- ARCHITECT.md, раздел 12 — observability stack
