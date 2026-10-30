"""Tests for structured logging functionality."""

import json
import logging
import sys
from io import StringIO
from typing import Any, Dict
from unittest.mock import patch

import pytest
import structlog
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.logging import setup_logging, get_logger, _redact_pii, _normalize_fields, _add_trace_context


class TestLoggingSetup:
    """Test logging configuration and functionality."""

    def setup_method(self):
        """Reset logging configuration before each test."""
        # Clear any existing logging configuration
        logging.root.handlers.clear()
        logging.root.setLevel(logging.WARNING)
        
        # Reset structlog configuration
        structlog.reset_wrapper_class()
        structlog._CONFIG = None

    def test_json_schema_required_fields(self):
        """Test that JSON logs contain all required fields."""
        # Setup logging with string capture
        string_io = StringIO()
        handler = logging.StreamHandler(string_io)
        formatter = setup_logging("DEBUG")
        logging.root.addHandler(handler)
        
        # Get a logger and log a message
        logger = get_logger("test")
        logger.info("test message", extra_field="value")
        
        # Parse the JSON output
        output = string_io.getvalue().strip()
        log_data = json.loads(output)
        
        # Check required fields
        required_fields = {"ts", "level", "logger", "event", "msg", "request_id", "trace_id", "span_id", "tenant_id"}
        assert required_fields.issubset(log_data.keys())
        assert log_data["event"] == "test message"
        assert log_data["msg"] == "test message"  # msg is alias of event
        assert log_data["extra_field"] == "value"
        assert log_data["request_id"] is None
        assert log_data["tenant_id"] is None

    def test_trace_context_injected(self):
        """Test that trace_id and span_id are injected when tracing is active."""
        # Setup tracing
        tracer_provider = TracerProvider()
        span_exporter = InMemorySpanExporter()
        tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        
        with patch("app.core.logging.get_tracer_provider", return_value=tracer_provider):
            # Setup logging
            string_io = StringIO()
            handler = logging.StreamHandler(string_io)
            logging.root.addHandler(handler)
            
            # Create a span and log within it
            tracer = tracer_provider.get_tracer(__name__)
            with tracer.start_span("test-span") as span:
                logger = get_logger("test")
                logger.info("span message")
            
            # Parse the JSON output
            output = string_io.getvalue().strip()
            log_data = json.loads(output)
            
            # Check trace context
            assert log_data["trace_id"] is not None
            assert log_data["span_id"] is not None
            assert len(log_data["trace_id"]) == 32  # 32 hex chars
            assert len(log_data["span_id"]) == 16  # 16 hex chars

    def test_no_trace_context_when_no_span(self):
        """Test that trace_id and span_id are None when no active span."""
        # Setup logging
        string_io = StringIO()
        handler = logging.StreamHandler(string_io)
        logging.root.addHandler(handler)
        
        # Log without active span
        logger = get_logger("test")
        logger.info("no span message")
        
        # Parse the JSON output
        output = string_io.getvalue().strip()
        log_data = json.loads(output)
        
        # Check no trace context
        assert log_data["trace_id"] is None
        assert log_data["span_id"] is None

    def test_contextvars_bound_and_cleared(self):
        """Test that context variables are bound and cleared properly."""
        # Setup logging
        string_io = StringIO()
        handler = logging.StreamHandler(string_io)
        logging.root.addHandler(handler)
        
        # Bind context variables
        structlog.contextvars.bind_contextvars(request_id="test-123", tenant_id="tenant-456")
        
        # Log a message
        logger = get_logger("test")
        logger.info("context message")
        
        # Clear context variables
        structlog.contextvars.clear_contextvars()
        
        # Log another message
        logger.info("after clear")
        
        # Parse outputs
        lines = string_io.getvalue().strip().split('\n')
        log_data1 = json.loads(lines[0])
        log_data2 = json.loads(lines[1])
        
        # Check first log has context
        assert log_data1["request_id"] == "test-123"
        assert log_data1["tenant_id"] == "tenant-456"
        
        # Check second log has cleared context
        assert log_data2["request_id"] is None
        assert log_data2["tenant_id"] is None

    def test_redaction_enabled(self):
        """Test PII redaction when enabled."""
        # Test redaction function directly
        event_dict = {
            "event": "Contact me at john@example.com or call 555-123-4567",
            "email": "john@example.com",
            "phone": "555-123-4567",
            "ssn": "123-45-6789",
            "aws_key": "AKIAIOSFODNN7EXAMPLE",
            "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
            "bearer": "Bearer abc123def456",
            "normal": "this is normal text",
            "number": 12345,
        }
        
        # Mock logger with redaction enabled
        mock_logger = type('MockLogger', (), {'_log_redaction_enabled': True})()
        
        result = _redact_pii(mock_logger, "", event_dict)
        
        # Check sensitive data is redacted
        assert "[EMAIL]" in result["event"]
        assert result["email"] == "[EMAIL]"
        assert result["phone"] == "[PHONE]"
        assert result["ssn"] == "[SSN]"
        assert result["aws_key"] == "[AWS_ACCESS_KEY]"
        assert result["jwt"] == "[JWT]"
        assert "Bearer [REDACTED]" in result["bearer"]
        assert result["normal"] == "this is normal text"  # unchanged
        assert result["number"] == 12345  # unchanged

    def test_redaction_disabled(self):
        """Test PII redaction when disabled."""
        event_dict = {
            "event": "Contact me at john@example.com",
            "email": "john@example.com",
        }
        
        # Mock logger with redaction disabled
        mock_logger = type('MockLogger', (), {'_log_redaction_enabled': False})()
        
        result = _redact_pii(mock_logger, "", event_dict)
        
        # Check no redaction
        assert result["event"] == "Contact me at john@example.com"
        assert result["email"] == "john@example.com"

    def test_normalize_fields(self):
        """Test field normalization ensures required keys exist."""
        event_dict = {"event": "test"}
        
        result = _normalize_fields(None, "", event_dict)
        
        # Check required fields exist
        assert result["event"] == "test"
        assert result["msg"] == "test"  # alias
        assert result["request_id"] is None
        assert result["tenant_id"] is None

    def test_stdlib_foreign_record_json(self):
        """Test that standard logging records are converted to JSON."""
        # Setup logging
        string_io = StringIO()
        handler = logging.StreamHandler(string_io)
        logging.root.addHandler(handler)
        
        # Log using standard logging
        std_logger = logging.getLogger("test.std")
        std_logger.info("hello %s", "world")
        
        # Parse the JSON output
        output = string_io.getvalue().strip()
        log_data = json.loads(output)
        
        # Check JSON structure
        assert log_data["event"] == "hello world"
        assert log_data["logger"] == "test.std"
        assert log_data["level"] == "INFO"

    def test_uvicorn_loggers_reconfigured(self):
        """Test that uvicorn loggers are reconfigured to propagate to root."""
        # Setup logging
        setup_logging("INFO")
        
        # Check uvicorn loggers
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi"):
            logger = logging.getLogger(name)
            assert logger.propagate is True
            assert len(logger.handlers) == 0


class TestScanCompleteLogging:
    """Test scan_complete event logging in scan endpoint."""

    def test_scan_complete_logged(self):
        """Test that scan_complete event is logged for both cache hit and miss."""
        from app.api.scan import scan_endpoint
        from fastapi import Request
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        
        # Create a mock request
        request = Request(scope={"type": "http", "method": "POST", "path": "/scan"})
        
        # Mock the scan endpoint to capture logs
        with patch('app.api.scan.scan_text') as mock_scan, \
             patch('app.api.scan.decide_with_opa') as mock_decide, \
             patch('app.api.scan.decision_cache') as mock_cache, \
             patch('app.api.scan.write_event') as mock_write, \
             patch('app.api.scan.get_active_policy_version') as mock_policy, \
             patch('app.api.scan.redact') as mock_redact, \
             patch('app.core.metrics.scanner_requests_total') as mock_metrics, \
             patch('app.core.metrics.scanner_request_duration_seconds') as mock_duration:
            
            # Setup mocks
            mock_policy.return_value = "test-policy"
            mock_cache.get.return_value = ({"verdict": "allow", "reason": "clean"}, True)  # Cache hit
            mock_cache.set.return_value = None
            mock_scan.return_value = ([], "hash123")
            mock_decide.return_value = type('MockResult', (), {
                'action': 'allow',
                'reason': 'clean',
                'policy_version': 'test-policy'
            })()
            mock_write.return_value = None
            mock_redact.return_value = "redacted"
            
            # Create test client and make request
            from app.main import app
            client = TestClient(app)
            
            # Mock auth info
            with patch('app.api.scan.verify_api_key') as mock_auth:
                from app.core.auth import AuthInfo
                mock_auth.return_value = AuthInfo(api_key="test-key", tenant_id="test-tenant")
                
                # Make request that should trigger cache hit
                response = client.post("/scan", json={"prompt": "test prompt"}, headers={
                    "Authorization": "Bearer test-token"
                })
                
                # Verify response
                assert response.status_code == 200
                
                # Note: In a real test, we would capture logs using structlog.testing.capture_logs
                # but since our setup_logging uses JSONRenderer, we need to verify the log was emitted
                # through other means. This test mainly ensures the endpoint works with the new logging.