#!/usr/bin/env python3
"""PCAP ingestion helpers for AL-5G-AE.

This module focuses on a lightweight, Scapy-based extraction that works without
external binaries. It generates a list of short, RAG-friendly text summaries.

Notes:
- PCAP protocol dissection for 5G (NGAP/NAS/GTP/PFCP) is non-trivial without
  tshark/wireshark dissectors; this extractor provides a practical overview
  (flows, ports, and common 5G UDP ports) and optional payload previews.
"""
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

from __future__ import annotations

import json
import csv
import shutil
import subprocess
from importlib.util import find_spec
from io import StringIO
from typing import Any, Dict, Iterable, List, Optional, Tuple, cast


scapy_available: bool = find_spec("scapy") is not None


def _tshark_available() -> bool:
    return shutil.which("tshark") is not None


def _protocol_labels_from_ports(*, sport: Optional[int], dport: Optional[int], proto: str) -> List[str]:
    labels: List[str] = []
    ports = {p for p in (sport, dport) if isinstance(p, int)}
    if 8805 in ports:
        labels.append("PFCP")
    if 2123 in ports:
        labels.append("GTPv2-C")
    if 2152 in ports:
        labels.append("GTP-U")
    if proto.upper() == "SCTP":
        labels.append("NGAP")
    return labels


def _labels_to_prefix(labels: List[str]) -> str:
    if not labels:
        return ""
    # de-dup while preserving order
    deduped: List[str] = []
    for label in labels:
        if label and label not in deduped:
            deduped.append(label)
    return f"[{','.join(deduped)}] " if deduped else ""


def process_pcap_tshark(
    pcap_path: str,
    *,
    max_packets: int = 1000,
    display_filter: Optional[str] = None,
) -> List[str]:
    """Extract packet summaries using tshark (Wireshark dissectors).

    This provides deeper decoding than Scapy when Wireshark/tshark is installed.
    Output is a list of RAG-friendly, single-packet summaries.
    """
    if not _tshark_available():
        raise FileNotFoundError("tshark not found on PATH")

    max_packets = max(1, int(max_packets))

    # Use columns for broad coverage, and add optional protocol-specific fields.
    # Missing fields will be empty.
    fields = [
        "frame.number",
        "frame.time_epoch",
        "_ws.col.Protocol",
        "_ws.col.Info",
        "ip.src",
        "ip.dst",
        "ipv6.src",
        "ipv6.dst",
        "udp.srcport",
        "udp.dstport",
        "tcp.srcport",
        "tcp.dstport",
        "sctp.srcport",
        "sctp.dstport",
        # Best-effort 5G-ish fields (empty if dissector not present)
        "pfcp.msg_type",
        "pfcp.seid",
        "gtp.message_type",
        "gtpv2.message_type",
        "gtpv2.cause",
        "ngap.procedureCode",
        "nas_5gs.mm.message_type",
        "nas_5gs.sm.message_type",
        "http2.streamid",
        "http2.type",
        "http2.method",
        "http2.headers.path",
        "http2.headers.status",
        "http2.header.name",
        "http2.header.value",
        "http2.data",
    ]

    cmd: List[str] = [
        "tshark",
        "-r",
        pcap_path,
        "-T",
        "fields",
        "-E",
        "separator=\t",
        "-E",
        "quote=d",
        "-E",
        "occurrence=f",
        "-c",
        str(max_packets),
    ]
    if display_filter:
        cmd.extend(["-Y", display_filter])
    for f in fields:
        cmd.extend(["-e", f])

    # tshark writes decode warnings to stderr; treat those as non-fatal.
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    if proc.returncode not in (0,):
        # If tshark fails to decode, fallback can still be attempted by caller.
        raise RuntimeError(f"tshark failed (rc={proc.returncode}): {proc.stderr.strip()}")

    out = proc.stdout.strip("\n")
    if not out:
        return []

    reader = csv.DictReader(StringIO(out), delimiter="\t", fieldnames=fields, quoting=csv.QUOTE_ALL)
    summaries: List[str] = []
    for row in reader:
        frame = (row.get("frame.number") or "").strip('"')
        ts = (row.get("frame.time_epoch") or "").strip('"')
        proto_col = (row.get("_ws.col.Protocol") or "").strip('"')
        info = (row.get("_ws.col.Info") or "").strip('"')

        ip_src = (row.get("ip.src") or row.get("ipv6.src") or "").strip('"')
        ip_dst = (row.get("ip.dst") or row.get("ipv6.dst") or "").strip('"')

        sport = None
        dport = None
        transport = ""
        for candidate in ("udp.srcport", "tcp.srcport", "sctp.srcport"):
            v = (row.get(candidate) or "").strip('"')
            if v.isdigit():
                sport = int(v)
                break
        for candidate in ("udp.dstport", "tcp.dstport", "sctp.dstport"):
            v = (row.get(candidate) or "").strip('"')
            if v.isdigit():
                dport = int(v)
                break
        if (row.get("udp.srcport") or "").strip('"'):
            transport = "UDP"
        elif (row.get("tcp.srcport") or "").strip('"'):
            transport = "TCP"
        elif (row.get("sctp.srcport") or "").strip('"'):
            transport = "SCTP"

        labels = _protocol_labels_from_ports(sport=sport, dport=dport, proto=transport)

        # Promote tshark protocol column if it matches known tokens.
        proto_upper = proto_col.upper()
        if "PFCP" in proto_upper and "PFCP" not in labels:
            labels.append("PFCP")
        if "GTP" in proto_upper and "GTP-U" not in labels and "GTPv2-C" not in labels:
            # Leave it as generic GTP label unless ports already specified U/C.
            labels.append("GTP")
        if "NGAP" in proto_upper and "NGAP" not in labels:
            labels.append("NGAP")

        prefix = _labels_to_prefix(labels)
        ports = f"{sport}->{dport}" if sport is not None and dport is not None else ""
        base = f"{prefix}frame={frame} ts={ts} {ip_src}->{ip_dst} {transport} {ports} proto={proto_col}".strip()
        if info:
            base += f" info={info}"

        # Add a compact tail of decoded fields if present.
        extras: List[str] = []
        for key in (
            "pfcp.msg_type",
            "pfcp.seid",
            "gtpv2.message_type",
            "gtpv2.cause",
            "gtp.message_type",
            "ngap.procedureCode",
            "nas_5gs.mm.message_type",
            "nas_5gs.sm.message_type",
        ):
            val = (row.get(key) or "").strip('"')
            if val:
                extras.append(f"{key}={val}")
        if extras:
            base += "\nFields: " + " ".join(extras)

        summaries.append(base)

    return summaries


def _iter_ek_events(stdout: str) -> Iterable[Dict[str, Any]]:
    """Yield parsed NDJSON events from tshark `-T ek` output.

    tshark emits one JSON object per line (NDJSON). Some versions may emit empty
    lines or non-JSON warnings; we skip those defensively.
    """
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            yield obj


def _ek_layers(event: Dict[str, Any]) -> Dict[str, Any]:
    src: Any = event.get("_source")
    if isinstance(src, dict):
        layers = cast(Any, src.get("layers"))  # type: ignore[union-attr]
        if isinstance(layers, dict):
            return cast(Dict[str, Any], {str(k): v for k, v in layers.items()})  # type: ignore[union-attr]
    return {}


def _coerce_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, list):
        for item in value:  # type: ignore[union-attr]
            s = _coerce_scalar(item)
            if s:
                return s
        return ""
    if isinstance(value, dict):
        # Prefer first scalar-ish leaf.
        for v in value.values():  # type: ignore[union-attr]
            s = _coerce_scalar(v)
            if s:
                return s
        return ""
    return str(value)


def _get_layer_field(layers: Dict[str, Any], layer_name: str, field: str) -> str:
    layer = layers.get(layer_name)
    layer: Any = layers.get(layer_name)
    if isinstance(layer, dict):
        if field in layer:
            return _coerce_scalar(layer.get(field))
        # Some EK exports prefix fields (e.g., "pfcp.pfcp.msg_type").
        for k, v in layer.items():  # type: ignore[union-attr]
            if str(k) == field or str(k).endswith("."+field):
                return _coerce_scalar(v)
    return ""


def _find_any_field(layers: Dict[str, Any], candidates: List[Tuple[str, str]]) -> str:
    for layer_name, field in candidates:
        v = _get_layer_field(layers, layer_name, field)
        if v:
            return v
    # fallback: search globally
    for layer in layers.values():
        if not isinstance(layer, dict):
            continue
        for _, field in candidates:
            if field in layer:
                v = _coerce_scalar(layer.get(field))
                if v:
                    return v
            for k, val in layer.items():  # type: ignore[union-attr]
                if str(k).endswith("."+field):
                    v = _coerce_scalar(val)
                    if v:
                        return v
    return ""


def _detect_protocols_from_layers(layers: Dict[str, Any]) -> List[str]:
    labels: List[str] = []
    # Keys here are the EK layer names used by tshark.
    for layer_key, label in (
        ("pfcp", "PFCP"),
        ("gtpv2", "GTPv2-C"),
        ("gtp", "GTP"),
        ("ngap", "NGAP"),
        ("http2", "HTTP2"),
    ):
        if layer_key in layers and label not in labels:
            labels.append(label)
    return labels


def process_pcap_tshark_ek(
    pcap_path: str,
    *,
    max_packets: int = 1000,
    display_filter: Optional[str] = None,
    include_verbose: bool = True,
) -> List[str]:
    """Extract packet summaries using tshark `-T ek` (NDJSON).

    This is intended for deterministic programmatic parsing: we parse NDJSON and
    extract protocol-specific fields into a structured, RAG-friendly summary.

    Note: Some tshark builds may not accept `-V` with `-T ek`. We attempt
    `-T ek -V` first when `include_verbose=True`, and fall back to `-T ek`.
    """
    if not _tshark_available():
        raise FileNotFoundError("tshark not found on PATH")

    max_packets = max(1, int(max_packets))

    base_cmd: List[str] = ["tshark", "-r", pcap_path]
    if display_filter:
        base_cmd.extend(["-Y", display_filter])
    base_cmd.extend(["-c", str(max_packets), "-T", "ek"])

    cmds: List[List[str]] = []
    if include_verbose:
        cmds.append(base_cmd + ["-V"])
    cmds.append(base_cmd)

    stdout = ""
    last_err = ""
    for cmd in cmds:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
        last_err = (proc.stderr or "").strip()
        if proc.returncode == 0 and (proc.stdout or "").strip():
            stdout = proc.stdout
            break
    if not stdout:
        raise RuntimeError(f"tshark ek export failed: {last_err}")

    summaries: List[str] = []

    # Candidate fields (best-effort; Wireshark versions can differ).
    frame_no = [("frame", "frame.number"), ("frame", "frame.frame_number"), ("frame", "frame.number_raw")]
    ts_epoch = [("frame", "frame.time_epoch"), ("frame", "frame.time_epoch_raw")]
    ip_src = [("ip", "ip.src"), ("ipv6", "ipv6.src")]
    ip_dst = [("ip", "ip.dst"), ("ipv6", "ipv6.dst")]
    udp_sport = [("udp", "udp.srcport")]
    udp_dport = [("udp", "udp.dstport")]
    tcp_sport = [("tcp", "tcp.srcport")]
    tcp_dport = [("tcp", "tcp.dstport")]
    sctp_sport = [("sctp", "sctp.srcport")]
    sctp_dport = [("sctp", "sctp.dstport")]

    # Human-readable label for each extracted field.
    protocol_fields: Dict[str, List[Tuple[str, str, str]]] = {
        "PFCP": [
            ("pfcp", "pfcp.msg_type", "Message Type"),
            ("pfcp", "pfcp.seid", "SEID"),
            ("pfcp", "pfcp.node_id", "Node ID"),
            ("pfcp", "pfcp.cause", "Cause"),
        ],
        "GTPv2-C": [
            ("gtpv2", "gtpv2.message_type", "Message Type"),
            ("gtpv2", "gtpv2.teid", "TEID"),
            ("gtpv2", "gtpv2.cause", "Cause"),
        ],
        "GTP-U": [
            ("gtp", "gtp.teid", "TEID"),
            ("gtp", "gtp.message_type", "Message Type"),
        ],
        "GTP": [
            ("gtp", "gtp.teid", "TEID"),
            ("gtp", "gtp.message_type", "Message Type"),
        ],
        "NGAP": [
            ("ngap", "ngap.procedureCode", "Procedure Code"),
            ("ngap", "ngap.pdu", "PDU"),
            ("ngap", "ngap.messageType", "Message Type"),
        ],
        "HTTP2": [
            ("http2", "http2.streamid", "Stream ID"),
            ("http2", "http2.type", "Frame Type"),
            ("http2", "http2.method", "Method"),
            ("http2", "http2.headers.path", "Path"),
            ("http2", "http2.headers.status", "Status"),
        ],
    }

    for event in _iter_ek_events(stdout):
        layers = _ek_layers(event)
        if not layers:
            continue

        frame = _find_any_field(layers, frame_no)
        ts = _find_any_field(layers, ts_epoch)
        src = _find_any_field(layers, ip_src)
        dst = _find_any_field(layers, ip_dst)

        # Determine transport/ports
        transport = ""
        sport: Optional[int] = None
        dport: Optional[int] = None

        s = _find_any_field(layers, udp_sport)
        d = _find_any_field(layers, udp_dport)
        if s and d:
            transport = "UDP"
        else:
            s = _find_any_field(layers, tcp_sport)
            d = _find_any_field(layers, tcp_dport)
            if s and d:
                transport = "TCP"
            else:
                s = _find_any_field(layers, sctp_sport)
                d = _find_any_field(layers, sctp_dport)
                if s and d:
                    transport = "SCTP"

        if s.isdigit():
            sport = int(s)
        if d.isdigit():
            dport = int(d)

        labels = _protocol_labels_from_ports(sport=sport, dport=dport, proto=transport)
        for extra in _detect_protocols_from_layers(layers):
            if extra == "HTTP2":
                extra = "HTTP2"
            if extra not in labels:
                labels.append(extra)
        prefix = _labels_to_prefix(labels)

        ports = f"{sport}->{dport}" if sport is not None and dport is not None else ""
        base = f"{prefix}frame={frame} ts={ts} {src}->{dst} {transport} {ports}".strip()

        extras: List[str] = []
        # Collect fields for each detected protocol label.
        for label in labels:
            for layer_name, field, readable in protocol_fields.get(label, []):
                val = _get_layer_field(layers, layer_name, field)
                if val:
                    extras.append(f"{readable}: {val}")

        # Add some HTTP2 header snippets if present.
        if "HTTP2" in labels:
            hn = _get_layer_field(layers, "http2", "http2.header.name")
            hv = _get_layer_field(layers, "http2", "http2.header.value")
            if hn and hv:
                extras.append(f"Header: {hn}={hv}")
            # Try to decode HTTP/2 data payload (hex -> text)
            data_hex = _get_layer_field(layers, "http2", "http2.data")
            if not data_hex:
                data_hex = _get_layer_field(layers, "http2", "http2.data.data")
            if data_hex:
                try:
                    body = bytes.fromhex(data_hex.replace(":", "")).decode("utf-8", errors="ignore")[:200]
                    if body.strip():
                        extras.append(f"Payload: {body.strip()}")
                except Exception:
                    pass

        if extras:
            base += "\n" + ", ".join(extras[:40])

        summaries.append(base)

    return summaries


def _safe_haslayer(pkt: Any, layer: Any) -> bool:
    if layer is None:
        return False
    try:
        haslayer = getattr(pkt, "haslayer", None)
        if not callable(haslayer):
            return False
        return bool(haslayer(layer))
    except Exception:
        return False


def _detect_app_protocol_labels(
    pkt: Any,
    *,
    sport: Optional[int],
    dport: Optional[int],
    pfcp_layer: Any = None,
    gtp_u_layer: Any = None,
    gtp_layer: Any = None,
) -> List[str]:
    """Detect 5G-relevant application protocols with best-effort heuristics.

    Primary signal: well-known ports.
    Secondary signal: Scapy dissectors (if available).
    """
    labels: List[str] = []

    # Prefer explicit dissectors if available.
    if _safe_haslayer(pkt, pfcp_layer):
        labels.append("PFCP")
    if _safe_haslayer(pkt, gtp_u_layer):
        labels.append("GTP-U")
    if _safe_haslayer(pkt, gtp_layer):
        # Scapy uses GTPHeader for multiple variants; keep label generic.
        labels.append("GTP")

    # Port-based heuristics (most reliable for typical 5GC deployments).
    ports = {p for p in (sport, dport) if isinstance(p, int)}
    if 8805 in ports:
        labels.append("PFCP")
    if 2123 in ports:
        labels.append("GTPv2-C")
    if 2152 in ports:
        labels.append("GTP-U")

    # De-dup while preserving order.
    deduped: List[str] = []
    for label in labels:
        if label not in deduped:
            deduped.append(label)
    return deduped


def process_pcap(
    pcap_path: str,
    *,
    max_packets: int = 1000,
    include_payload: bool = False,
    payload_chars: int = 200,
    prefer_tshark: bool = True,
    tshark_display_filter: Optional[str] = None,
    tshark_mode: str = "ek",
) -> List[str]:
    """Extract readable summaries from a PCAP/PCAPNG.

    Returns a list of strings (one per packet, up to `max_packets`).

    If `prefer_tshark` is True and `tshark` is available on PATH, this will
    use Wireshark dissectors for deeper decoding. It falls back to Scapy.
    """
    if prefer_tshark and _tshark_available():
        try:
            mode = (tshark_mode or "").strip().lower()
            if mode in ("ek", "ndjson", "json"):
                return process_pcap_tshark_ek(
                    pcap_path,
                    max_packets=max_packets,
                    display_filter=tshark_display_filter,
                    include_verbose=True,
                )
            return process_pcap_tshark(
                pcap_path,
                max_packets=max_packets,
                display_filter=tshark_display_filter,
            )
        except Exception:
            # Fall back to Scapy if tshark fails for any reason.
            pass

    if not scapy_available:
        raise ImportError("Scapy not installed. Install with: pip install scapy")

    # Import Scapy layers lazily so the module can be imported even when Scapy
    # isn't installed (and to avoid type-checker 'possibly unbound' warnings).
    from scapy.all import PcapReader, Raw  # type: ignore
    from scapy.layers.inet import IP, TCP, UDP  # type: ignore
    from scapy.layers.inet6 import IPv6  # type: ignore
    from scapy.layers.sctp import SCTP  # type: ignore

    # Optional dissectors (may not be present in all Scapy installs).
    try:
        from scapy.contrib.gtp import GTPHeader, GTP_U_Header  # type: ignore
    except Exception:  # pragma: no cover
        GTPHeader = None  # type: ignore
        GTP_U_Header = None  # type: ignore

    try:
        from scapy.contrib.pfcp import PFCP  # type: ignore
    except Exception:  # pragma: no cover
        PFCP = None  # type: ignore

    max_packets = max(1, int(max_packets))
    payload_chars = max(0, int(payload_chars))

    summaries: List[str] = []

    with PcapReader(pcap_path) as reader:  # type: ignore[no-untyped-call]
        for idx, pkt in enumerate(reader, start=1):  # type: ignore[arg-type]
            if idx > max_packets:
                break

            pkt_: Any = cast(Any, pkt)  # type: ignore[redundant-cast]  # Scapy lacks stubs
            ip_src = ip_dst = ""
            if IP in pkt_:
                ip_src, ip_dst = str(pkt_[IP].src), str(pkt_[IP].dst)
            elif IPv6 in pkt_:
                ip_src, ip_dst = str(pkt_[IPv6].src), str(pkt_[IPv6].dst)

            proto = ""
            ports = ""
            sport: Optional[int] = None
            dport: Optional[int] = None
            labels: List[str] = []

            if UDP in pkt_:
                proto = "UDP"
                sport, dport = int(pkt_[UDP].sport), int(pkt_[UDP].dport)
                ports = f"{sport}->{dport}"
                labels = _detect_app_protocol_labels(
                    pkt_,
                    sport=sport,
                    dport=dport,
                    pfcp_layer=PFCP,
                    gtp_u_layer=GTP_U_Header,
                    gtp_layer=GTPHeader,
                )
            elif TCP in pkt_:
                proto = "TCP"
                sport, dport = int(pkt_[TCP].sport), int(pkt_[TCP].dport)
                ports = f"{sport}->{dport}"
            elif SCTP in pkt_:
                proto = "SCTP"
                sport, dport = int(pkt_[SCTP].sport), int(pkt_[SCTP].dport)
                ports = f"{sport}->{dport}"
                labels = ["NGAP"]
            else:
                proto = "OTHER"

            label_prefix = _labels_to_prefix(labels)
            line = f"{label_prefix}pkt={idx} {ip_src}->{ip_dst} {proto} {ports}".strip()

            if include_payload and payload_chars > 0 and Raw in pkt_:
                try:
                    raw_bytes = bytes(pkt_[Raw].load)
                    decoded = raw_bytes.decode("utf-8", errors="ignore").strip()
                    if decoded:
                        decoded = decoded.replace("\r\n", "\n").replace("\r", "\n")
                        preview = decoded[:payload_chars]
                        line += f"\nPayloadPreview: {preview}"
                except Exception:
                    pass

            summaries.append(line)

    return summaries


def summaries_to_text(summaries: List[str], *, header: Optional[str] = None) -> str:
    """Join summaries to a single RAG-friendly block."""
    lines: List[str] = []
    if header:
        lines.append(header)
    lines.extend(summaries)
    return "\n".join(lines).strip()
