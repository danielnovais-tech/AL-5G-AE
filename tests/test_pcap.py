#!/usr/bin/env python3
# pyright: reportMissingModuleSource=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownParameterType=false
# pyright: reportUnknownArgumentType=false, reportReturnType=false
# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false
# pyright: reportUntypedFunctionDecorator=false, reportUnusedImport=false
"""
Unit tests for pcap_ingest.py and pcap_stream_reassembly.py.

Uses synthetic PCAPs — no real captures needed.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.conftest import create_synthetic_pcap, create_temp_dir


# ===================================================================
# Scapy-based ingestion
# ===================================================================

class TestProcessPcapScapy:
    @pytest.fixture(autouse=True)
    def _check_scapy(self) -> None:
        try:
            import scapy  # noqa: F401
        except ImportError:
            pytest.skip("scapy not installed")

    def test_basic_ingestion(self) -> None:
        from pcap_ingest import process_pcap
        tmp = create_temp_dir()
        try:
            pcap = create_synthetic_pcap(os.path.join(tmp, "test.pcap"), num_packets=10)
            summaries = process_pcap(pcap, max_packets=10, prefer_tshark=False)
            assert isinstance(summaries, list)
            assert len(summaries) > 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_protocol_tagging(self) -> None:
        from pcap_ingest import process_pcap
        tmp = create_temp_dir()
        try:
            pcap = create_synthetic_pcap(os.path.join(tmp, "tagged.pcap"), num_packets=20)
            summaries = process_pcap(pcap, max_packets=20, prefer_tshark=False)
            text = "\n".join(summaries)
            # Should tag PFCP (port 8805) or GTPv2 (port 2123)
            assert "[PFCP]" in text or "[GTPv2-C]" in text or "UDP" in text
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_max_packets_limit(self) -> None:
        from pcap_ingest import process_pcap
        tmp = create_temp_dir()
        try:
            pcap = create_synthetic_pcap(os.path.join(tmp, "limit.pcap"), num_packets=50)
            summaries = process_pcap(pcap, max_packets=5, prefer_tshark=False)
            assert len(summaries) <= 5
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================
# Protocol label helpers
# ===================================================================

class TestProtocolLabels:
    def test_pfcp_label(self) -> None:
        from pcap_ingest import _protocol_labels_from_ports
        labels = _protocol_labels_from_ports(sport=12345, dport=8805, proto="UDP")
        assert "PFCP" in labels

    def test_gtpv2c_label(self) -> None:
        from pcap_ingest import _protocol_labels_from_ports
        labels = _protocol_labels_from_ports(sport=2123, dport=50000, proto="UDP")
        assert "GTPv2-C" in labels

    def test_gtpu_label(self) -> None:
        from pcap_ingest import _protocol_labels_from_ports
        labels = _protocol_labels_from_ports(sport=12345, dport=2152, proto="UDP")
        assert "GTP-U" in labels

    def test_sctp_ngap(self) -> None:
        from pcap_ingest import _protocol_labels_from_ports
        labels = _protocol_labels_from_ports(sport=38412, dport=38412, proto="SCTP")
        assert "NGAP" in labels

    def test_no_label(self) -> None:
        from pcap_ingest import _protocol_labels_from_ports
        labels = _protocol_labels_from_ports(sport=50000, dport=50001, proto="UDP")
        assert labels == []


class TestLabelPrefix:
    def test_prefix_format(self) -> None:
        from pcap_ingest import _labels_to_prefix
        assert _labels_to_prefix(["PFCP"]) == "[PFCP] "
        assert _labels_to_prefix(["PFCP", "GTPv2-C"]) == "[PFCP,GTPv2-C] "

    def test_empty(self) -> None:
        from pcap_ingest import _labels_to_prefix
        assert _labels_to_prefix([]) == ""

    def test_dedup(self) -> None:
        from pcap_ingest import _labels_to_prefix
        assert _labels_to_prefix(["PFCP", "PFCP"]) == "[PFCP] "


# ===================================================================
# TCP stream reassembly
# ===================================================================

class TestStreamReassembly:
    @pytest.fixture(autouse=True)
    def _check_scapy(self) -> None:
        try:
            import scapy  # noqa: F401
        except ImportError:
            pytest.skip("scapy not installed")

    def test_reassemble_streams(self) -> None:
        from pcap_stream_reassembly import reassemble_tcp_streams, streams_to_text
        tmp = create_temp_dir()
        try:
            pcap = create_synthetic_pcap(os.path.join(tmp, "streams.pcap"), num_packets=20)
            streams = reassemble_tcp_streams(pcap, max_packets=20)
            assert isinstance(streams, dict)
            text_chunks = streams_to_text(streams)
            assert isinstance(text_chunks, list)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_protocol_tags_in_text(self) -> None:
        from pcap_stream_reassembly import streams_to_text
        # Simulate streams dict
        streams = {
            ("10.0.0.1", 40000, "10.0.0.2", 80): "GET / HTTP/1.1",
            ("10.0.0.1", 40001, "10.0.0.2", 8805): "PFCP data",
        }
        chunks = streams_to_text(streams)
        text = "\n".join(chunks)
        assert "[HTTP2/SBI]" in text or "[TCP]" in text
        assert "[PFCP]" in text


# ===================================================================
# process_pcap unified entry point
# ===================================================================

class TestProcessPcap:
    @pytest.fixture(autouse=True)
    def _check_scapy(self) -> None:
        try:
            import scapy  # noqa: F401
        except ImportError:
            pytest.skip("scapy not installed")

    def test_unified_entry(self) -> None:
        from pcap_ingest import process_pcap
        tmp = create_temp_dir()
        try:
            pcap = create_synthetic_pcap(os.path.join(tmp, "unified.pcap"), num_packets=10)
            summaries = process_pcap(pcap, max_packets=10)
            assert isinstance(summaries, list)
            assert len(summaries) > 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
