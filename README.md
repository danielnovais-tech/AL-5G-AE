# AL-5G-AE

A production-ready 5G Core specialist copilot. Small enough to run on a laptop, powerful enough to assist field engineers, NOC/SOC teams, and developers.

AL-5G-AE combines a lightweight language model (Phi-2 or TinyLlama) with RAG, PCAP ingestion, and a multi-modal interface (CLI, Web UI, REST API).

## Key Features

| Feature | Description |
|---|---|
| **5G-focused system prompt** | AMF, SMF, UPF, NRF, PCF, NSSF, AUSF, UDM; protocols NGAP, GTP-U, GTPv2-C, PFCP, HTTP/2, SBI, NAS; call flows, troubleshooting, log analysis |
| **RAG** | Index text files (specs, runbooks, logs) with semantic or multiline chunking. Retrieve relevant context per query |
| **PCAP ingestion** | `tshark -T ek` JSON export (or Scapy fallback). Protocol-aware tagging: `[PFCP]`, `[GTPv2-C]`, `[GTP-U]`, `[NGAP]`, `[HTTP/2]`. Decodes HTTP/2 payloads |
| **Log file ingestion** | Index any log file (plain text) into RAG |
| **Knowledge Base Builder** | Convert Markdown to plain text, slice large logs (`--log-lines`, `--log-regex`, `--since`, `--until`, `--log-multiline`) |
| **CLI** | Interactive or single-query mode |
| **Web UI (Gradio)** | Chat interface. Auto-fallback port selection (`--port 0` for OS-assigned free port). `--minimal-ui` for frontend-only testing |
| **REST API (FastAPI)** | `/query`, `/upload_log`, `/upload_pcap`, `/health` |
| **Model fallback** | `microsoft/phi-2` (2.7B) by default; falls back to `TinyLlama-1.1B-Chat` if needed |
| **Logging** | All queries and responses logged to `logs/al_5g_ae.log` |
| **Docker & HF Spaces** | Ready for containerised deployment or one-click Spaces launch |

## Installation

```bash
git clone https://github.com/danielnovais-tech/AL-5G-AE.git
cd AL-5G-AE
python -m pip install --upgrade -r requirements.txt
```

Install `tshark` (Wireshark CLI) for deep PCAP dissection:

- Ubuntu: `sudo apt install tshark`
- macOS: `brew install wireshark`
- Windows: install Wireshark and add to PATH

Verify: `python -c "import shutil; print(shutil.which('tshark'))"`

## Quick Start

### CLI — interactive with RAG and PCAP

```bash
python al_5g_ae.py --rag-dir knowledge_base --pcap-file capture.pcapng
```

### CLI — single query with log file

```bash
python al_5g_ae.py --query "What does AMF registration reject cause #15 mean?" --log-file amf.log
```

### Web UI — most robust (auto-pick free port)

```bash
python web_ui.py --rag-dir knowledge_base --debug --port 0
```

### REST API

```bash
python api_server.py --rag-dir knowledge_base
# In another terminal:
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Explain PFCP association procedure"}'
```

### Build a knowledge base from docs and logs

```bash
python kb_builder.py --input-dir ./docs --output-dir ./knowledge_base \
  --extensions .md .log --log-regex "ERROR|WARN" --log-lines 5000
```

### Docker

```bash
docker build -t al-5g-ae .
docker run --rm -p 7860:7860 -v al5gae_data:/data al-5g-ae
```

## CLI Arguments

| Argument | Default | Description |
|---|---|---|
| `--model` | `microsoft/phi-2` | Model name or path (falls back to TinyLlama) |
| `--device` | `cpu` | `cpu` or `cuda` |
| `--max-tokens` | `512` | Max new tokens to generate |
| `--temperature` | `0.7` | Sampling temperature |
| `--rag-dir` | — | Directory of `.txt` files for RAG |
| `--log-file` | — | Log file to ingest (indexed if RAG enabled) |
| `--pcap-file` | — | PCAP file to ingest (indexed if RAG enabled) |
| `--pcap-max-packets` | `2000` | Max packets to parse |
| `--pcap-filter` | — | `tshark` display filter (only when `tshark` installed) |
| `--run-log` | `logs/al_5g_ae.log` | Run log path (set to `""` to disable) |
| `--verbose` | off | Debug-level logging |
| `--query` | — | Single-shot question (non-interactive) |

## How It Works

1. **Model & RAG** — Loads a causal LM and optionally builds a FAISS index over documents/logs/PCAPs.
2. **PCAP path** — If `tshark` is present, uses `-T ek -V` for detailed JSON, then parses protocol fields (SEID, message type, TEID, HTTP/2 headers and payload). Falls back to Scapy.
3. **Chunking** — Two modes: *semantic* (sentence-aware, for prose) and *multiline* (preserves stack traces, log entries). Auto-detected by default.
4. **Prompt** — Injects system prompt + retrieved context (if RAG) + user question.
5. **Generation** — Temperature, top-p, repetition penalty for focused answers.
6. **Web UI** — Gradio with automatic port fallback (scans for free port, supports `--port 0`).
7. **API** — FastAPI server with async endpoints.

## Web UI

```bash
python web_ui.py --rag-dir knowledge_base --debug
```

Port handling:

- Auto-scans ports 7860–7910; falls back to OS-assigned if all busy
- Force a port: `--port 7861`
- Let OS pick: `--port 0`

Optional PCAP ingestion:

```bash
python web_ui.py --rag-dir knowledge_base --pcap-file capture.pcapng --pcap-filter "pfcp || gtpv2"
```

Troubleshooting `Dft.clearMarks is not a function`:

- Open page in Incognito/Private window
- Hard refresh (`Ctrl+Shift+R`)
- Disable React DevTools extension
- This is cosmetic and does not affect functionality

## REST API (FastAPI)

```bash
python api_server.py --host 0.0.0.0 --port 8000 --rag-dir ./knowledge_base
```

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Status check |
| `/query` | POST | Ask a question (JSON body) |
| `/upload_log` | POST | Upload and index a log file |
| `/upload_pcap` | POST | Upload, extract, and index a PCAP |

Examples:

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What causes AMF registration reject?", "rag_dir": "./knowledge_base"}'

curl -X POST http://localhost:8000/upload_log \
  -F "rag_dir=./knowledge_base" -F "file=@./logs/amf.log"

curl -X POST http://localhost:8000/upload_pcap \
  -F "rag_dir=./knowledge_base" -F "max_packets=2000" \
  -F "pcap_filter=pfcp || gtpv2 || gtp" -F "file=@./captures/session.pcapng"
```

## Knowledge Base

### Starter pack

`knowledge_base/` ships with non-copyrighted sample files:

- `ts_23501.txt` — 5G Core architecture summary
- `ts_23502.txt` — Registration procedure steps
- `vendor_troubleshooting.txt` — Common AMF issues
- `pcap-protocol-map.txt` — Protocol lookup (tshark filters, JSON paths, ports)

Add your own 3GPP specs, vendor guides, or runbooks as `.txt` files.

### Knowledge Base Builder

```bash
python kb_builder.py --input-dir ./docs --output-dir ./knowledge_base --extensions .md .log --clear
```

| Flag | Description |
|---|---|
| `--input-dir` | Source directory (default: `./docs`) |
| `--output-dir` | Output directory (default: `./knowledge_base`) |
| `--extensions` | File types to process (default: `.md .log`) |
| `--since` | Keep log lines at or after this timestamp |
| `--until` | Keep log lines at or before this timestamp |
| `--log-multiline` | Group stacktraces/multiline entries before filtering |
| `--log-lines` | Keep only the last N lines |
| `--log-regex` | Keep only lines matching a regex |
| `--clear` | Delete output directory before building |
| `--verbose` | Print per-file details |

Example (AMF errors in a time window, preserving stacktraces):

```bash
python kb_builder.py --input-dir ./docs --output-dir ./knowledge_base --extensions .log --clear \
  --since "2026-04-02T10:00:00" --until "2026-04-02T12:00:00" --log-multiline \
  --log-regex "ERROR|WARN|AMF_" --log-lines 5000
```

## Logging

```bash
python al_5g_ae.py --run-log ""                    # disable
python al_5g_ae.py --run-log ./logs/session-001.log # custom path
python al_5g_ae.py --verbose                        # debug-level
```

## Docker

```bash
docker build -t al-5g-ae .
docker run --rm -p 7860:7860 -v al5gae_data:/data al-5g-ae
```

- Model weights cached under `/data`
- Lazy-loaded on first request
- Includes `tshark` for deep PCAP decoding

## Hugging Face Spaces

Entry file: `app.py`. Environment variables:

| Variable | Default |
|---|---|
| `AL5GAE_MODEL` | `microsoft/phi-2` |
| `AL5GAE_DEVICE` | `cpu` |
| `AL5GAE_RAG_DIR` | `knowledge_base` |

`packages.txt` installs `tshark` automatically in Spaces.

## Known Issues & Workarounds

| Issue | Solution |
|---|---|
| `Dft.clearMarks is not a function` | Gradio 6.x fixed this. If seen: Incognito + disable React DevTools. Cosmetic only |
| Port 7860 already in use | Auto-scans 7860–7910; use `--port 0` for OS-assigned port |
| `tshark` not found | Falls back to Scapy. Install `tshark` for full JSON export |
| Large PCAPs slow | Use `--pcap-filter` and/or `--pcap-max-packets 500` |

## Customization

- **Model** — `--model` to swap models
- **System Prompt** — edit `SYSTEM_PROMPT` in `al_5g_ae_core.py`
- **RAG** — point `--rag-dir` at your docs folder

## Validation

All modules pass `py_compile`. The web UI launches without errors, PCAP ingestion works with both `tshark` and Scapy, and the API responds to queries.

## Roadmap

- Deploy to Hugging Face Spaces
- Real-time monitoring (Kafka/REST streams from 5G core)
- Fine-tune the SLM (LoRA on domain Q&A pairs)
- Slack / Teams bot integration
