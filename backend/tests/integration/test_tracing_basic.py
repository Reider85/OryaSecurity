"""Test OpenTelemetry tracing functionality."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.main import app
from app.core.observability import init_telemetry, shutdown_telemetry, get_tracer


def test_tracer_basic_functionality():
    """Test that basic tracer functionality works."""
    exporter = InMemorySpanExporter()
    init_telemetry(app, span_exporter=exporter)
    
    try:
        # Create a span manually
        tracer = get_tracer("test")
        with tracer.start_span("test_span") as span:
            span.set_attribute("test_key", "test_value")
            span.set_attribute("tenant_id", "test-tenant")
            span.set_attribute("request_id", "test-request")
        
        # Check that spans were created
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "test_span"
        assert span.attributes.get("test_key") == "test_value"
        assert span.attributes.get("tenant_id") == "test-tenant"
        assert span.attributes.get("request_id") == "test-request"
        
    finally:
        shutdown_telemetry()


def test_observability_spans():
    """Test that observability spans are created correctly."""
    exporter = InMemorySpanExporter()
    init_telemetry(app, span_exporter=exporter)
    
    try:
        # Test that spans are created with correct attributes
        tracer = get_tracer("observability_test")
        
        with tracer.start_span("observability_span") as span:
            span.set_attribute("service.name", "llm-security-scanner")
            span.set_attribute("service.version", "0.2.0")
            span.set_attribute("tenant_id", "test-tenant")
            span.set_attribute("request_id", "test-request")
        
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "observability_span"
        assert span.attributes.get("service.name") == "llm-security-scanner"
        assert span.attributes.get("service.version") == "0.2.0"
        assert span.attributes.get("tenant_id") == "test-tenant"
        assert span.attributes.get("request_id") == "test-request"
        
    finally:
        shutdown_telemetry()


def test_multiple_spans():
    """Test that multiple spans can be created."""
    exporter = InMemorySpanExporter()
    init_telemetry(app, span_exporter=exporter)
    
    try:
        tracer = get_tracer("test")
        
        # Create multiple spans
        with tracer.start_span("span1") as span1:
            span1.set_attribute("key", "value1")
            
        with tracer.start_span("span2") as span2:
            span2.set_attribute("key", "value2")
            
        with tracer.start_span("span3") as span3:
            span3.set_attribute("key", "value3")
        
        spans = exporter.get_finished_spans()
        assert len(spans) == 3
        
        # Check all spans
        span_names = [s.name for s in spans]
        assert "span1" in span_names
        assert "span2" in span_names
        assert "span3" in span_names
        
    finally:
        shutdown_telemetry()


def test_span_error_handling():
    """Test that spans handle errors correctly."""
    exporter = InMemorySpanExporter()
    init_telemetry(app, span_exporter=exporter)
    
    try:
        tracer = get_tracer("test")
        
        # Create a span that will have an exception
        try:
            with tracer.start_span("error_span") as span:
                span.set_attribute("test", "error")
                # Simulate an error
                raise ValueError("Test error")
        except ValueError:
            pass  # Expected
    
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.status.status_code == 2  # ERROR
        assert span.status.description == "Test error"
        
    finally:
        shutdown_telemetry()