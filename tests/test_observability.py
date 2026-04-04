#!/usr/bin/env python3
# pyright: reportMissingModuleSource=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownParameterType=false
# pyright: reportUnknownArgumentType=false, reportReturnType=false
# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false
# pyright: reportCallIssue=false, reportUnusedImport=false, reportUnusedVariable=false
"""
Unit tests for observability.py.

Tests structured logging, noop tracer, and Prometheus metric helpers.
"""

from __future__ import annotations

import json
import logging
import sys
from io import StringIO
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from observability import (
    JSONFormatter,
    configure_logging,
    get_tracer,
    _NoopSpan,
)


# ===================================================================
# JSON Formatter
# ===================================================================

class TestJSONFormatter:
    def test_formats_as_json(self) -> None:
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="hello world", args=(), exc_info=None,
        )
        output = fmt.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "hello world"
        assert parsed["level"] == "INFO"
        assert "timestamp" in parsed

    def test_includes_exception(self) -> None:
        fmt = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys as _sys
            exc_info = _sys.exc_info()
            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname="test.py",
                lineno=1, msg="error occurred", args=(), exc_info=exc_info,
            )
            output = fmt.format(record)
            parsed = json.loads(output)
            assert "exception" in parsed
            assert "ValueError" in parsed["exception"]


# ===================================================================
# Noop tracer (when OTEL not installed)
# ===================================================================

class TestNoopSpan:
    def test_context_manager(self) -> None:
        span = _NoopSpan()
        with span as s:
            s.set_attribute("key", "value")
        # Should not raise

    def test_record_exception(self) -> None:
        span = _NoopSpan()
        span.record_exception(ValueError("test"))


class TestGetTracer:
    def test_returns_tracer(self) -> None:
        tracer = get_tracer("test_module")
        assert tracer is not None

    def test_tracer_creates_span(self) -> None:
        tracer = get_tracer("test_module")
        span = tracer.start_as_current_span("test_op")
        # Should be usable as context manager regardless of OTEL availability
        with span as s:
            pass


# ===================================================================
# Prometheus helpers
# ===================================================================

class TestPrometheusHelpers:
    def test_query_timer(self) -> None:
        try:
            from observability import QueryTimer
            timer = QueryTimer("test_interface")
            with timer:
                pass  # Simulate a fast query
            # Should not raise
        except ImportError:
            pytest.skip("prometheus_client not installed")

    def test_record_functions(self) -> None:
        try:
            from observability import record_query, record_rag_retrieval, record_error
            record_query("test")
            record_rag_retrieval("test")
            record_error("test")
        except ImportError:
            pytest.skip("prometheus_client not installed")


# ===================================================================
# Structured logging configuration
# ===================================================================

class TestConfigureLogging:
    def test_json_mode(self) -> None:
        import os
        old = os.environ.get("AL5GAE_LOG_FORMAT")
        try:
            os.environ["AL5GAE_LOG_FORMAT"] = "json"
            logger = configure_logging("test_json_log")
            assert logger is not None
        finally:
            if old is None:
                os.environ.pop("AL5GAE_LOG_FORMAT", None)
            else:
                os.environ["AL5GAE_LOG_FORMAT"] = old

    def test_plain_mode(self) -> None:
        import os
        old = os.environ.get("AL5GAE_LOG_FORMAT")
        try:
            os.environ["AL5GAE_LOG_FORMAT"] = "plain"
            logger = configure_logging("test_plain_log")
            assert logger is not None
        finally:
            if old is None:
                os.environ.pop("AL5GAE_LOG_FORMAT", None)
            else:
                os.environ["AL5GAE_LOG_FORMAT"] = old


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
