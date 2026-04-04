#!/usr/bin/env python3
# pyright: reportMissingModuleSource=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportUnknownParameterType=false, reportReturnType=false
"""
Centralised observability for AL-5G-AE.

Provides:
  1. **OpenTelemetry tracing** — initialise once, then import the tracer
     in any module.  Exports to an OTLP endpoint (Jaeger, Tempo, etc.).
  2. **Structured JSON logging** — drop-in replacement for the default
     ``logging.Formatter`` so logs are machine-readable (ELK / Loki).
  3. **Prometheus metrics helpers** — thin wrappers so every interface
     records the same counters/histograms.

All heavy dependencies are lazily imported so modules that don't enable
observability pay zero startup cost.

Environment variables
---------------------
OTEL_EXPORTER_OTLP_ENDPOINT  – OTLP gRPC endpoint (default: http://localhost:4317)
OTEL_SERVICE_NAME            – service name tag    (default: al-5g-ae)
AL5GAE_LOG_FORMAT            – "json" for structured logs, anything else for plain
AL5GAE_LOG_LEVEL             – DEBUG / INFO / WARNING / ERROR (default: INFO)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OTEL_ENDPOINT: str = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
SERVICE_NAME: str = os.environ.get("OTEL_SERVICE_NAME", "al-5g-ae")
LOG_FORMAT: str = os.environ.get("AL5GAE_LOG_FORMAT", "plain")
LOG_LEVEL: str = os.environ.get("AL5GAE_LOG_LEVEL", "INFO")

# ---------------------------------------------------------------------------
# Lazy OpenTelemetry state
# ---------------------------------------------------------------------------
_otel_initialised: bool = False
_tracer_provider: Any = None
_noop_tracer: Optional[Any] = None
_noop_span: Optional[Any] = None

# Prometheus metrics (shared across modules)
_prom_available: Optional[bool] = None
_query_counter: Any = None
_query_duration: Any = None
_rag_hit_counter: Any = None
_error_counter: Any = None


# ===================================================================
# 1. OpenTelemetry tracing
# ===================================================================

def _try_init_otel() -> bool:
    """Attempt to bootstrap the OTEL tracer provider.  Returns True on success."""
    global _otel_initialised, _tracer_provider
    if _otel_initialised:
        return _tracer_provider is not None
    _otel_initialised = True
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        resource = Resource.create({"service.name": SERVICE_NAME})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer_provider = provider
        return True
    except Exception:
        # OTEL libs not installed or endpoint unreachable — degrade gracefully.
        return False


class _NoopSpan:
    """Minimal stand-in when OTEL is unavailable."""
    def set_attribute(self, key: str, value: Any) -> None: ...
    def set_status(self, status: Any) -> None: ...
    def record_exception(self, exc: BaseException) -> None: ...
    def __enter__(self) -> "_NoopSpan":
        return self
    def __exit__(self, *args: Any) -> None: ...


class _NoopTracer:
    """Returns noop spans so callers don't need ``if tracer:`` guards."""
    def start_as_current_span(self, name: str, **kwargs: Any) -> _NoopSpan:
        return _NoopSpan()


def get_tracer(name: str = "al-5g-ae") -> Any:
    """Return an OpenTelemetry ``Tracer`` (or a silent noop if OTEL is not installed)."""
    global _noop_tracer
    if _try_init_otel():
        from opentelemetry import trace
        return trace.get_tracer(name)
    if _noop_tracer is None:
        _noop_tracer = _NoopTracer()
    return _noop_tracer


# ===================================================================
# 2. Structured (JSON) logging
# ===================================================================

class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line.

    Fields: ``timestamp``, ``level``, ``logger``, ``message``, ``module``,
    ``funcName``, ``lineno``, and optionally ``trace_id`` / ``span_id``
    (injected automatically when OTEL is active).
    """

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "funcName": record.funcName,
            "lineno": record.lineno,
        }

        # Inject OTEL trace/span IDs if available
        try:
            from opentelemetry import trace
            ctx = trace.get_current_span().get_span_context()
            if ctx and ctx.trace_id:
                doc["trace_id"] = format(ctx.trace_id, "032x")
                doc["span_id"] = format(ctx.span_id, "016x")
        except Exception:
            pass

        if record.exc_info and record.exc_info[1] is not None:
            doc["exception"] = traceback.format_exception(*record.exc_info)

        return json.dumps(doc, default=str)


def configure_logging(
    name: str = "al_5g_ae",
    *,
    level: Optional[str] = None,
    log_format: Optional[str] = None,
    log_path: Optional[str] = None,
) -> logging.Logger:
    """Configure (or reconfigure) the root logger for a given module.

    Parameters
    ----------
    name : str
        Logger name.
    level : str | None
        Override ``AL5GAE_LOG_LEVEL`` env var.
    log_format : str | None
        ``"json"`` for structured output, anything else for plain.
        Defaults to ``AL5GAE_LOG_FORMAT`` env var.
    log_path : str | None
        Optional file path for a ``FileHandler``.
    """
    logger = logging.getLogger(name)
    resolved_level = getattr(logging, (level or LOG_LEVEL).upper(), logging.INFO)
    logger.setLevel(resolved_level)
    logger.propagate = False

    # Avoid adding duplicate handlers on repeated calls
    if logger.handlers:
        return logger

    fmt = (log_format or LOG_FORMAT).lower().strip()
    if fmt == "json":
        formatter: logging.Formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    sh = logging.StreamHandler(stream=sys.stderr)
    sh.setLevel(resolved_level)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    if log_path:
        from pathlib import Path
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger


# ===================================================================
# 3. Prometheus metrics helpers
# ===================================================================

def _ensure_prom() -> bool:
    global _prom_available, _query_counter, _query_duration, _rag_hit_counter, _error_counter
    if _prom_available is not None:
        return _prom_available
    try:
        from prometheus_client import Counter, Histogram
        _query_counter = Counter(
            "al5gae_queries_total",
            "Total queries across all interfaces",
            ["interface"],
        )
        _query_duration = Histogram(
            "al5gae_query_duration_seconds",
            "End-to-end query latency",
            ["interface"],
        )
        _rag_hit_counter = Counter(
            "al5gae_rag_retrievals_total",
            "Number of RAG retrievals",
            ["interface"],
        )
        _error_counter = Counter(
            "al5gae_errors_total",
            "Errors during query processing",
            ["interface"],
        )
        _prom_available = True
    except ImportError:
        _prom_available = False
    return _prom_available


def record_query(interface: str) -> None:
    """Increment the per-interface query counter."""
    if _ensure_prom():
        _query_counter.labels(interface=interface).inc()


def record_query_duration(interface: str, seconds: float) -> None:
    """Observe query latency for the given interface."""
    if _ensure_prom():
        _query_duration.labels(interface=interface).observe(seconds)


def record_rag_retrieval(interface: str) -> None:
    """Increment the RAG-retrieval counter."""
    if _ensure_prom():
        _rag_hit_counter.labels(interface=interface).inc()


def record_error(interface: str) -> None:
    """Increment the error counter."""
    if _ensure_prom():
        _error_counter.labels(interface=interface).inc()


class QueryTimer:
    """Context manager that records query duration to Prometheus + OTEL span."""

    def __init__(self, interface: str, tracer: Any = None, span_name: str = "query"):
        self.interface = interface
        self.tracer = tracer
        self.span_name = span_name
        self._start: float = 0.0
        self._span: Any = None

    def __enter__(self) -> "QueryTimer":
        self._start = time.monotonic()
        if self.tracer and hasattr(self.tracer, "start_as_current_span"):
            self._span = self.tracer.start_as_current_span(self.span_name)
            self._span.__enter__()
        record_query(self.interface)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        elapsed = time.monotonic() - self._start
        record_query_duration(self.interface, elapsed)
        if exc_type is not None:
            record_error(self.interface)
        if self._span is not None:
            if exc_val is not None:
                self._span.record_exception(exc_val)
            self._span.__exit__(exc_type, exc_val, exc_tb)
