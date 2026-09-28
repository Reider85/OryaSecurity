from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram
from prometheus_client.core import CollectorRegistry

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

# Registry for metrics (can be used for testing)
metrics_registry = CollectorRegistry()

# Initialize metrics with zero values
def init_metrics() -> None:
    """Initialize all metrics to zero values."""
    scanner_cache_hits_total.clear()
    scanner_cache_misses_total.clear()
    scanner_cache_size.set(0)