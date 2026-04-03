# PCAP Protocol Map (AL-5G-AE)

This table is intended to be **RAG-friendly** and **deterministic**: it documents the ports, recommended `tshark` display filters (`-Y`), the key fields that AL-5G-AE tries to extract, and the EK JSON paths used when parsing `tshark -T ek` output.

Notes:
- `tshark` has native dissectors for PFCP, GTPv2-C, GTP-U (GTP), NGAP, and HTTP/2.
- SBI is not its own wire protocol; it is typically **HTTP/2** (often over **TLS**). You may only see HTTP/2 metadata unless you have TLS keys for decryption.
- Field names can vary slightly by Wireshark/tshark version; AL-5G-AE uses best-effort extraction.
- HTTP/2 payload (`http2.data`) is hex-encoded in EK output; AL-5G-AE decodes it to UTF-8 text automatically.

| Protocol | Tag Prefix | Typical Port(s) | Suggested `-Y` filter | Key fields | EK JSON path (`_source.layers.*`) |
|---|---|---:|---|---|---|
| PFCP | `[PFCP]` | UDP/8805 | `pfcp \|\| udp.port==8805` | `pfcp.msg_type`, `pfcp.seid`, `pfcp.node_id`, `pfcp.cause` | `layers.pfcp.pfcp.seid`, `layers.pfcp.pfcp.msg_type` |
| GTPv2-C | `[GTPv2-C]` | UDP/2123 | `gtpv2 \|\| udp.port==2123` | `gtpv2.message_type`, `gtpv2.teid`, `gtpv2.cause` | `layers.gtpv2.gtpv2.message_type`, `layers.gtpv2.gtpv2.teid` |
| GTP-U | `[GTP-U]` | UDP/2152 | `(udp.port==2152 && gtp) \|\| gtp` | `gtp.teid`, `gtp.message_type` | `layers.gtp.gtp.teid`, `layers.gtp.gtp.message_type` |
| NGAP | `[NGAP]` | SCTP (often 38412) | `ngap \|\| (sctp && ngap)` | `ngap.procedureCode`, `ngap.pdu`, `ngap.messageType` | `layers.ngap.ngap.procedureCode` |
| HTTP/2 (SBI) | `[HTTP2]` | TCP/443, TCP/80 | `http2 \|\| (tcp && http2)` | `http2.streamid`, `http2.type`, `http2.method`, `http2.headers.path`, `http2.headers.status`, `http2.data` | `layers.http2.http2.method`, `layers.http2.http2.headers.path`, `layers.http2.http2.headers.status` |

## Deterministic extraction modes

### EK (NDJSON) — recommended for full per-packet detail

```bash
tshark -r capture.pcap -Y "pfcp" -T ek -V
```

### Columnar fields — best for extracting a known set of fields

```bash
tshark -r capture.pcap -Y "pfcp" -T fields -E header=n -E separator=\t \
  -e frame.time_epoch -e ip.src -e ip.dst -e pfcp.msg_type -e pfcp.seid
```

## Practical examples

Extract PFCP only (when `tshark` is available):

```bash
python al_5g_ae.py --rag-dir knowledge_base --pcap-file capture.pcapng --pcap-filter "pfcp || udp.port==8805"
```

Extract a mixed control-plane set:

```bash
python al_5g_ae.py --rag-dir knowledge_base --pcap-file capture.pcapng --pcap-filter "pfcp || gtpv2 || ngap || http2"
```
