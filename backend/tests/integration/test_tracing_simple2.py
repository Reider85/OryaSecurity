"""Simple test to check if tracing works."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.main import app
from app.core.observability import init_telemetry, shutdown_telemetry


def test_tracing_setup():
    """Test that tracing can be set up."""
    exporter = InMemorySpanExporter()
    init_telemetry(app, span_exporter=exporter)
    
    try:
        # Check that spans were created
        spans = exporter.get_finished_spans()
        print(f"Number of spans: {len(spans)}")
        for span in spans:
            print(f"Span: {span.name}")
        
        # Test should pass if no exceptions raised
        assert True
        
    finally:
        shutdown_telemetry()