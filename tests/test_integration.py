#!/usr/bin/env python3
# pyright: reportMissingModuleSource=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownParameterType=false
# pyright: reportUnknownArgumentType=false, reportReturnType=false
# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false
# pyright: reportUntypedFunctionDecorator=false, reportAttributeAccessIssue=false
# pyright: reportUnusedImport=false, reportCallIssue=false
"""
Integration tests for AL-5G-AE with mock 5G core simulators.

Tests end-to-end flows:
  - Query + RAG pipeline (model mocked)
  - API server endpoints (TestClient)
  - Prometheus bridge webhook handling
  - Root-cause correlation pipeline
  - Realtime 5GC clients (mocked gNMI / RESTCONF / Kafka)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.conftest import (
    MockModel,
    MockTokenizer,
    create_alertmanager_payload,
    create_synthetic_kb,
    create_synthetic_pcap,
    create_temp_dir,
)


# ===================================================================
# End-to-end query + RAG pipeline
# ===================================================================

class TestQueryPipeline:
    """Test the full question → RAG retrieve → generate_response flow."""

    @pytest.fixture(autouse=True)
    def _check_rag_deps(self) -> None:
        try:
            import faiss  # noqa: F401
            import sentence_transformers  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            pytest.skip("RAG dependencies not installed")

    def test_full_pipeline(self) -> None:
        from al_5g_ae_core import RAG, generate_response

        tmp = create_temp_dir()
        try:
            kb = create_synthetic_kb(tmp)
            rag = RAG(rerank=False, hybrid=False, contextual_compression=False)
            for f in Path(kb).glob("*.txt"):
                rag.add_file(str(f))

            # Retrieve context
            context = rag.retrieve("What is PFCP?", k=3)
            assert len(context) > 0
            assert any("PFCP" in c or "8805" in c for c in context)

            # Generate response with mock model
            tok = MockTokenizer()
            mdl = MockModel()
            answer = generate_response(tok, mdl, "What is PFCP?", context)
            assert isinstance(answer, str)
            assert len(answer) > 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_pcap_to_rag_pipeline(self) -> None:
        """PCAP → summaries → RAG index → retrieve."""
        try:
            import scapy  # noqa: F401
        except ImportError:
            pytest.skip("scapy not installed")

        from al_5g_ae_core import RAG
        from pcap_ingest import process_pcap

        tmp = create_temp_dir()
        try:
            pcap = create_synthetic_pcap(os.path.join(tmp, "test.pcap"), num_packets=10)
            summaries = process_pcap(pcap, max_packets=10)

            rag = RAG(rerank=False, hybrid=False, contextual_compression=False)
            rag.add_documents(summaries, sources=["pcap"] * len(summaries))

            results = rag.retrieve("UDP traffic", k=2)
            assert len(results) > 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================
# API server (FastAPI TestClient)
# ===================================================================

class TestAPIServer:
    @pytest.fixture(autouse=True)
    def _check_fastapi(self) -> None:
        try:
            from fastapi.testclient import TestClient  # noqa: F401
        except ImportError:
            pytest.skip("fastapi not installed")

    def test_health_endpoint(self) -> None:
        from fastapi.testclient import TestClient
        import api_server

        # Mock the model and RAG
        api_server.tokenizer = MockTokenizer()
        api_server.model = MockModel()
        api_server.rag = None

        client = TestClient(api_server.app)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_query_endpoint(self) -> None:
        from fastapi.testclient import TestClient
        import api_server

        api_server.tokenizer = MockTokenizer()
        api_server.model = MockModel()
        api_server.rag = None

        client = TestClient(api_server.app)
        resp = client.post("/query", json={"question": "What is PFCP?"})
        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert isinstance(data["answer"], str)

    def test_query_with_rag(self) -> None:
        try:
            import faiss  # noqa: F401
            import sentence_transformers  # noqa: F401
        except ImportError:
            pytest.skip("RAG dependencies not installed")

        from fastapi.testclient import TestClient
        from al_5g_ae_core import RAG
        import api_server

        api_server.tokenizer = MockTokenizer()
        api_server.model = MockModel()

        rag = RAG(rerank=False, hybrid=False, contextual_compression=False)
        rag.add_documents(["PFCP runs on UDP port 8805 between SMF and UPF."])
        api_server.rag = rag

        client = TestClient(api_server.app)
        resp = client.post("/query", json={"question": "What port does PFCP use?"})
        assert resp.status_code == 200


# ===================================================================
# Prometheus bridge
# ===================================================================

class TestPrometheusBridge:
    @pytest.fixture(autouse=True)
    def _check_deps(self) -> None:
        try:
            from fastapi.testclient import TestClient  # noqa: F401
        except ImportError:
            pytest.skip("fastapi not installed")

    def test_health(self) -> None:
        from fastapi.testclient import TestClient
        import prometheus_bridge

        prometheus_bridge.tokenizer = MockTokenizer()
        prometheus_bridge.model = MockModel()
        prometheus_bridge.rag = None

        client = TestClient(prometheus_bridge.app)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_webhook_fires(self) -> None:
        from fastapi.testclient import TestClient
        import prometheus_bridge

        prometheus_bridge.tokenizer = MockTokenizer()
        prometheus_bridge.model = MockModel()
        prometheus_bridge.rag = None

        client = TestClient(prometheus_bridge.app)
        payload = create_alertmanager_payload()
        resp = client.post("/webhook", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_webhook_resolved_alert_skipped(self) -> None:
        from fastapi.testclient import TestClient
        import prometheus_bridge

        prometheus_bridge.tokenizer = MockTokenizer()
        prometheus_bridge.model = MockModel()
        prometheus_bridge.rag = None

        client = TestClient(prometheus_bridge.app)
        payload = create_alertmanager_payload(status="resolved")
        resp = client.post("/webhook", json=payload)
        assert resp.status_code == 200

    def test_metrics_endpoint(self) -> None:
        from fastapi.testclient import TestClient
        import prometheus_bridge

        client = TestClient(prometheus_bridge.app)
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "al5gae" in resp.text or "HELP" in resp.text


# ===================================================================
# Root cause correlator (mocked data sources)
# ===================================================================

class TestRootCauseCorrelator:
    def test_alert_ingestion(self) -> None:
        from realtime_5gc import RootCauseCorrelator
        rcc = RootCauseCorrelator()
        payload = create_alertmanager_payload()
        count = rcc.add_alerts(payload["alerts"])
        assert count == 1
        assert len(rcc.events) == 1
        assert "PFCP" in rcc.events[0].summary

    def test_log_ingestion(self) -> None:
        from realtime_5gc import RootCauseCorrelator
        rcc = RootCauseCorrelator()
        logs = [
            "2026-04-03T10:00:00Z ERROR [SMF] PFCP heartbeat timeout",
            "2026-04-03T10:00:01Z WARNING [UPF] N4 association lost",
        ]
        count = rcc.add_logs(logs)
        assert count == 2
        assert len(rcc.events) == 2

    def test_pcap_summary_ingestion(self) -> None:
        from realtime_5gc import RootCauseCorrelator
        rcc = RootCauseCorrelator()
        summaries = [
            "[PFCP] 1700000000.0 10.0.0.1:8805 → 10.0.0.2:8805 Heartbeat Request",
            "[GTPv2-C] 1700000001.0 10.0.0.1:2123 → 10.0.0.3:2123 Create Session",
        ]
        count = rcc.add_pcap_summaries(summaries)
        assert count == 2

    def test_timeline_building(self) -> None:
        from realtime_5gc import RootCauseCorrelator
        rcc = RootCauseCorrelator()
        rcc.add_alerts(create_alertmanager_payload()["alerts"])
        rcc.add_logs([
            "2026-04-03T09:59:50Z INFO [SMF] PFCP heartbeat sent",
            "2026-04-03T10:00:10Z ERROR [SMF] No heartbeat response",
        ])
        timeline = rcc.build_timeline()
        assert isinstance(timeline, str)
        assert "PFCP" in timeline
        assert len(rcc.events) == 3

    def test_analyse_with_mock_model(self) -> None:
        from realtime_5gc import RootCauseCorrelator
        rcc = RootCauseCorrelator()
        rcc.add_alerts(create_alertmanager_payload()["alerts"])
        rcc.add_logs(["2026-04-03T10:00:00Z ERROR PFCP timeout"])

        tok = MockTokenizer()
        mdl = MockModel()
        result = rcc.analyse(tok, mdl, rag=None)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_clear(self) -> None:
        from realtime_5gc import RootCauseCorrelator
        rcc = RootCauseCorrelator()
        rcc.add_logs(["line1", "line2"])
        assert len(rcc.events) == 2
        rcc.clear()
        assert len(rcc.events) == 0


# ===================================================================
# Telemetry event normalisation
# ===================================================================

class TestTelemetryNormalisation:
    def test_metric_event(self) -> None:
        from realtime_5gc import _normalise_event
        raw = {
            "timestamp": "2026-04-03T10:00:00Z",
            "source": "upf-01",
            "metric_name": "cpu_usage",
            "value": 85.2,
        }
        event = _normalise_event(raw)
        assert event.event_type == "metric"
        assert "cpu_usage" in event.content
        assert event.source == "upf-01"

    def test_log_event(self) -> None:
        from realtime_5gc import _normalise_event
        raw = {
            "timestamp": "2026-04-03T10:00:00Z",
            "host": "smf-01",
            "severity": "ERROR",
            "message": "PFCP heartbeat timeout",
        }
        event = _normalise_event(raw)
        assert event.event_type == "log"
        assert "PFCP" in event.content

    def test_trace_event(self) -> None:
        from realtime_5gc import _normalise_event
        raw = {
            "timestamp": "2026-04-03T10:00:00Z",
            "traceId": "abc123",
            "serviceName": "amf",
            "operationName": "registration",
            "duration": 42,
        }
        event = _normalise_event(raw)
        assert event.event_type == "trace"
        assert "registration" in event.content

    def test_unknown_event_falls_to_log(self) -> None:
        from realtime_5gc import _normalise_event
        raw = {"foo": "bar", "baz": 123}
        event = _normalise_event(raw)
        assert event.event_type == "log"


# ===================================================================
# Mock gNMI client
# ===================================================================

class TestGNMIClientMocked:
    def test_get_as_text(self) -> None:
        from realtime_5gc import GNMIClient

        client = GNMIClient(target="mock:57400")

        mock_response = {
            "notification": [
                {
                    "timestamp": "2026-04-03T10:00:00Z",
                    "update": [
                        {"path": "/interfaces/interface[name=N3]", "val": {"status": "up"}},
                    ],
                }
            ]
        }
        with patch.object(client, "get", return_value=mock_response):
            text = client.get_as_text(["/interfaces/interface"])
            assert "N3" in text
            assert "up" in text


# ===================================================================
# Mock RESTCONF client
# ===================================================================

class TestRESTCONFClientMocked:
    def test_get_5gc_state(self) -> None:
        from realtime_5gc import RESTCONFClient

        client = RESTCONFClient(base_url="https://mock:443")

        mock_resp = {"amf": {"ue-count": 42, "active-sessions": 10}}
        with patch.object(client, "get", return_value=mock_resp):
            text = client.get_5gc_state("amf-sessions")
            assert "42" in text

    def test_unknown_nf_type(self) -> None:
        from realtime_5gc import RESTCONFClient

        client = RESTCONFClient(base_url="https://mock:443")
        text = client.get_5gc_state("unknown-nf")
        assert "Unknown NF type" in text


# ===================================================================
# Mock Kafka consumer
# ===================================================================

class TestTelemetryConsumerMocked:
    def test_consume_with_rag(self) -> None:
        try:
            import faiss  # noqa: F401
            import sentence_transformers  # noqa: F401
        except ImportError:
            pytest.skip("RAG dependencies not installed")

        from al_5g_ae_core import RAG
        from realtime_5gc import TelemetryConsumer, TelemetryEvent

        rag = RAG(rerank=False, hybrid=False, contextual_compression=False)
        consumer = TelemetryConsumer(rag=rag, buffer_size=2)

        # Simulate events
        events_processed: List[TelemetryEvent] = []
        consumer.on_event = lambda e: events_processed.append(e)  # type: ignore[assignment]

        # Manually push to buffer (simulating Kafka messages)
        from realtime_5gc import _normalise_event
        raw_msgs = [
            {"timestamp": "2026-04-03T10:00:00Z", "host": "amf-01", "severity": "ERROR", "message": "NAS reject"},
            {"timestamp": "2026-04-03T10:00:01Z", "host": "smf-01", "metric_name": "pfcp_sessions", "value": 100},
        ]
        for raw in raw_msgs:
            event = _normalise_event(raw)
            consumer._buffer.append(event.to_text())
            consumer.on_event(event)  # type: ignore[misc]

        count = consumer._flush()
        assert count == 2
        assert rag.index is not None
        assert rag.index.ntotal > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
