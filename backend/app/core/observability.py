"""OpenTelemetry setup for LLM Security Scanner (Prompt 40).

Provides centralized telemetry initialization and shutdown.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Optional

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Tracer

from app.config import settings

logger = structlog.get_logger(__name__)

# Module-level guard to prevent double initialization
_tracer_provider_initialized = False
_instrumentation_initialized = False


def init_telemetry(app, *, span_exporter: Optional[SpanExporter] = None) -> None:
    """Initialize OpenTelemetry tracing with OTLP exporter.

    Args:
        app: FastAPI app instance to instrument
        span_exporter: Optional custom exporter (e.g., InMemorySpanExporter for tests)
                      If None and tracing enabled, uses OTLP exporter to OTEL_EXPORTER_OTLP_ENDPOINT.
                      If tracing disabled and no exporter, does nothing.
    """
    global _tracer_provider_initialized, _instrumentation_initialized

    if _tracer_provider_initialized:
        logger.info("telemetry_already_initialized")
        return

    # Check if tracing is enabled
    if not settings.otel_tracing_enabled and span_exporter is None:
        logger.info("telemetry_disabled", enabled=settings.otel_tracing_enabled)
        return

    # Create resource with service name and version
    resource = Resource.create(
        {
            "service.name": "scanner",
            "service.version": os.getenv("SCANNER_OTEL_SERVICE_VERSION", settings.otel_service_version),
        }
    )

    # Create tracer provider
    tracer_provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(tracer_provider)

    # Configure exporter
    if span_exporter:
        exporter = span_exporter
    else:
        # Use OTLP exporter
        endpoint = settings.otel_exporter_otlp_endpoint
        logger.info("telemetry_otlp_endpoint", endpoint=endpoint)
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)

    # Configure sampler (Prompt 41: tail sampling support)
    if settings.otel_sampling_ratio < 1.0:
        from opentelemetry.sdk.trace.sampling import (
            TraceIdRatioBased,
            ParentBased,
        )
        sampler = ParentBased(TraceIdRatioBased(settings.otel_sampling_ratio))
        tracer_provider.add_span_processor(
            BatchSpanProcessor(exporter, sampler=sampler)
        )
        logger.info(
            "telemetry_sampler_configured",
            sampler_type="ParentBased(TraceIdRatioBased)",
            ratio=settings.otel_sampling_ratio,
        )
    else:
        # Default: always_on (let collector do tail sampling)
        tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info("telemetry_sampler_default", sampler="always_on")

    # Instrument FastAPI app (only once)
    if not _instrumentation_initialized:
        FastAPIInstrumentor.instrument_app(app)
        HTTPXClientInstrumentor().instrument()
        AsyncPGInstrumentor().instrument()
        RedisInstrumentor().instrument()
        _instrumentation_initialized = True
        logger.info("telemetry_instrumentors_applied")

    _tracer_provider_initialized = True
    logger.info("telemetry_initialized", exporter=type(exporter).__name__)


def shutdown_telemetry() -> None:
    """Shutdown OpenTelemetry tracing."""
    global _tracer_provider_initialized, _instrumentation_initialized

    if not _tracer_provider_initialized:
        return

    tracer_provider = trace.get_tracer_provider()
    if hasattr(tracer_provider, "force_flush"):
        tracer_provider.force_flush()

    # Note: opentelemetry-api doesn't provide a public shutdown method for the provider.
    # In practice, the exporter's background threads will be cleaned up when the process exits.

    _tracer_provider_initialized = False
    _instrumentation_initialized = False
    logger.info("telemetry_shutdown")


def inject_trace_headers(headers: dict[str, Any]) -> None:
    """Inject W3C traceparent header into headers dict if active span exists.

    Args:
        headers: Dictionary to inject traceparent into
    """
    try:
        from opentelemetry.propagate import inject
        inject(headers)
    except ImportError:
        # Should not happen with opentelemetry-api installed
        pass


def get_tracer(name: str) -> Tracer:
    """Get a tracer instance for the given name.

    Args:
        name: Name for the tracer (usually __name__)

    Returns:
        Tracer instance
    """
    return trace.get_tracer(name)