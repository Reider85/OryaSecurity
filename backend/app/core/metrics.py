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