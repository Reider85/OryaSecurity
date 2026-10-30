"""
Logging configuration for the application.

Provides structured JSON logging with structlog and ProcessorFormatter.
Supports PII redaction, OpenTelemetry trace correlation, and context variables.

JSON fields: ts, level, logger, event, msg, request_id, trace_id, span_id, tenant_id
"""

import logging
import re
import sys
from typing import Any, Dict, Optional

import structlog
from opentelemetry.trace import Span, Tracer, get_tracer_provider


# PII redaction patterns (compiled once for performance)
_REDACTION_PATTERNS = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b(?:\d[ -]*?){13,16}\b"), "[CARD]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[AWS_ACCESS_KEY]"),
    (re.compile(r"eyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}"), "[JWT]"),
    (re.compile(r"(?i)\bbearer\s+[\w.\-_]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?<!\w)\+?\d{10,15}\b"), "[PHONE]"),
]


def _add_trace_context(
    logger: Any, method_name: str, event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Inject trace_id and span_id from current OpenTelemetry span."""
    try:
        tracer_provider = get_tracer_provider()
        span: Span = tracer_provider.get_tracer(__name__).get_current_span()
        if span.is_valid():
            ctx = span.get_span_context()
            event_dict["trace_id"] = format(ctx.trace_id, "032x")
            event_dict["span_id"] = format(ctx.span_id, "016x")
        else:
            event_dict["trace_id"] = None
            event_dict["span_id"] = None
    except Exception:
        # No active span or tracing disabled
        event_dict["trace_id"] = None
        event_dict["span_id"] = None
    return event_dict


def _normalize_fields(
    logger: Any, method_name: str, event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Ensure required fields exist and msg is alias of event."""
    # Ensure event exists (first positional argument in structlog)
    if "event" not in event_dict:
        event_dict["event"] = "unknown"
    
    # Ensure required fields exist (may be added by contextvars)
    event_dict.setdefault("request_id", None)
    event_dict.setdefault("tenant_id", None)
    
    # msg is alias of event (for LogQL compatibility)
    event_dict["msg"] = event_dict["event"]
    
    return event_dict


def _redact_pii(
    logger: Any, method_name: str, event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Apply PII redaction to string values if enabled."""
    if not getattr(logger, "_log_redaction_enabled", True):
        return event_dict
    
    def redact_value(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        
        original = value
        for pattern, replacement in _REDACTION_PATTERNS:
            value = pattern.sub(replacement, value)
        
        return value
    
    # Apply redaction to all string values
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = redact_value(value)
        elif isinstance(value, dict):
            # Recursively redact nested dicts
            for nested_key, nested_value in value.items():
                if isinstance(nested_value, str):
                    value[nested_key] = redact_value(nested_value)
    
    return event_dict


# Common processor chain for both structlog and foreign logs
PRE_PROCESSORS = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", key="ts", utc=True),
    _add_trace_context,
    _normalize_fields,
    _redact_pii,
]


def setup_logging(level: str = "INFO") -> None:
    """
    Setup structured JSON logging for the application.
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    # Configure structlog with ProcessorFormatter
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *PRE_PROCESSORS,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    
    # Configure root logger with ProcessorFormatter
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=PRE_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    
    # Setup root logger
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper()))
    
    # Remove existing handlers to avoid duplicates
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    
    # Add stdout handler with our formatter
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root.addHandler(handler)
    
    # Configure uvicorn loggers to propagate to root (JSON output)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(getattr(logging, level.upper()))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger for the given name.
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        Structured logger instance
    """
    return structlog.get_logger(name)


# Default logger
logger = get_logger(__name__)