from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, REGISTRY



def _unregister_builtin_python_info() -> None:
    """Drop prometheus_client's auto-registered python_info collector.

    ProcessCollector/PlatformCollector register ``python_info`` in the default
    registry; our custom Gauge with the same name would otherwise raise
    DuplicateTimeseries at import time.
    """
    collector_to_names = getattr(REGISTRY, "_collector_to_names", None)
    if not collector_to_names:
        return
    for collector in list(collector_to_names.keys()):
        if "python_info" in collector_to_names[collector]:
            try:
                REGISTRY.unregister(collector)
            except KeyError:
                pass


_unregister_builtin_python_info()

# Cache metrics
scanner_cache_hits_total = Counter(
    "scanner_cache_hits_total",
    "Total cache hits",
    labelnames=["type"],
)

scanner_cache_misses_total = Counter(
    "scanner_cache_misses_total", 
    "Total cache misses",
    labelnames=["type"],
)

scanner_cache_size = Gauge(
    "scanner_cache_size",
    "Current number of entries in cache",
)

scanner_cache_latency_seconds = Histogram(
    "scanner_cache_latency_seconds",
    "Cache operation latency in seconds",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)

# Request metrics
scanner_requests_total = Counter(
    "scanner_requests_total",
    "Total requests processed",
    labelnames=["verdict", "tenant_id"],
)

scanner_request_duration_seconds = Histogram(
    "scanner_request_duration_seconds",
    "Request processing latency in seconds",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0],
)

scanner_rules_matched_total = Counter(
    "scanner_rules_matched_total",
    "Total rules matched",
    labelnames=["rule_id"],
)

scanner_uptime_seconds = Gauge(
    "scanner_uptime_seconds",
    "Application uptime in seconds",
)

# Streaming metrics
scanner_stream_chunks_total = Counter(
    "scanner_stream_chunks_total",
    "Total streamed chunks inspected",
)

scanner_stream_blocks_total = Counter(
    "scanner_stream_blocks_total",
    "Total streamed chunks blocked",
    labelnames=["reason_type"],
)

# Post-factum deep analysis metrics (Prompt 6)
scanner_post_factum_total = Counter(
    "scanner_post_factum_total",
    "Total post-factum analyses by outcome",
    labelnames=["outcome"],  # flagged | clean | error
)

scanner_post_factum_duration_seconds = Histogram(
    "scanner_post_factum_duration_seconds",
    "Post-factum analysis latency in seconds",
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0],
)

scanner_post_factum_degraded_total = Counter(
    "scanner_post_factum_degraded_total",
    "Post-factum deep-analysis calls skipped due to unavailable components",
    labelnames=["component"],  # embedding | vector_search | ml_classifier
)

# Vector search SLO (Prompt 13): p99 < 30ms
scanner_vector_search_duration_seconds = Histogram(
    "scanner_vector_search_duration_seconds",
    "Vector search latency in seconds",
    buckets=[0.005, 0.01, 0.03, 0.05, 0.1, 0.5, 1.0],
)

# ML classifier SLO (Prompt 18): p99 < 15ms batch=1, <5ms batch=32
scanner_ml_classifier_duration_seconds = Histogram(
    "scanner_ml_classifier_duration_seconds",
    "ML classifier inference latency in seconds",
    buckets=[0.001, 0.005, 0.01, 0.015, 0.02, 0.05, 0.1, 0.5],
)

# SHAP explainability metrics (Prompt 19)
scanner_explainability_duration_seconds = Histogram(
    "scanner_explainability_duration_seconds",
    "SHAP explainability computation latency in seconds",
    buckets=[0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0],
)

scanner_explainability_total = Counter(
    "scanner_explainability_total",
    "Total SHAP explainability requests by outcome",
    labelnames=["outcome"],  # ok | cache_hit | timeout | degraded | disabled | error
)

# Embedding cache metrics (ADR-0015)
scanner_embedding_cache_hits_total = Counter(
    "scanner_embedding_cache_hits_total",
    "Total embedding cache hits",
)

scanner_embedding_cache_lookups_total = Counter(
    "scanner_embedding_cache_lookups_total",
    "Total embedding cache lookups",
)

scanner_embedding_cache_size = Gauge(
    "scanner_embedding_cache_size",
    "Current number of entries in embedding cache",
)

# WebSocket notifications metrics
scanner_ws_connections = Gauge(
    "scanner_ws_connections",
    "Current number of active WebSocket notification connections",
)

scanner_ws_notifications_total = Counter(
    "scanner_ws_notifications_total",
    "Total WebSocket notifications broadcast",
    labelnames=["type"],
)

# Slow path metrics (Prompt 23)
scanner_slow_path_duration_seconds = Histogram(
    "scanner_slow_path_duration_seconds",
    "Slow path pipeline latency in seconds",
    buckets=[0.01, 0.02, 0.03, 0.05, 0.1, 0.25, 0.5, 1.0],
)

scanner_slow_path_total = Counter(
    "scanner_slow_path_total",
    "Total slow path requests by outcome",
    labelnames=["outcome"],  # clean | threat | degraded
)

scanner_slow_path_degraded_total = Counter(
    "scanner_slow_path_degraded_total",
    "Slow path requests degraded due to unavailable components",
    labelnames=["component"],  # embedding | vector_search | ml_classifier
)

# Scanner pipeline path distribution (Prompt 24)
scanner_path_distribution = Counter(
    "scanner_path_distribution",
    "Scanner pipeline path distribution per request",
    labelnames=["path"],  # fast | slow | cache_hit
)

# OPA PDP decisions (EP-12/Prompt 28)
scanner_pdp_decisions_total = Counter(
    "scanner_pdp_decisions_total",
    "PDP decisions by evaluation source and resulting action",
    labelnames=["source", "action"],  # source: opa | local | local_fallback
)

scanner_pdp_opa_errors_total = Counter(
    "scanner_pdp_opa_errors_total",
    "OPA client errors by type",
    labelnames=["type"],  # http | decode | manifest | circuit_open
)

# Info-style gauge: one series per known policy version, value 1 for the active one.
scanner_policy_version_info = Gauge(
    "scanner_policy_version_info",
    "Active OPA policy bundle version (1 = active)",
    labelnames=["version"],
)

# Circuit breaker state (EP-14/Prompt 36, ADR-0008)
scanner_circuit_breaker_state = Gauge(
    "scanner_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    labelnames=["component"],  # vector_db | ml_classifier | vault
)

scanner_circuit_breaker_failures_total = Counter(
    "scanner_circuit_breaker_failures_total",
    "Upstream failures recorded by a circuit breaker",
    labelnames=["component"],
)

scanner_circuit_breaker_fallback_invocations_total = Counter(
    "scanner_circuit_breaker_fallback_invocations_total",
    "Circuit breaker fallback invocations by mode",
    labelnames=["component", "mode"],  # degrade | fail_open | fail_closed
)

# PII tokenization via Vault (EP-13/Prompt 32, ADR-0007)
scanner_redaction_tokens_total = Counter(
    "scanner_redaction_tokens_total",
    "PII/secrets tokens minted by the Vault redactor, by PII type",
    labelnames=["type"],  # SSN | EMAIL | AWS_KEY | ...
)

scanner_redaction_failures_total = Counter(
    "scanner_redaction_failures_total",
    "Vault redaction failures (fail-closed path), by error reason",
    labelnames=["error"],  # circuit_open | vault_unreachable | ...
)

# PII reverse tokenization via Vault (EP-13/Prompt 33, ADR-0007)
scanner_detokenization_tokens_total = Counter(
    "scanner_detokenization_tokens_total",
    "Vault tokens restored to original values, by PII type",
    labelnames=["type"],  # SSN | EMAIL | AWS_KEY | ...
)

scanner_detokenization_failures_total = Counter(
    "scanner_detokenization_failures_total",
    "Vault detokenization failures (token left in place), by error reason",
    labelnames=["error"],  # circuit_open | vault_decode_failed: ... | ...
)

python_info = Gauge(
    "python_info",
    "Python runtime information",
    labelnames=["version", "implementation", "platform"],
)

# Alias for the default registry, where all collectors above are registered.
# Kept as a module attribute because /api/v1/metrics/summary reads sample values
# from it. It must be REGISTRY, not a separate empty CollectorRegistry, or every
# lookup would return None and the dashboard would show zeros forever.
metrics_registry = REGISTRY

# Initialize metrics with zero values
def init_metrics() -> None:
    """Initialize all metrics to zero values."""
    scanner_cache_hits_total.clear()
    scanner_cache_misses_total.clear()
    scanner_cache_size.set(0)
    scanner_requests_total.clear()
    scanner_rules_matched_total.clear()
    scanner_uptime_seconds.set(0)
    scanner_stream_chunks_total.clear()
    scanner_stream_blocks_total.clear()
    scanner_post_factum_total.clear()
    scanner_post_factum_degraded_total.clear()
    scanner_ws_connections.set(0)
    scanner_ws_notifications_total.clear()
    scanner_slow_path_total.clear()
    scanner_slow_path_degraded_total.clear()
    scanner_path_distribution.clear()
    scanner_redaction_tokens_total.clear()
    scanner_redaction_failures_total.clear()
    scanner_detokenization_tokens_total.clear()
    scanner_detokenization_failures_total.clear()
    scanner_circuit_breaker_failures_total.clear()
    scanner_circuit_breaker_fallback_invocations_total.clear()