#!/usr/bin/env python3
"""
Advanced packet analysis for AL-5G-AE.

Features:
    1. pyshark + tshark live/offline dissection with display filters.
    2. TLS decryption using pre-master secret log files (SSLKEYLOGFILE).
    3. Flow-based analysis: 5-tuple aggregation, RTT estimation,
       retransmission / out-of-order detection, per-flow statistics.

All heavy dependencies (pyshark, scapy) are lazily imported so the module can
be imported even when they are not installed.
"""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownParameterType=false, reportMissingTypeStubs=false
# pyright: reportUnknownVariableType=false, reportReturnType=false

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import statistics
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pcap_advanced")


# ---------------------------------------------------------------------------
# Lazy dependency guards
# ---------------------------------------------------------------------------

_pyshark_available: Optional[bool] = None


def _ensure_pyshark() -> bool:
    global _pyshark_available
    if _pyshark_available is None:
        try:
            import pyshark as _pyshark  # noqa: F811, F401
            _ = _pyshark  # suppress unused-import warning
            _pyshark_available = True
        except ImportError:
            _pyshark_available = False
    return _pyshark_available


def _tshark_path() -> Optional[str]:
    return shutil.which("tshark")


# ---------------------------------------------------------------------------
# 1.  pyshark + tshark integration
# ---------------------------------------------------------------------------


def dissect_live(
    interface: str,
    *,
    display_filter: Optional[str] = None,
    bpf_filter: Optional[str] = None,
    timeout: int = 30,
    packet_count: int = 100,
    tls_keylog: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Capture and dissect packets live from a network interface.

    Returns a list of dicts with per-packet layer information.
    Requires pyshark + tshark on PATH.
    """
    if not _ensure_pyshark():
        raise ImportError("pyshark is required.  pip install pyshark")

    import pyshark  # type: ignore[import-untyped]

    override_prefs: Dict[str, str] = {}
    if tls_keylog and os.path.isfile(tls_keylog):
        override_prefs["tls.keylog_file"] = tls_keylog

    cap = pyshark.LiveCapture(
        interface=interface,
        display_filter=display_filter or None,
        bpf_filter=bpf_filter or None,
        override_prefs=override_prefs or None,
    )
    cap.sniff(timeout=timeout, packet_count=packet_count)

    results: List[Dict[str, Any]] = []
    for pkt in cap:
        results.append(_packet_to_dict(pkt))
    cap.close()
    return results


def dissect_file(
    pcap_path: str,
    *,
    display_filter: Optional[str] = None,
    max_packets: int = 1000,
    tls_keylog: Optional[str] = None,
    decode_as: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Dissect an offline PCAP/PCAPNG using pyshark (full Wireshark dissectors).

    Parameters
    ----------
    pcap_path : str
        Path to the capture file.
    display_filter : str, optional
        Wireshark display filter (e.g., ``"ngap || pfcp"``).
    max_packets : int
        Stop after this many packets.
    tls_keylog : str, optional
        Path to the TLS pre-master-secret log (``SSLKEYLOGFILE``).
    decode_as : dict, optional
        Protocol decode overrides, e.g. ``{"tcp.port==8080": "http2"}``.

    Returns
    -------
    list of dict
        One dict per packet with layer names → field dicts.
    """
    if not _ensure_pyshark():
        raise ImportError("pyshark is required.  pip install pyshark")

    import pyshark  # type: ignore[import-untyped]

    override_prefs: Dict[str, str] = {}
    if tls_keylog and os.path.isfile(tls_keylog):
        override_prefs["tls.keylog_file"] = tls_keylog

    cap = pyshark.FileCapture(
        pcap_path,
        display_filter=display_filter or None,
        override_prefs=override_prefs or None,
        decode_as=decode_as or None,
    )

    results: List[Dict[str, Any]] = []
    for i, pkt in enumerate(cap):
        if i >= max_packets:
            break
        results.append(_packet_to_dict(pkt))
    cap.close()
    return results


def _packet_to_dict(pkt: Any) -> Dict[str, Any]:
    """Convert a pyshark Packet object to a plain dict."""
    d: Dict[str, Any] = {
        "number": getattr(pkt, "number", None),
        "length": getattr(pkt, "length", None),
        "timestamp": str(getattr(pkt, "sniff_time", "")),
        "highest_layer": getattr(pkt, "highest_layer", ""),
        "protocols": str(getattr(pkt, "layers", [])),
        "layers": {},
    }
    for layer in pkt.layers:
        layer_name = layer.layer_name
        fields: Dict[str, str] = {}
        for fname in layer.field_names:
            try:
                fields[fname] = str(getattr(layer, fname, ""))
            except Exception:
                pass
        d["layers"][layer_name] = fields
    return d


def dissect_to_summaries(
    pcap_path: str,
    *,
    display_filter: Optional[str] = None,
    max_packets: int = 1000,
    tls_keylog: Optional[str] = None,
    decode_as: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Dissect a PCAP and return RAG-friendly text summaries (one per packet).

    This is the main entry point for the advanced dissection pipeline — it
    combines pyshark's full Wireshark dissectors with 5G-aware labelling.
    """
    packets = dissect_file(
        pcap_path,
        display_filter=display_filter,
        max_packets=max_packets,
        tls_keylog=tls_keylog,
        decode_as=decode_as,
    )

    summaries: List[str] = []
    for pkt in packets:
        layers_d: Dict[str, Any] = pkt.get("layers", {})
        ip_layer = layers_d.get("ip", layers_d.get("ipv6", {}))
        src = ip_layer.get("src", ip_layer.get("src_host", ""))
        dst = ip_layer.get("dst", ip_layer.get("dst_host", ""))

        sport = dport = ""
        transport = ""
        for proto_key in ("tcp", "udp", "sctp"):
            if proto_key in layers_d:
                transport = proto_key.upper()
                sport = layers_d[proto_key].get("srcport", "")
                dport = layers_d[proto_key].get("dstport", "")
                break

        # 5G protocol tags
        tags: List[str] = []
        if "ngap" in layers_d:
            tags.append("NGAP")
        if "pfcp" in layers_d:
            tags.append("PFCP")
        if "gtpv2" in layers_d:
            tags.append("GTPv2-C")
        if "gtp" in layers_d:
            tags.append("GTP-U")
        if "http2" in layers_d:
            tags.append("HTTP2/SBI")
        if "nas-5gs" in layers_d or "nas_5gs" in layers_d:
            tags.append("NAS-5GS")
        if "diameter" in layers_d:
            tags.append("Diameter")
        if "s1ap" in layers_d:
            tags.append("S1AP")

        tag_str = f"[{','.join(tags)}] " if tags else ""
        ports_str = f"{sport}->{dport}" if sport and dport else ""
        highest = pkt.get("highest_layer", "")

        line = f"{tag_str}pkt={pkt.get('number', '?')} {src}->{dst} {transport} {ports_str} {highest}"

        # Append key 5G fields
        extras: List[str] = []
        if "ngap" in layers_d:
            pc = layers_d["ngap"].get("procedurecode", "")
            if pc:
                extras.append(f"NGAP.procedureCode={pc}")
        if "pfcp" in layers_d:
            mt = layers_d["pfcp"].get("msg_type", "")
            if mt:
                extras.append(f"PFCP.msgType={mt}")
        if "http2" in layers_d:
            path = layers_d["http2"].get("headers_path", layers_d["http2"].get("header_value", ""))
            method = layers_d["http2"].get("headers_method", layers_d["http2"].get("method", ""))
            if method:
                extras.append(f"HTTP2.method={method}")
            if path:
                extras.append(f"HTTP2.path={path}")

        # TLS info (visible when decrypted)
        if "tls" in layers_d:
            tls_ver = layers_d["tls"].get("record_version", "")
            ct = layers_d["tls"].get("record_content_type", "")
            if tls_ver:
                extras.append(f"TLS.ver={tls_ver}")
            if ct:
                extras.append(f"TLS.contentType={ct}")

        if extras:
            line += " | " + ", ".join(extras)

        summaries.append(line.strip())

    return summaries


# ---------------------------------------------------------------------------
# 2.  TLS decryption helpers
# ---------------------------------------------------------------------------


def decrypt_pcap(
    pcap_path: str,
    keylog_file: str,
    output_path: Optional[str] = None,
    *,
    display_filter: Optional[str] = None,
) -> str:
    """Decrypt TLS traffic using a pre-master secret log file.

    Uses tshark ``-o tls.keylog_file:`` to produce decrypted output.
    Returns the path to the decrypted PCAP (pdml → re-exported).

    For most workflows, prefer passing ``tls_keylog`` to ``dissect_file()``
    or ``dissect_to_summaries()`` which decrypt inline. This helper is for
    when you want a separate decrypted capture file.
    """
    tshark = _tshark_path()
    if not tshark:
        raise FileNotFoundError("tshark not found on PATH")
    if not os.path.isfile(keylog_file):
        raise FileNotFoundError(f"Keylog file not found: {keylog_file}")

    if output_path is None:
        stem = Path(pcap_path).stem
        output_path = str(Path(pcap_path).with_name(f"{stem}_decrypted.pcapng"))

    cmd: List[str] = [
        tshark,
        "-r", pcap_path,
        "-o", f"tls.keylog_file:{keylog_file}",
        "-w", output_path,
    ]
    if display_filter:
        cmd.extend(["-Y", display_filter])

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"tshark decryption failed: {proc.stderr.strip()}")

    logger.info(f"Decrypted PCAP written to {output_path}")
    return output_path


def extract_tls_metadata(
    pcap_path: str,
    *,
    keylog_file: Optional[str] = None,
    max_packets: int = 5000,
) -> List[Dict[str, str]]:
    """Extract TLS handshake metadata (SNI, cipher suite, version, cert CN).

    Works with or without keylog. With keylog, also extracts decrypted
    application data layer info.
    """
    tshark = _tshark_path()
    if not tshark:
        raise FileNotFoundError("tshark not found on PATH")

    fields = [
        "frame.number",
        "ip.src",
        "ip.dst",
        "tcp.srcport",
        "tcp.dstport",
        "tls.handshake.type",
        "tls.handshake.extensions_server_name",
        "tls.handshake.ciphersuite",
        "tls.record.version",
        "tls.handshake.certificate",
        "x509ce.dNSName",
    ]

    cmd: List[str] = [
        tshark, "-r", pcap_path,
        "-Y", "tls",
        "-T", "fields",
        "-E", "separator=\t",
        "-E", "quote=d",
        "-c", str(max_packets),
    ]
    if keylog_file and os.path.isfile(keylog_file):
        cmd.extend(["-o", f"tls.keylog_file:{keylog_file}"])
    for f in fields:
        cmd.extend(["-e", f])

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"tshark TLS extraction failed: {proc.stderr.strip()}")

    results: List[Dict[str, str]] = []
    for line in proc.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < len(fields):
            parts.extend([""] * (len(fields) - len(parts)))
        row = {fields[i]: parts[i].strip('"') for i in range(len(fields))}
        results.append(row)
    return results


# ---------------------------------------------------------------------------
# 3.  Flow-based analysis
# ---------------------------------------------------------------------------


@dataclass
class FlowStats:
    """Statistics for a single flow (5-tuple)."""
    src_ip: str = ""
    dst_ip: str = ""
    src_port: int = 0
    dst_port: int = 0
    protocol: str = ""
    packet_count: int = 0
    byte_count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    syn_count: int = 0
    fin_count: int = 0
    rst_count: int = 0
    retransmissions: int = 0
    out_of_order: int = 0
    duplicate_acks: int = 0
    rtt_samples: List[float] = field(default_factory=list)
    tcp_seqs: Dict[int, float] = field(default_factory=dict, repr=False)
    tags: List[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.last_seen - self.first_seen)

    @property
    def avg_rtt_ms(self) -> Optional[float]:
        return (statistics.mean(self.rtt_samples) * 1000) if self.rtt_samples else None

    @property
    def min_rtt_ms(self) -> Optional[float]:
        return (min(self.rtt_samples) * 1000) if self.rtt_samples else None

    @property
    def max_rtt_ms(self) -> Optional[float]:
        return (max(self.rtt_samples) * 1000) if self.rtt_samples else None

    @property
    def p95_rtt_ms(self) -> Optional[float]:
        if len(self.rtt_samples) < 2:
            return self.avg_rtt_ms
        sorted_rtts = sorted(self.rtt_samples)
        idx = int(len(sorted_rtts) * 0.95)
        return sorted_rtts[min(idx, len(sorted_rtts) - 1)] * 1000

    def to_dict(self) -> Dict[str, Any]:
        return {
            "five_tuple": f"{self.src_ip}:{self.src_port} -> {self.dst_ip}:{self.dst_port} ({self.protocol})",
            "packets": self.packet_count,
            "bytes": self.byte_count,
            "duration_s": round(self.duration, 3),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "syn": self.syn_count,
            "fin": self.fin_count,
            "rst": self.rst_count,
            "retransmissions": self.retransmissions,
            "out_of_order": self.out_of_order,
            "duplicate_acks": self.duplicate_acks,
            "avg_rtt_ms": round(self.avg_rtt_ms, 2) if self.avg_rtt_ms is not None else None,
            "min_rtt_ms": round(self.min_rtt_ms, 2) if self.min_rtt_ms is not None else None,
            "max_rtt_ms": round(self.max_rtt_ms, 2) if self.max_rtt_ms is not None else None,
            "p95_rtt_ms": round(self.p95_rtt_ms, 2) if self.p95_rtt_ms is not None else None,
            "tags": self.tags,
        }

    def to_summary(self) -> str:
        """RAG-friendly single-line summary."""
        tag = f"[{','.join(self.tags)}] " if self.tags else ""
        parts = [
            f"{tag}{self.src_ip}:{self.src_port} -> {self.dst_ip}:{self.dst_port} {self.protocol}",
            f"pkts={self.packet_count} bytes={self.byte_count} dur={self.duration:.2f}s",
        ]
        if self.retransmissions:
            parts.append(f"retrans={self.retransmissions}")
        if self.out_of_order:
            parts.append(f"ooo={self.out_of_order}")
        if self.duplicate_acks:
            parts.append(f"dup_ack={self.duplicate_acks}")
        if self.rst_count:
            parts.append(f"RST={self.rst_count}")
        if self.avg_rtt_ms is not None:
            parts.append(f"RTT avg={self.avg_rtt_ms:.1f}ms p95={self.p95_rtt_ms:.1f}ms")
        return " | ".join(parts)


FlowKey = Tuple[str, str, int, int, str]


def _canonical_flow_key(
    src_ip: str, dst_ip: str, sport: int, dport: int, proto: str
) -> FlowKey:
    """Canonicalise a 5-tuple so both directions map to the same flow."""
    if (src_ip, sport) <= (dst_ip, dport):
        return (src_ip, dst_ip, sport, dport, proto)
    return (dst_ip, src_ip, dport, sport, proto)


def _tag_flow(sport: int, dport: int, proto: str) -> List[str]:
    """Apply 5G-aware heuristic tags based on ports."""
    tags: List[str] = []
    ports = {sport, dport}
    if 8805 in ports:
        tags.append("PFCP")
    if 2123 in ports:
        tags.append("GTPv2-C")
    if 2152 in ports:
        tags.append("GTP-U")
    if 38412 in ports or proto == "SCTP":
        tags.append("NGAP")
    if ports & {80, 443, 8080, 29510, 29518, 29502, 29503, 29504, 29505, 29507, 29509}:
        tags.append("SBI")
    if 3868 in ports:
        tags.append("Diameter")
    return tags


def analyse_flows_tshark(
    pcap_path: str,
    *,
    max_packets: int = 50000,
    display_filter: Optional[str] = None,
    tls_keylog: Optional[str] = None,
) -> List[FlowStats]:
    """Flow-based analysis using tshark fields for speed.

    Extracts 5-tuples, TCP flags, sequence numbers, and timestamps to compute
    per-flow statistics including retransmissions, RTT, and anomalies.
    """
    tshark = _tshark_path()
    if not tshark:
        raise FileNotFoundError("tshark not found on PATH")

    fields = [
        "frame.time_epoch",
        "frame.len",
        "ip.src", "ip.dst",
        "ipv6.src", "ipv6.dst",
        "tcp.srcport", "tcp.dstport",
        "udp.srcport", "udp.dstport",
        "sctp.srcport", "sctp.dstport",
        "tcp.flags.syn", "tcp.flags.fin", "tcp.flags.reset", "tcp.flags.ack",
        "tcp.seq_raw", "tcp.ack_raw",
        "tcp.analysis.retransmission",
        "tcp.analysis.out_of_order",
        "tcp.analysis.duplicate_ack",
    ]

    cmd: List[str] = [
        tshark, "-r", pcap_path,
        "-T", "fields",
        "-E", "separator=\t",
        "-E", "quote=d",
        "-c", str(max_packets),
    ]
    if display_filter:
        cmd.extend(["-Y", display_filter])
    if tls_keylog and os.path.isfile(tls_keylog):
        cmd.extend(["-o", f"tls.keylog_file:{tls_keylog}"])
    for f in fields:
        cmd.extend(["-e", f])

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"tshark flow analysis failed: {proc.stderr.strip()}")

    flows: Dict[FlowKey, FlowStats] = {}

    for line in proc.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < len(fields):
            parts.extend([""] * (len(fields) - len(parts)))
        row = {fields[i]: parts[i].strip('"') for i in range(len(fields))}

        ts_str = row.get("frame.time_epoch", "")
        ts = float(ts_str) if ts_str else 0.0
        pkt_len = int(row.get("frame.len", "0") or "0")

        src = row.get("ip.src") or row.get("ipv6.src", "")
        dst = row.get("ip.dst") or row.get("ipv6.dst", "")

        sport = dport = 0
        proto = ""
        for prefix in ("tcp", "udp", "sctp"):
            s = row.get(f"{prefix}.srcport", "")
            d = row.get(f"{prefix}.dstport", "")
            if s and d:
                sport = int(s)
                dport = int(d)
                proto = prefix.upper()
                break

        if not src or not dst or not proto:
            continue

        key = _canonical_flow_key(src, dst, sport, dport, proto)

        if key not in flows:
            flows[key] = FlowStats(
                src_ip=key[0], dst_ip=key[1],
                src_port=key[2], dst_port=key[3],
                protocol=key[4],
                first_seen=ts,
                tags=_tag_flow(sport, dport, proto),
            )

        flow = flows[key]
        flow.packet_count += 1
        flow.byte_count += pkt_len
        flow.last_seen = ts

        # TCP flag tracking
        if proto == "TCP":
            if row.get("tcp.flags.syn", "") == "1":
                flow.syn_count += 1
            if row.get("tcp.flags.fin", "") == "1":
                flow.fin_count += 1
            if row.get("tcp.flags.reset", "") == "1":
                flow.rst_count += 1

            # Retransmission / OOO / dup-ack (tshark analysis fields)
            if row.get("tcp.analysis.retransmission", ""):
                flow.retransmissions += 1
            if row.get("tcp.analysis.out_of_order", ""):
                flow.out_of_order += 1
            if row.get("tcp.analysis.duplicate_ack", ""):
                flow.duplicate_acks += 1

            # RTT estimation via SYN→SYN-ACK or data→ACK
            seq_raw = row.get("tcp.seq_raw", "")
            ack_raw = row.get("tcp.ack_raw", "")
            is_ack = row.get("tcp.flags.ack", "") == "1"
            if seq_raw and ts:
                try:
                    seq_int = int(seq_raw)
                    flow.tcp_seqs[seq_int] = ts
                except ValueError:
                    pass
            if is_ack and ack_raw and ts:
                try:
                    ack_int = int(ack_raw)
                    if ack_int in flow.tcp_seqs:
                        rtt = ts - flow.tcp_seqs[ack_int]
                        if 0 < rtt < 30:  # sanity: < 30s
                            flow.rtt_samples.append(rtt)
                        del flow.tcp_seqs[ack_int]
                except (ValueError, KeyError):
                    pass

    return sorted(flows.values(), key=lambda f: f.byte_count, reverse=True)


def analyse_flows_scapy(
    pcap_path: str,
    *,
    max_packets: int = 50000,
) -> List[FlowStats]:
    """Flow-based analysis using Scapy (no tshark required).

    Scapy's TCP analysis fields (retransmission etc.) are not available,
    so we use sequence number tracking heuristics instead.
    """
    try:
        from scapy.all import PcapReader  # type: ignore[import-untyped]
        from scapy.layers.inet import IP, TCP, UDP  # type: ignore[import-untyped]
        from scapy.layers.inet6 import IPv6  # type: ignore[import-untyped]
        from scapy.layers.sctp import SCTP  # type: ignore[import-untyped]
    except ImportError:
        raise ImportError("Scapy is required.  pip install scapy")

    flows: Dict[FlowKey, FlowStats] = {}
    seen_seqs: Dict[FlowKey, Dict[int, int]] = defaultdict(dict)

    with PcapReader(pcap_path) as reader:  # type: ignore[no-untyped-call]
        for idx, pkt in enumerate(reader, 1):  # type: ignore[arg-type]
            if idx > max_packets:
                break

            src = dst = ""
            if IP in pkt:
                src, dst = str(pkt[IP].src), str(pkt[IP].dst)
            elif IPv6 in pkt:
                src, dst = str(pkt[IPv6].src), str(pkt[IPv6].dst)
            else:
                continue

            sport = dport = 0
            proto = ""
            if TCP in pkt:
                sport, dport = int(pkt[TCP].sport), int(pkt[TCP].dport)
                proto = "TCP"
            elif UDP in pkt:
                sport, dport = int(pkt[UDP].sport), int(pkt[UDP].dport)
                proto = "UDP"
            elif SCTP in pkt:
                sport, dport = int(pkt[SCTP].sport), int(pkt[SCTP].dport)
                proto = "SCTP"
            else:
                continue

            ts = float(pkt.time)
            pkt_len = len(pkt)

            key = _canonical_flow_key(src, dst, sport, dport, proto)

            if key not in flows:
                flows[key] = FlowStats(
                    src_ip=key[0], dst_ip=key[1],
                    src_port=key[2], dst_port=key[3],
                    protocol=key[4],
                    first_seen=ts,
                    tags=_tag_flow(sport, dport, proto),
                )

            flow = flows[key]
            flow.packet_count += 1
            flow.byte_count += pkt_len
            flow.last_seen = ts

            if proto == "TCP":
                flags = int(pkt[TCP].flags)
                if flags & 0x02:  # SYN
                    flow.syn_count += 1
                if flags & 0x01:  # FIN
                    flow.fin_count += 1
                if flags & 0x04:  # RST
                    flow.rst_count += 1

                # Sequence-based retransmission detection
                seq = int(pkt[TCP].seq)
                payload_len = len(bytes(pkt[TCP].payload))
                if payload_len > 0:
                    if seq in seen_seqs[key]:
                        flow.retransmissions += 1
                    else:
                        seen_seqs[key][seq] = payload_len

                # RTT: track SYN→SYN-ACK and data→ACK
                ack = int(pkt[TCP].ack)
                is_ack = bool(flags & 0x10)
                if seq and ts:
                    flow.tcp_seqs[seq] = ts
                if is_ack and ack and ts:
                    if ack in flow.tcp_seqs:
                        rtt = ts - flow.tcp_seqs[ack]
                        if 0 < rtt < 30:
                            flow.rtt_samples.append(rtt)
                        del flow.tcp_seqs[ack]

    return sorted(flows.values(), key=lambda f: f.byte_count, reverse=True)


def analyse_flows(
    pcap_path: str,
    *,
    max_packets: int = 50000,
    display_filter: Optional[str] = None,
    tls_keylog: Optional[str] = None,
    prefer_tshark: bool = True,
) -> List[FlowStats]:
    """Unified flow analysis — uses tshark when available, falls back to Scapy."""
    if prefer_tshark and _tshark_path():
        try:
            return analyse_flows_tshark(
                pcap_path,
                max_packets=max_packets,
                display_filter=display_filter,
                tls_keylog=tls_keylog,
            )
        except Exception as e:
            logger.warning(f"tshark flow analysis failed, falling back to Scapy: {e}")
    return analyse_flows_scapy(pcap_path, max_packets=max_packets)


def flows_to_summaries(flows: List[FlowStats]) -> List[str]:
    """Convert flow stats to RAG-friendly text summaries."""
    return [f.to_summary() for f in flows]


def flows_to_json(flows: List[FlowStats]) -> str:
    """Serialise all flow stats to JSON."""
    return json.dumps([f.to_dict() for f in flows], indent=2)


def flow_anomaly_report(flows: List[FlowStats]) -> List[str]:
    """Identify flows with potential issues and return diagnostic summaries."""
    issues: List[str] = []
    for f in flows:
        problems: List[str] = []
        if f.retransmissions > 0:
            pct = (f.retransmissions / max(f.packet_count, 1)) * 100
            problems.append(f"retransmissions={f.retransmissions} ({pct:.1f}%)")
        if f.rst_count > 0:
            problems.append(f"TCP RSTs={f.rst_count}")
        if f.out_of_order > 0:
            problems.append(f"out-of-order={f.out_of_order}")
        if f.duplicate_acks > 3:
            problems.append(f"dup-ACKs={f.duplicate_acks}")
        if f.p95_rtt_ms is not None and f.p95_rtt_ms > 100:
            problems.append(f"high p95 RTT={f.p95_rtt_ms:.1f}ms")
        if f.syn_count > 0 and f.packet_count == f.syn_count:
            problems.append("SYN-only (no established connection)")
        if problems:
            tag = f"[{','.join(f.tags)}] " if f.tags else ""
            line = (
                f"ANOMALY: {tag}{f.src_ip}:{f.src_port} -> {f.dst_ip}:{f.dst_port} "
                f"{f.protocol} — {', '.join(problems)}"
            )
            issues.append(line)
    return issues


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Advanced PCAP analysis for AL-5G-AE",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # --- dissect ---
    p_dissect = sub.add_parser("dissect", help="Dissect PCAP with pyshark (full Wireshark dissectors)")
    p_dissect.add_argument("pcap", help="Path to PCAP/PCAPNG file")
    p_dissect.add_argument("--filter", dest="display_filter", help="Wireshark display filter")
    p_dissect.add_argument("--max-packets", type=int, default=1000)
    p_dissect.add_argument("--tls-keylog", help="Path to TLS keylog file (SSLKEYLOGFILE)")
    p_dissect.add_argument("--decode-as", nargs="*", help="Decode-as rules, e.g. 'tcp.port==8080:http2'")
    p_dissect.add_argument("--output", help="Output file (default: stdout)")

    # --- decrypt ---
    p_decrypt = sub.add_parser("decrypt", help="Decrypt TLS in a PCAP")
    p_decrypt.add_argument("pcap", help="Path to PCAP file")
    p_decrypt.add_argument("keylog", help="Path to TLS pre-master-secret log")
    p_decrypt.add_argument("--output", help="Output PCAP path")
    p_decrypt.add_argument("--filter", dest="display_filter", help="Display filter")

    # --- tls-meta ---
    p_tls = sub.add_parser("tls-meta", help="Extract TLS handshake metadata")
    p_tls.add_argument("pcap", help="Path to PCAP file")
    p_tls.add_argument("--tls-keylog", help="Optional keylog file")
    p_tls.add_argument("--max-packets", type=int, default=5000)

    # --- flows ---
    p_flows = sub.add_parser("flows", help="5-tuple flow analysis with RTT and retransmission stats")
    p_flows.add_argument("pcap", help="Path to PCAP file")
    p_flows.add_argument("--max-packets", type=int, default=50000)
    p_flows.add_argument("--filter", dest="display_filter", help="Display filter")
    p_flows.add_argument("--tls-keylog", help="TLS keylog file")
    p_flows.add_argument("--json", action="store_true", help="Output as JSON")
    p_flows.add_argument("--anomalies", action="store_true", help="Show only anomalous flows")
    p_flows.add_argument("--output", help="Output file (default: stdout)")

    # --- live ---
    p_live = sub.add_parser("live", help="Live capture and dissection (pyshark)")
    p_live.add_argument("interface", help="Network interface name")
    p_live.add_argument("--filter", dest="display_filter", help="Display filter")
    p_live.add_argument("--bpf", help="BPF capture filter")
    p_live.add_argument("--timeout", type=int, default=30)
    p_live.add_argument("--count", type=int, default=100)
    p_live.add_argument("--tls-keylog", help="TLS keylog file")
    p_live.add_argument("--output", help="Output file (default: stdout)")

    args = parser.parse_args()

    if args.command == "dissect":
        decode_as = None
        if args.decode_as:
            decode_as = {}
            for rule in args.decode_as:
                if ":" in rule:
                    k, v = rule.split(":", 1)
                    decode_as[k] = v
        summaries = dissect_to_summaries(
            args.pcap,
            display_filter=args.display_filter,
            max_packets=args.max_packets,
            tls_keylog=args.tls_keylog,
            decode_as=decode_as,
        )
        output = "\n".join(summaries)
        if args.output:
            Path(args.output).write_text(output, encoding="utf-8")
            print(f"Written {len(summaries)} packet summaries to {args.output}")
        else:
            print(output)

    elif args.command == "decrypt":
        out = decrypt_pcap(
            args.pcap, args.keylog,
            output_path=args.output,
            display_filter=args.display_filter,
        )
        print(f"Decrypted PCAP: {out}")

    elif args.command == "tls-meta":
        meta = extract_tls_metadata(
            args.pcap,
            keylog_file=args.tls_keylog,
            max_packets=args.max_packets,
        )
        print(json.dumps(meta, indent=2))

    elif args.command == "flows":
        flow_list = analyse_flows(
            args.pcap,
            max_packets=args.max_packets,
            display_filter=args.display_filter,
            tls_keylog=args.tls_keylog,
        )
        if args.anomalies:
            lines = flow_anomaly_report(flow_list)
            if not lines:
                lines = ["No anomalies detected."]
        elif args.json:
            lines = [flows_to_json(flow_list)]
        else:
            lines = flows_to_summaries(flow_list)

        output = "\n".join(lines)
        if args.output:
            Path(args.output).write_text(output, encoding="utf-8")
            print(f"Written {len(flow_list)} flow summaries to {args.output}")
        else:
            print(output)

    elif args.command == "live":
        packets = dissect_live(
            args.interface,
            display_filter=args.display_filter,
            bpf_filter=args.bpf,
            timeout=args.timeout,
            packet_count=args.count,
            tls_keylog=args.tls_keylog,
        )
        output = json.dumps(packets, indent=2, default=str)
        if args.output:
            Path(args.output).write_text(output, encoding="utf-8")
            print(f"Written {len(packets)} packets to {args.output}")
        else:
            print(output)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
