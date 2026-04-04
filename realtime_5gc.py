#!/usr/bin/env python3
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownParameterType=false, reportUnknownArgumentType=false
# pyright: reportMissingModuleSource=false, reportReturnType=false
"""
Real-time 5G Core integration for AL-5G-AE.

Provides three capabilities:

1. **gNMI / RESTCONF client** — fetch live configuration and state from 5G
   core NFs (AMF, SMF, UPF, etc.) via gNMI (gRPC) or RESTCONF (HTTPS/JSON).
2. **Streaming telemetry (Kafka)** — consume metrics, logs, and traces from
   Kafka topics, normalise them, and index into RAG.
3. **Root cause correlator** — combine alerts, logs, and PCAP summaries into
   a single timeline and query the model for automated root-cause analysis.

All heavy dependencies are lazily imported.

Environment variables
---------------------
GNMI_TARGET        – gNMI target address (host:port)
GNMI_USER          – gNMI username
GNMI_PASSWORD      – gNMI password
GNMI_TLS_CERT      – path to TLS client certificate (optional)
RESTCONF_BASE_URL  – RESTCONF base URL (e.g. https://amf.lab:443)
RESTCONF_USER      – RESTCONF HTTP username
RESTCONF_PASSWORD  – RESTCONF HTTP password
KAFKA_BOOTSTRAP    – Kafka bootstrap servers (default: localhost:9092)
KAFKA_TOPICS       – comma-separated Kafka topics for telemetry
AL5GAE_MODEL       – model name or GGUF path
RAG_DIR            – knowledge base directory
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("realtime_5gc")


# ---------------------------------------------------------------------------
# Lazy dependency helpers
# ---------------------------------------------------------------------------
_grpc: Any = None
_gnmi_pb2: Any = None
_gnmi_pb2_grpc: Any = None
_requests: Any = None
_KafkaConsumer: Any = None


def _ensure_grpc() -> bool:  # noqa: F811  – kept for future direct gRPC usage
    global _grpc
    if _grpc is not None:
        return True
    try:
        import grpc  # type: ignore[import-untyped]
        _grpc = grpc
        return True
    except ImportError:
        return False


_ = _ensure_grpc  # mark as accessed (used indirectly by gNMI fallback path)


def _ensure_gnmi_proto() -> bool:
    """Try to import the gNMI protobuf stubs (pygnmi or grpcio protos)."""
    global _gnmi_pb2, _gnmi_pb2_grpc
    if _gnmi_pb2 is not None:
        return True
    try:
        from pygnmi.client import gNMIclient  # type: ignore[import-untyped]
        _gnmi_pb2 = gNMIclient  # re-use pygnmi client as our accessor
        return True
    except ImportError:
        return False


def _ensure_requests() -> bool:
    global _requests
    if _requests is not None:
        return True
    try:
        import requests  # type: ignore[import-untyped]
        _requests = requests
        return True
    except ImportError:
        return False


def _ensure_kafka() -> bool:
    global _KafkaConsumer
    if _KafkaConsumer is not None:
        return True
    try:
        from kafka import KafkaConsumer  # type: ignore[import-untyped]
        _KafkaConsumer = KafkaConsumer
        return True
    except ImportError:
        return False


# ===================================================================
# 1.  gNMI client
# ===================================================================

class GNMIClient:
    """Thin wrapper around pygnmi for gNMI Get / Subscribe operations."""

    def __init__(
        self,
        target: str = "",
        username: str = "",
        password: str = "",
        tls_cert: str = "",
        insecure: bool = False,
    ):
        self.target = target or os.environ.get("GNMI_TARGET", "localhost:57400")
        self.username = username or os.environ.get("GNMI_USER", "admin")
        self.password = password or os.environ.get("GNMI_PASSWORD", "admin")
        self.tls_cert = tls_cert or os.environ.get("GNMI_TLS_CERT", "")
        self.insecure = insecure

    def get(self, paths: List[str]) -> Dict[str, Any]:
        """gNMI Get request.  Returns parsed JSON notification."""
        if not _ensure_gnmi_proto():
            raise RuntimeError("pygnmi is not installed.  pip install pygnmi")

        kwargs: Dict[str, Any] = {
            "target": (self.target.split(":")[0], int(self.target.split(":")[1])),
            "username": self.username,
            "password": self.password,
            "insecure": self.insecure,
        }
        if self.tls_cert:
            kwargs["path_cert"] = self.tls_cert

        with _gnmi_pb2(**kwargs) as gc:
            result = gc.get(path=paths, encoding="json_ietf")
        logger.info("gNMI GET %s → %d notifications", paths, len(result.get("notification", [])))
        return result  # type: ignore[no-any-return]

    def subscribe_once(self, paths: List[str], sample_interval_ns: int = 10_000_000_000) -> List[Dict[str, Any]]:
        """gNMI Subscribe ONCE – collect one sample per path and return."""
        if not _ensure_gnmi_proto():
            raise RuntimeError("pygnmi is not installed.  pip install pygnmi")

        subscribe_args: Dict[str, Any] = {
            "subscription": [{"path": p, "mode": "sample", "sample_interval": sample_interval_ns} for p in paths],
            "mode": "once",
            "encoding": "json_ietf",
        }
        kwargs: Dict[str, Any] = {
            "target": (self.target.split(":")[0], int(self.target.split(":")[1])),
            "username": self.username,
            "password": self.password,
            "insecure": self.insecure,
        }
        if self.tls_cert:
            kwargs["path_cert"] = self.tls_cert

        results: List[Dict[str, Any]] = []
        with _gnmi_pb2(**kwargs) as gc:
            for resp in gc.subscribe(subscribe=subscribe_args):
                results.append(resp)
        logger.info("gNMI subscribe-once collected %d responses", len(results))
        return results

    def get_as_text(self, paths: List[str]) -> str:
        """Convenience: return gNMI Get as a RAG-friendly text summary."""
        data = self.get(paths)
        lines: List[str] = []
        for notification in data.get("notification", []):
            ts = notification.get("timestamp", "")
            for update in notification.get("update", []):
                path = update.get("path", "")
                val = update.get("val", "")
                lines.append(f"[gNMI {ts}] {path} = {json.dumps(val)}")
        return "\n".join(lines) if lines else json.dumps(data, indent=2)


# ===================================================================
# 2.  RESTCONF client
# ===================================================================

class RESTCONFClient:
    """Simple RESTCONF GET client for 5G core NFs (YANG-modelled)."""

    def __init__(
        self,
        base_url: str = "",
        username: str = "",
        password: str = "",
        verify_tls: bool = True,
    ):
        self.base_url = (base_url or os.environ.get("RESTCONF_BASE_URL", "https://localhost:443")).rstrip("/")
        self.username = username or os.environ.get("RESTCONF_USER", "admin")
        self.password = password or os.environ.get("RESTCONF_PASSWORD", "admin")
        self.verify_tls = verify_tls
        self._headers = {
            "Accept": "application/yang-data+json",
            "Content-Type": "application/yang-data+json",
        }

    def get(self, resource_path: str) -> Dict[str, Any]:
        """GET a RESTCONF resource.  *resource_path* is appended to the base URL."""
        if not _ensure_requests():
            raise RuntimeError("requests is not installed.  pip install requests")

        url = f"{self.base_url}/restconf/data/{resource_path.lstrip('/')}"
        resp = _requests.get(
            url,
            headers=self._headers,
            auth=(self.username, self.password),
            verify=self.verify_tls,
            timeout=30,
        )
        resp.raise_for_status()
        data: Dict[str, Any] = resp.json()
        logger.info("RESTCONF GET %s → %d bytes", resource_path, len(resp.content))
        return data

    def get_as_text(self, resource_path: str) -> str:
        """Convenience: return RESTCONF result as a RAG-friendly text summary."""
        data = self.get(resource_path)
        return json.dumps(data, indent=2)

    # Pre-canned 5GC queries ---------------------------------------------------

    _5GC_PATHS: Dict[str, str] = {
        "amf-sessions": "ietf-amf:amf/ue-contexts",
        "smf-sessions": "ietf-smf:smf/pdu-sessions",
        "upf-interfaces": "ietf-upf:upf/interfaces",
        "nrf-nf-instances": "ietf-nrf:nrf/nf-instances",
        "pcf-policies": "ietf-pcf:pcf/policies",
    }

    def get_5gc_state(self, nf_type: str) -> str:
        """High-level query by NF type.  Returns RAG-friendly text."""
        path = self._5GC_PATHS.get(nf_type.lower().replace(" ", "-"), "")
        if not path:
            available = ", ".join(self._5GC_PATHS.keys())
            return f"Unknown NF type '{nf_type}'.  Available: {available}"
        try:
            return self.get_as_text(path)
        except Exception as exc:
            return f"RESTCONF error for {nf_type}: {exc}"


# ===================================================================
# 3.  Streaming telemetry — Kafka ingestion
# ===================================================================

@dataclass
class TelemetryEvent:
    """Normalised telemetry event (metric, log, or trace span)."""
    timestamp: str
    source: str  # e.g. "amf-01", "smf-02"
    event_type: str  # "metric" | "log" | "trace" | "alert"
    content: str  # human-readable summary
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_text(self) -> str:
        return f"[{self.event_type.upper()} {self.timestamp}] {self.source}: {self.content}"


def _normalise_event(raw: Dict[str, Any]) -> TelemetryEvent:
    """Best-effort normalisation of a Kafka JSON message into TelemetryEvent."""
    ts = (
        raw.get("timestamp")
        or raw.get("@timestamp")
        or raw.get("time")
        or datetime.now(timezone.utc).isoformat()
    )
    source = (
        raw.get("source")
        or raw.get("host")
        or raw.get("hostname")
        or raw.get("instance")
        or "unknown"
    )
    # Detect event type
    if "metric" in raw or "metric_name" in raw or "value" in raw:
        etype = "metric"
        name = raw.get("metric_name") or raw.get("metric", {}).get("__name__", "unknown")
        value = raw.get("value") or raw.get("metric_value", "")
        content = f"{name} = {value}"
    elif "severity" in raw or "level" in raw or "log" in raw:
        etype = "log"
        level = raw.get("severity") or raw.get("level") or "INFO"
        msg = raw.get("message") or raw.get("log") or raw.get("msg") or json.dumps(raw)
        content = f"[{level}] {msg}"
    elif "traceId" in raw or "trace_id" in raw or "spanId" in raw:
        etype = "trace"
        svc = raw.get("serviceName") or raw.get("service") or source
        op = raw.get("operationName") or raw.get("name") or "?"
        dur = raw.get("duration") or raw.get("durationMs") or "?"
        content = f"{svc}/{op} duration={dur}"
    elif "alertname" in raw or "status" in raw and raw.get("status") in ("firing", "resolved"):
        etype = "alert"
        alertname = raw.get("alertname") or raw.get("labels", {}).get("alertname", "unknown")
        content = f"{alertname}: {raw.get('annotations', {}).get('summary', json.dumps(raw))}"
    else:
        etype = "log"
        content = json.dumps(raw)[:500]

    return TelemetryEvent(timestamp=str(ts), source=str(source), event_type=etype, content=content, raw=raw)


class TelemetryConsumer:
    """Consumes telemetry events from Kafka and indexes them into RAG."""

    def __init__(
        self,
        bootstrap_servers: str = "",
        topics: Optional[List[str]] = None,
        group_id: str = "al5gae-telemetry",
        rag: Any = None,
        buffer_size: int = 50,
        on_event: Optional[Callable[[TelemetryEvent], None]] = None,
    ):
        self.bootstrap = bootstrap_servers or os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
        env_topics = os.environ.get("KAFKA_TOPICS", "")
        self.topics = topics or ([t.strip() for t in env_topics.split(",") if t.strip()] if env_topics else ["5gc-telemetry"])
        self.group_id = group_id
        self.rag = rag
        self.buffer_size = buffer_size
        self.on_event = on_event
        self._buffer: List[str] = []
        self._event_count: int = 0

    def _flush(self) -> int:
        if not self._buffer or not self.rag:
            return 0
        text = "\n".join(self._buffer)
        self.rag.add_documents([text])
        count = len(self._buffer)
        self._buffer.clear()
        logger.info("Indexed %d telemetry events into RAG", count)
        return count

    def consume(self, max_events: int = 0) -> int:
        """Blocking consume loop.  Returns total events processed (0 = infinite loop)."""
        if not _ensure_kafka():
            raise RuntimeError("kafka-python is not installed.  pip install kafka-python")

        def _deser(m: bytes) -> Any:
            return json.loads(m.decode("utf-8", errors="ignore")) if m else {}

        consumer = _KafkaConsumer(
            *self.topics,
            bootstrap_servers=self.bootstrap,
            group_id=self.group_id,
            auto_offset_reset="latest",
            value_deserializer=_deser,
        )
        logger.info("Kafka telemetry consumer started: %s on %s", self.topics, self.bootstrap)

        for msg in consumer:
            raw = msg.value if isinstance(msg.value, dict) else {}
            event = _normalise_event(raw)
            self._event_count += 1
            self._buffer.append(event.to_text())

            if self.on_event:
                self.on_event(event)

            if len(self._buffer) >= self.buffer_size:
                self._flush()

            if max_events and self._event_count >= max_events:
                self._flush()
                break

        logger.info("Telemetry consumer stopped after %d events", self._event_count)
        return self._event_count


# ===================================================================
# 4.  Root cause correlator
# ===================================================================

@dataclass
class CorrelationEvent:
    """A single event in a correlation timeline."""
    timestamp: str
    source_type: str  # "alert" | "log" | "pcap" | "metric" | "gnmi" | "restconf"
    summary: str
    detail: str = ""

    def to_text(self) -> str:
        return f"[{self.source_type.upper()} {self.timestamp}] {self.summary}"


class RootCauseCorrelator:
    """
    Combines alerts, logs, PCAP summaries, and telemetry events into a
    unified timeline.  Queries the model for automated root-cause analysis.
    """

    def __init__(self) -> None:
        self.events: List[CorrelationEvent] = []

    def clear(self) -> None:
        self.events.clear()

    # ---- Ingest methods --------------------------------------------------

    def add_alerts(self, alerts: List[Dict[str, Any]]) -> int:
        """Ingest Alertmanager-format alerts."""
        count = 0
        for alert in alerts:
            labels = alert.get("labels", {})
            annotations = alert.get("annotations", {})
            ts = alert.get("startsAt") or alert.get("activeAt") or datetime.now(timezone.utc).isoformat()
            name = labels.get("alertname", "unknown")
            instance = labels.get("instance", "?")
            severity = labels.get("severity", "?")
            summary = annotations.get("summary", annotations.get("description", ""))
            self.events.append(CorrelationEvent(
                timestamp=str(ts),
                source_type="alert",
                summary=f"{name} on {instance} (severity={severity})",
                detail=summary,
            ))
            count += 1
        return count

    def add_logs(self, log_lines: Sequence[str], source: str = "log") -> int:
        """Ingest raw log lines.  Attempts to extract timestamps."""
        ts_pattern = re.compile(
            r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[\.\d]*[Z+\-\d:]*)"
        )
        count = 0
        for line in log_lines:
            line = line.strip()
            if not line:
                continue
            match = ts_pattern.search(line)
            ts = match.group(1) if match else datetime.now(timezone.utc).isoformat()
            self.events.append(CorrelationEvent(
                timestamp=ts,
                source_type="log",
                summary=line[:200],
                detail=line,
            ))
            count += 1
        return count

    def add_pcap_summaries(self, summaries: Sequence[str]) -> int:
        """Ingest RAG-friendly PCAP summary lines (from pcap_ingest.py)."""
        ts_pattern = re.compile(r"(\d+\.\d+)")
        count = 0
        for line in summaries:
            line = line.strip()
            if not line:
                continue
            match = ts_pattern.search(line)
            ts = match.group(1) if match else "0"
            self.events.append(CorrelationEvent(
                timestamp=ts,
                source_type="pcap",
                summary=line[:200],
                detail=line,
            ))
            count += 1
        return count

    def add_telemetry_events(self, events: Sequence[TelemetryEvent]) -> int:
        """Ingest normalised TelemetryEvent objects."""
        count = 0
        for te in events:
            self.events.append(CorrelationEvent(
                timestamp=te.timestamp,
                source_type=te.event_type,
                summary=te.content[:200],
                detail=te.to_text(),
            ))
            count += 1
        return count

    def add_gnmi_snapshot(self, client: GNMIClient, paths: List[str]) -> int:
        """Fetch gNMI data and add to the correlation timeline."""
        text = client.get_as_text(paths)
        now = datetime.now(timezone.utc).isoformat()
        self.events.append(CorrelationEvent(
            timestamp=now,
            source_type="gnmi",
            summary=f"gNMI snapshot: {', '.join(paths)}",
            detail=text[:2000],
        ))
        return 1

    def add_restconf_snapshot(self, client: RESTCONFClient, resource_path: str) -> int:
        """Fetch RESTCONF data and add to the correlation timeline."""
        text = client.get_as_text(resource_path)
        now = datetime.now(timezone.utc).isoformat()
        self.events.append(CorrelationEvent(
            timestamp=now,
            source_type="restconf",
            summary=f"RESTCONF snapshot: {resource_path}",
            detail=text[:2000],
        ))
        return 1

    # ---- Timeline --------------------------------------------------------

    def build_timeline(self, max_events: int = 100) -> str:
        """Sort events by timestamp and produce a text timeline."""
        sorted_events = sorted(self.events, key=lambda e: e.timestamp)
        if len(sorted_events) > max_events:
            sorted_events = sorted_events[-max_events:]
        lines: List[str] = ["=== Root Cause Correlation Timeline ===", ""]
        for evt in sorted_events:
            lines.append(evt.to_text())
            if evt.detail and evt.detail != evt.summary:
                lines.append(f"    Detail: {evt.detail[:300]}")
        lines.append("")
        lines.append(f"Total events: {len(self.events)} (showing last {len(sorted_events)})")
        return "\n".join(lines)

    # ---- LLM analysis ----------------------------------------------------

    def analyse(
        self,
        tokenizer: Any = None,
        model: Any = None,
        rag: Any = None,
        max_timeline_events: int = 50,
    ) -> str:
        """
        Build the timeline, retrieve RAG context, and query the model
        for a root-cause analysis.

        Returns the model's answer as a string.
        """
        from al_5g_ae_core import generate_response  # late import

        timeline = self.build_timeline(max_events=max_timeline_events)

        # Use RAG for supplementary context
        rag_context: Optional[str] = None
        if rag:
            # Use the first alert or log as the RAG query
            query_text = ""
            for evt in self.events:
                if evt.source_type == "alert":
                    query_text = evt.summary
                    break
            if not query_text and self.events:
                query_text = self.events[0].summary
            if query_text:
                rag_context = rag.retrieve(query_text, k=3)

        # Combine timeline + RAG into a single context block
        combined_context = timeline
        if rag_context:
            combined_context += "\n\n=== Relevant Knowledge Base ===\n" + rag_context

        question = (
            "Given the following correlation timeline of alerts, logs, PCAP captures, "
            "and telemetry from a 5G Core network, perform root cause analysis.\n"
            "Identify:\n"
            "1. The most likely root cause\n"
            "2. Affected network functions and interfaces\n"
            "3. Recommended remediation steps\n"
            "4. Any patterns that suggest systemic issues\n"
        )

        return generate_response(tokenizer, model, question, [combined_context])


# ===================================================================
# CLI entrypoint
# ===================================================================

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Real-time 5G Core integration")
    sub = parser.add_subparsers(dest="command")

    # -- gnmi subcommand --
    gnmi_parser = sub.add_parser("gnmi", help="gNMI Get request")
    gnmi_parser.add_argument("--target", default="", help="gNMI target (host:port)")
    gnmi_parser.add_argument("--user", default="", help="gNMI username")
    gnmi_parser.add_argument("--password", default="", help="gNMI password")
    gnmi_parser.add_argument("--tls-cert", default="", help="TLS client certificate")
    gnmi_parser.add_argument("--insecure", action="store_true", help="Skip TLS verification")
    gnmi_parser.add_argument("paths", nargs="+", help="YANG paths to query")

    # -- restconf subcommand --
    rest_parser = sub.add_parser("restconf", help="RESTCONF Get request")
    rest_parser.add_argument("--base-url", default="", help="Base URL")
    rest_parser.add_argument("--user", default="", help="Username")
    rest_parser.add_argument("--password", default="", help="Password")
    rest_parser.add_argument("--no-verify", action="store_true", help="Skip TLS verification")
    rest_parser.add_argument("resource", help="RESTCONF resource path")

    # -- kafka subcommand --
    kafka_parser = sub.add_parser("kafka", help="Kafka telemetry consumer")
    kafka_parser.add_argument("--bootstrap", default="", help="Kafka bootstrap servers")
    kafka_parser.add_argument("--topics", default="", help="Comma-separated topics")
    kafka_parser.add_argument("--group-id", default="al5gae-telemetry")
    kafka_parser.add_argument("--rag-dir", default="./knowledge_base")
    kafka_parser.add_argument("--max-events", type=int, default=0, help="Stop after N events (0 = infinite)")

    # -- correlate subcommand --
    corr_parser = sub.add_parser("correlate", help="Root cause correlation")
    corr_parser.add_argument("--alerts-file", default="", help="JSON file of Alertmanager alerts")
    corr_parser.add_argument("--log-file", default="", help="Text log file")
    corr_parser.add_argument("--pcap-file", default="", help="PCAP file (requires pcap_ingest)")
    corr_parser.add_argument("--rag-dir", default="./knowledge_base")
    corr_parser.add_argument("--model", default="", help="Model name or GGUF path")

    args = parser.parse_args()

    if args.command == "gnmi":
        client = GNMIClient(
            target=args.target,
            username=args.user,
            password=args.password,
            tls_cert=args.tls_cert,
            insecure=args.insecure,
        )
        print(client.get_as_text(args.paths))

    elif args.command == "restconf":
        client_r = RESTCONFClient(
            base_url=args.base_url,
            username=args.user,
            password=args.password,
            verify_tls=not args.no_verify,
        )
        print(client_r.get_as_text(args.resource))

    elif args.command == "kafka":
        rag = None
        rag_dir = Path(args.rag_dir)
        if rag_dir.exists():
            from al_5g_ae_core import RAG
            rag = RAG()
            for f in rag_dir.glob("*.txt"):
                rag.add_file(str(f))
            logger.info("Loaded RAG from %s", args.rag_dir)

        topics = [t.strip() for t in args.topics.split(",") if t.strip()] if args.topics else None
        consumer = TelemetryConsumer(
            bootstrap_servers=args.bootstrap,
            topics=topics,
            group_id=args.group_id,
            rag=rag,
            on_event=lambda e: print(e.to_text()),
        )
        consumer.consume(max_events=args.max_events)

    elif args.command == "correlate":
        from al_5g_ae_core import load_model, RAG, DEFAULT_MODEL

        correlator = RootCauseCorrelator()

        # Ingest alerts
        if args.alerts_file:
            with open(args.alerts_file, "r", encoding="utf-8") as f:
                alert_data = json.load(f)
            alerts = alert_data if isinstance(alert_data, list) else alert_data.get("alerts", [])
            n = correlator.add_alerts(alerts)
            logger.info("Added %d alerts", n)

        # Ingest logs
        if args.log_file:
            with open(args.log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            n = correlator.add_logs(lines)
            logger.info("Added %d log lines", n)

        # Ingest PCAP
        if args.pcap_file:
            try:
                from pcap_ingest import process_pcap
                summaries = process_pcap(args.pcap_file)
                n = correlator.add_pcap_summaries(summaries)
                logger.info("Added %d PCAP summaries", n)
            except ImportError:
                logger.warning("pcap_ingest not available; skipping PCAP")

        # Build RAG
        rag = None
        rag_dir = Path(args.rag_dir)
        if rag_dir.exists():
            rag = RAG()
            for f in rag_dir.glob("*.txt"):
                rag.add_file(str(f))

        # Load model and analyse
        model_name = args.model or os.environ.get("AL5GAE_MODEL", DEFAULT_MODEL)
        logger.info("Loading model %s for correlation analysis...", model_name)
        tokenizer, model = load_model(model_name, "cpu")

        print("\n" + correlator.build_timeline())
        print("\n=== Root Cause Analysis ===\n")
        answer = correlator.analyse(tokenizer=tokenizer, model=model, rag=rag)
        print(answer)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
