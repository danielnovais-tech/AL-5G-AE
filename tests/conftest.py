#!/usr/bin/env python3
# pyright: reportMissingModuleSource=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownParameterType=false
# pyright: reportUnknownArgumentType=false, reportReturnType=false
# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false
# pyright: reportUnusedImport=false
"""
Shared helpers for the AL-5G-AE test suite.

Provides:
  - Synthetic data generators (PCAPs, logs, knowledge-base docs).
  - Lightweight mock model and tokenizer for offline testing.
  - Temporary directory fixtures.
"""

from __future__ import annotations

import json
import os
import struct
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Mock model / tokenizer
# ---------------------------------------------------------------------------

class MockTokenizer:
    """Minimal tokenizer mock matching the HuggingFace interface."""

    pad_token: Optional[str] = "<pad>"
    eos_token: str = "<eos>"
    pad_token_id: int = 0
    eos_token_id: int = 1

    def __call__(
        self,
        text: str,
        return_tensors: str = "pt",
        truncation: bool = True,
        max_length: int = 2048,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        # Return a simple dict with input_ids as a list of ints
        tokens = text.split()[:max_length]
        ids = list(range(len(tokens)))
        return {"input_ids": _FakeTensor([ids]), "attention_mask": _FakeTensor([[1] * len(ids)])}

    def decode(self, ids: Any, skip_special_tokens: bool = True) -> str:
        return "This is a mock response about 5G Core troubleshooting."

    def batch_decode(self, ids: Any, **kwargs: Any) -> List[str]:
        return [self.decode(i) for i in ids]


class _FakeTensor:
    """Minimal tensor-like object for tests that don't need real torch."""

    def __init__(self, data: Any) -> None:
        self.data = data
        self.shape = [len(data)] if isinstance(data, list) else [1, 1]

    def to(self, device: str = "cpu") -> "_FakeTensor":
        return self

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, key: Any) -> Any:
        return self.data[key]


class MockModel:
    """Minimal model mock matching the HuggingFace generate() interface."""

    def generate(self, input_ids: Any, **kwargs: Any) -> Any:
        # Return a fake tensor with 10 token ids
        return _FakeTensor([[0, 1, 2, 3, 4, 5, 6, 7, 8, 9]])

    def to(self, device: str = "cpu") -> "MockModel":
        return self

    def eval(self) -> "MockModel":
        return self


# ---------------------------------------------------------------------------
# Synthetic data generators
# ---------------------------------------------------------------------------

# Minimal global PCAP header (little-endian)
_PCAP_MAGIC = 0xA1B2C3D4
_PCAP_VERSION_MAJOR = 2
_PCAP_VERSION_MINOR = 4
_PCAP_SNAPLEN = 65535
_PCAP_LINKTYPE_ETHERNET = 1


def _pcap_global_header() -> bytes:
    """Return a valid pcap global header."""
    return struct.pack(
        "<IHHIIII",
        _PCAP_MAGIC,
        _PCAP_VERSION_MAJOR,
        _PCAP_VERSION_MINOR,
        0,  # timezone correction
        0,  # timestamp accuracy
        _PCAP_SNAPLEN,
        _PCAP_LINKTYPE_ETHERNET,
    )


def _pcap_packet_record(raw: bytes, ts_sec: int = 1700000000, ts_usec: int = 0) -> bytes:
    """Wrap raw bytes in a pcap per-packet header."""
    return struct.pack("<IIII", ts_sec, ts_usec, len(raw), len(raw)) + raw


def _make_ethernet_ip_udp(
    *,
    src_ip: str = "10.0.0.1",
    dst_ip: str = "10.0.0.2",
    sport: int = 12345,
    dport: int = 8805,
    payload: bytes = b"\x20\x01\x00\x00",
) -> bytes:
    """Build a minimal Ethernet + IP + UDP frame (no checksums)."""
    # Ethernet: dst(6) src(6) type(2) = 14 bytes
    eth = b"\x00" * 6 + b"\x00" * 6 + b"\x08\x00"
    # IP: version+ihl, tos, total_len, id, flags+frag, ttl, proto(17=UDP), checksum, src, dst
    udp_len = 8 + len(payload)
    ip_total = 20 + udp_len
    ip_parts = list(map(int, src_ip.split(".")))
    ip_dst_parts = list(map(int, dst_ip.split(".")))
    ip = struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0, ip_total, 0, 0, 64, 17, 0,
        bytes(ip_parts), bytes(ip_dst_parts),
    )
    # UDP: sport, dport, length, checksum
    udp = struct.pack("!HHHH", sport, dport, udp_len, 0) + payload
    return eth + ip + udp


def _make_ethernet_ip_tcp(
    *,
    src_ip: str = "10.0.0.1",
    dst_ip: str = "10.0.0.2",
    sport: int = 12345,
    dport: int = 80,
    seq: int = 1000,
    ack: int = 0,
    flags: int = 0x02,  # SYN
    payload: bytes = b"",
) -> bytes:
    """Build a minimal Ethernet + IP + TCP frame (no checksums)."""
    eth = b"\x00" * 6 + b"\x00" * 6 + b"\x08\x00"
    tcp_header_len = 20
    ip_total = 20 + tcp_header_len + len(payload)
    ip_parts = list(map(int, src_ip.split(".")))
    ip_dst_parts = list(map(int, dst_ip.split(".")))
    ip = struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0, ip_total, 0, 0, 64, 6, 0,
        bytes(ip_parts), bytes(ip_dst_parts),
    )
    data_offset = (tcp_header_len // 4) << 4
    tcp = struct.pack(
        "!HHIIBBHHH",
        sport, dport, seq, ack, data_offset, flags, 65535, 0, 0,
    ) + payload
    return eth + ip + tcp


def create_synthetic_pcap(path: str, *, num_packets: int = 10) -> str:
    """Write a minimal valid pcap with synthetic UDP + TCP packets."""
    data = _pcap_global_header()
    for i in range(num_packets):
        if i % 2 == 0:
            frame = _make_ethernet_ip_udp(
                sport=12345 + i, dport=8805 if i % 4 == 0 else 2123,
                payload=b"\x20\x01" + bytes([i]),
            )
        else:
            frame = _make_ethernet_ip_tcp(
                sport=40000 + i, dport=80, seq=1000 * i,
                payload=b"GET / HTTP/1.1\r\n\r\n",
            )
        data += _pcap_packet_record(frame, ts_sec=1700000000 + i)
    Path(path).write_bytes(data)
    return path


def create_synthetic_logs(path: str, *, num_lines: int = 50) -> str:
    """Write synthetic timestamped log entries."""
    lines = []
    for i in range(num_lines):
        ts = f"2026-04-03T10:{i // 60:02d}:{i % 60:02d}.000Z"
        level = ["INFO", "WARNING", "ERROR"][i % 3]
        component = ["AMF", "SMF", "UPF", "NRF", "PCF"][i % 5]
        lines.append(f"{ts} {level} [{component}] Sample log entry {i}: session={i * 100}")
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return path


def create_synthetic_kb(dirpath: str) -> str:
    """Create a minimal knowledge-base directory with .txt files."""
    kb = Path(dirpath)
    kb.mkdir(parents=True, exist_ok=True)

    (kb / "5gc-overview.txt").write_text(
        "5G Core Architecture Overview\n\n"
        "The 5G Core (5GC) uses a Service-Based Architecture (SBA).\n"
        "Key network functions: AMF, SMF, UPF, NRF, PCF, NSSF, AUSF, UDM.\n"
        "The AMF handles access and mobility management.\n"
        "The SMF manages session establishment via PFCP toward the UPF.\n"
        "The UPF handles user-plane forwarding (GTP-U tunnels).\n",
        encoding="utf-8",
    )

    (kb / "protocols.txt").write_text(
        "5G Protocol Reference\n\n"
        "NGAP: NG Application Protocol between gNB and AMF (SCTP port 38412).\n"
        "PFCP: Packet Forwarding Control Protocol between SMF and UPF (UDP port 8805).\n"
        "GTPv2-C: GPRS Tunnelling Protocol v2 Control (UDP port 2123).\n"
        "GTP-U: User-plane tunnelling (UDP port 2152).\n"
        "HTTP/2 SBI: Service-Based Interface between NFs (ports 29510-29518).\n"
        "NAS-5GS: Non-Access Stratum signalling carried over NGAP.\n",
        encoding="utf-8",
    )

    (kb / "troubleshooting.txt").write_text(
        "Common 5GC Troubleshooting\n\n"
        "Registration failure: check AMF logs for NAS reject causes.\n"
        "PDU session failure: verify SMF-UPF PFCP association, check N4 interface.\n"
        "Handover failure: inspect NGAP PathSwitchRequest in AMF, UPF N3 tunnel update.\n"
        "Service discovery failure: check NRF availability, NF profile registration.\n"
        "High latency: check UPF forwarding rules, GTP-U tunnel overhead.\n",
        encoding="utf-8",
    )

    return dirpath


def create_alertmanager_payload(
    *,
    alertname: str = "PFCPAssociationDown",
    instance: str = "smf-01:8805",
    severity: str = "critical",
    summary: str = "PFCP association lost",
    description: str = "SMF lost PFCP heartbeat to UPF on N4 interface.",
    status: str = "firing",
) -> Dict[str, Any]:
    """Return a synthetic Alertmanager webhook payload."""
    return {
        "status": status,
        "alerts": [
            {
                "status": status,
                "labels": {
                    "alertname": alertname,
                    "instance": instance,
                    "severity": severity,
                },
                "annotations": {
                    "summary": summary,
                    "description": description,
                },
                "startsAt": "2026-04-03T10:00:00.000Z",
                "endsAt": "0001-01-01T00:00:00Z",
            }
        ],
    }


def create_tshark_ek_output(*, num_packets: int = 5) -> str:
    """Return synthetic tshark -T ek NDJSON output."""
    lines = []
    for i in range(num_packets):
        pkt: Dict[str, Any] = {
            "index": {"_index": "packets", "_type": "doc"},
        }
        layers: Dict[str, Any] = {
            "layers": {
                "frame": {"frame_frame_number": str(i + 1)},
                "ip": {
                    "ip_ip_src": "10.0.0.1",
                    "ip_ip_dst": "10.0.0.2",
                },
                "udp": {
                    "udp_udp_srcport": "12345",
                    "udp_udp_dstport": "8805",
                },
            }
        }
        lines.append(json.dumps(pkt))
        lines.append(json.dumps(layers))
    return "\n".join(lines)


def create_temp_dir() -> str:
    """Create and return a temporary directory path."""
    return tempfile.mkdtemp(prefix="al5gae_test_")
