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
| **Real-time streaming** | WebSocket server (+ optional Kafka consumer) indexes live logs into RAG |
| **Slack bot** | `/al5gae` slash command and `@mention` handler via Socket Mode |
| **Teams bot** | Microsoft Teams integration via Bot Framework SDK (aiohttp) |
| **TCP stream reassembly** | Reconstruct full TCP payloads (e.g., SBI HTTP/2 flows) from PCAPs |
| **LoRA fine-tuning** | Domain-adapt Phi-2 or TinyLlama on your own 5G Q&A pairs |
| **Prometheus bridge** | Alertmanager webhook receiver → model analysis → forward to Slack/Teams. Exposes `/metrics` for scraping |
| **Enhanced observability** | OpenTelemetry tracing across all interfaces, structured JSON logging (ELK/Loki), pre-built Grafana dashboard |
| **GGUF / llama.cpp backend** | Quantized model serving via `llama-cpp-python` for 2–5× faster CPU inference. Auto-detected by file extension |
| **Hybrid BM25 + vector search** | Reciprocal Rank Fusion of BM25 keyword scores and FAISS vector similarity for improved RAG recall |
| **Embedding fine-tuning** | Fine-tune sentence-transformer on 5G domain pairs (query/positive/negative) for better retrieval accuracy |
| **Cross-encoder re-ranking** | Re-scores top RAG candidates with a cross-encoder (ms-marco-MiniLM) for precision. Auto-enabled when `sentence-transformers` is installed |
| **Contextual compression** | LLM-based relevance filter removes off-topic chunks before they reach the prompt, reducing noise |
| **Multi-modal RAG (CLIP)** | Index topology diagrams and screenshots via CLIP embeddings; retrieved alongside text chunks |
| **PDF ingestion** | Extract text from 3GPP specs and vendor manuals (PyMuPDF) for RAG indexing |
| **Confluence crawler** | Crawl a Confluence wiki space and index all pages as plain text |
| **SharePoint crawler** | Download and index files from a SharePoint document library (via Microsoft Graph) |
| **Folder watcher** | Auto re-index when files change in the input directory (watchdog or polling fallback) |
| **gNMI client** | Fetch live configuration and state from 5G core NFs (AMF, SMF, UPF) via gNMI (gRPC / pygnmi) |
| **RESTCONF client** | Query YANG-modelled NFs over HTTPS/JSON with pre-canned 5GC paths (AMF sessions, SMF PDU sessions, etc.) |
| **Kafka telemetry ingestion** | Consume streaming metrics, logs, and traces from Kafka topics; auto-normalise and index into RAG |
| **Root cause correlator** | Combine alerts + logs + PCAPs + telemetry into a timeline and query the model for automated RCA |
| **pyshark deep dissection** | Full Wireshark dissector chain via pyshark — live capture or offline PCAP, with 5G-aware tagging |
| **TLS decryption** | Decrypt TLS traffic using pre-master secret logs (SSLKEYLOGFILE); extract SNI, cipher suites, cert CNs |
| **Flow-based analysis** | 5-tuple aggregation with RTT estimation, retransmission / OOO / dup-ACK detection, anomaly reporting |
| **Conversation export** | Export conversation threads to Markdown or PDF for documentation and audit trails |
| **Commenting & tagging** | Attach feedback comments (with 1–5 ratings) and tags to individual messages or entire threads |
| **Suggested queries** | Context-aware query suggestions based on recent alerts, query history, and common 5G issues |
| **Unit tests** | Comprehensive pytest suite for all core modules using synthetic data (PCAPs, logs, KB) |
| **Integration tests** | End-to-end tests with mock 5G core simulators (gNMI, RESTCONF, Kafka), FastAPI TestClient |
| **Performance benchmarks** | QPS, RAG latency, chunking throughput, PCAP ingestion rate, memory footprint |
| **Dark mode** | Toggle light/dark theme in the web UI; persisted via `localStorage`, respects `prefers-color-scheme` |
| **Mobile-responsive UI** | Adaptive layout with touch-friendly tap targets, iOS zoom prevention, and optimised chat height |
| **Voice input** | Browser-based speech recognition (Web Speech API) for hands-free queries — click the microphone button |

## Installation

```bash
git clone https://github.com/danielnovais-tech/AL-5G-AE.git
cd AL-5G-AE
python -m pip install --upgrade -r requirements.txt
```

Install `tshark` (Wireshark CLI) for deep PCAP dissection:

- Ubuntu: `sudo apt install tshark`
- macOS: `brew install wireshark`
- Windows: install [Wireshark](https://www.wireshark.org/download.html), then add `tshark.exe` to your PATH:
  1. Find the Wireshark install folder (typically `C:\Program Files\Wireshark`)
  2. Open **Settings → System → Environment Variables → Path → Edit** and add that folder
  3. Restart your terminal

Verify: `python -c "import shutil; print(shutil.which('tshark'))"`  
If it prints `None`, `tshark` is not on PATH — PCAP ingestion will fall back to Scapy (still works, just less detail).

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
python kb_builder.py --input-dir ./docs --output-dir ./knowledge_base --extensions .md .log .pdf --clear
```

| Flag | Description |
|---|---|
| `--input-dir` | Source directory (default: `./docs`) |
| `--output-dir` | Output directory (default: `./knowledge_base`) |
| `--extensions` | File types to process (default: `.md .log .pdf`) |
| `--since` | Keep log lines at or after this timestamp |
| `--until` | Keep log lines at or before this timestamp |
| `--log-multiline` | Group stacktraces/multiline entries before filtering |
| `--log-lines` | Keep only the last N lines |
| `--log-regex` | Keep only lines matching a regex |
| `--clear` | Delete output directory before building |
| `--verbose` | Print per-file details |
| `--watch` | After initial build, watch for changes and re-index automatically |
| `--poll-interval` | Polling interval in seconds for `--watch` (default: 2.0) |

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

### Manual deploy

1. Go to [huggingface.co/new-space](https://huggingface.co/new-space)
2. Choose **Gradio** SDK
3. Upload this repository (or connect your GitHub repo)
4. Set environment variables if needed (see table above)

### Automated deploy

```bash
pip install huggingface_hub
huggingface-cli login             # or set HF_TOKEN env var
python deploy_spaces.py            # uses your logged-in HF account
python deploy_spaces.py --owner your-org --space-name al-5g-ae  # custom
python deploy_spaces.py --private  # private Space
```

## GitHub Releases

```bash
pip install PyGithub

# Linux/macOS:
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx

# Windows PowerShell:
# $env:GITHUB_TOKEN = "ghp_xxxxxxxxxxxxxxxxxxxx"

# Preview changelog
python create_release.py --dry-run

# Create v1.0.0 release
python create_release.py

# Create a draft release with a different tag
python create_release.py --tag v1.1.0 --draft
```

## Real-time Log Streaming

Index live 5G core logs into RAG as they arrive — via WebSocket or Kafka.

### WebSocket server

```bash
python stream_ingest.py websocket --rag-dir knowledge_base --port 8765
```

Send log lines from any client:

```python
import asyncio, json, websockets

async def send():
    async with websockets.connect("ws://localhost:8765") as ws:
        await ws.send(json.dumps({"log_line": "2026-04-02T10:00:00 AMF ERROR registration reject cause #15"}))
        print(await ws.recv())

asyncio.run(send())
```

### Kafka consumer (optional)

```bash
pip install kafka-python
python stream_ingest.py kafka --rag-dir knowledge_base --bootstrap-servers localhost:9092 --topic al5gae-logs
```

| Flag | Default | Description |
|---|---|---|
| `--rag-dir` | — | Knowledge-base directory |
| `--buffer-size` | `100` | Lines to buffer before indexing |
| `--host` | `0.0.0.0` | WebSocket bind address |
| `--port` | `8765` | WebSocket port |

## Slack Bot

Query AL-5G-AE from Slack via `/al5gae` slash commands or `@mentions`.

### Setup

1. Create a Slack app at [api.slack.com/apps](https://api.slack.com/apps)
2. Enable **Socket Mode** and create an App-Level Token (`xapp-...`)
3. Add the `/al5gae` slash command
4. Install the app to your workspace and copy the Bot Token (`xoxb-...`)
5. Set environment variables:

```powershell
# PowerShell
$env:SLACK_BOT_TOKEN = "xoxb-..."
$env:SLACK_APP_TOKEN = "xapp-..."
$env:RAG_DIR = "./knowledge_base"      # optional
$env:AL5GAE_MODEL = "microsoft/phi-2"  # optional
```

```bash
# Bash
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_APP_TOKEN=xapp-...
```

6. Run:

```bash
python slack_bot.py
```

Then in Slack: `/al5gae What causes AMF registration reject cause #15?`

## Microsoft Teams Bot

Query AL-5G-AE from Microsoft Teams via `@mentions` or direct messages.

### Setup

1. Register a bot in [Azure Bot Service](https://portal.azure.com/#create/Microsoft.BotServiceConnectivityGallery)
2. Note the **Application (client) ID** and create a **client secret**
3. Set environment variables:

```powershell
# PowerShell
$env:MICROSOFT_APP_ID = "your-app-id"
$env:MICROSOFT_APP_PASSWORD = "your-client-secret"
$env:RAG_DIR = "./knowledge_base"      # optional
$env:AL5GAE_MODEL = "microsoft/phi-2"  # optional
```

```bash
# Bash
export MICROSOFT_APP_ID=your-app-id
export MICROSOFT_APP_PASSWORD=your-client-secret
```

4. Run:

```bash
python teams_bot.py
```

5. Configure the messaging endpoint in Azure Bot Service to `https://yourdomain.com/api/messages` (use [ngrok](https://ngrok.com/) or a reverse proxy for local development)
6. Install the bot in your Teams tenant via the Azure portal or a Teams app manifest

The bot listens on port **3978** by default (override with `PORT` env var).

## TCP Stream Reassembly

Reconstruct full TCP sessions from a PCAP — useful for SBI (HTTP/2) flow analysis.

```bash
# Print reassembled streams to stdout
python pcap_stream_reassembly.py capture.pcapng

# Save to file
python pcap_stream_reassembly.py capture.pcapng --output streams.txt

# Index into RAG
python pcap_stream_reassembly.py capture.pcapng --rag-index --rag-dir knowledge_base
```

Streams are tagged with protocol heuristics (`[HTTP2/SBI]`, `[PFCP]`, `[GTPv2-C]`, `[TCP]`).

## Fine-tuning (LoRA)

Domain-adapt Phi-2 or TinyLlama on your own 5G Q&A dataset.

### Dataset format (JSONL)

```json
{"instruction": "What is PFCP?", "output": "PFCP (Packet Forwarding Control Protocol) is used on the N4 interface between SMF and UPF..."}
{"instruction": "Explain AMF registration reject cause #15", "output": "Cause #15 means no suitable cells in the tracking area..."}
```

### Train

```bash
pip install peft datasets bitsandbytes

python finetune.py --dataset data/5g_qa.jsonl --model microsoft/phi-2 --output-dir ./lora_adapter \
  --epochs 3 --batch-size 4 --lr 2e-4 --lora-r 8 --lora-alpha 32
```

### Use the adapter

```python
from peft import PeftModel
from transformers import AutoModelForCausalLM

base = AutoModelForCausalLM.from_pretrained("microsoft/phi-2")
model = PeftModel.from_pretrained(base, "./lora_adapter")
```

Or set `--model ./lora_adapter` when running the CLI/web UI.

| Flag | Default | Description |
|---|---|---|
| `--dataset` | (required) | JSONL file with `instruction` / `output` |
| `--model` | `microsoft/phi-2` | Base model |
| `--output-dir` | `./lora_adapter` | Where to save the adapter |
| `--epochs` | `3` | Training epochs |
| `--batch-size` | `4` | Per-device batch size |
| `--lr` | `2e-4` | Learning rate |
| `--lora-r` | `8` | LoRA rank |
| `--lora-alpha` | `32` | LoRA alpha |
| `--fp16` / `--no-fp16` | `--fp16` | Half-precision training |

## Prometheus / Grafana Alerting Bridge

Receive alerts from Prometheus Alertmanager, query AL-5G-AE for root-cause analysis, and forward the result to a webhook (Slack, Teams, or any `{"text": "..."}` endpoint).

### Setup

1. Set environment variables:

```powershell
# PowerShell
$env:FORWARD_WEBHOOK_URL = "https://hooks.slack.com/services/xxx"  # or Teams incoming webhook
$env:RAG_DIR = "./knowledge_base"
$env:BRIDGE_PORT = "9090"          # optional, default 9090
$env:AL5GAE_MODEL = "microsoft/phi-2"  # optional
```

```bash
# Bash
export FORWARD_WEBHOOK_URL="https://hooks.slack.com/services/xxx"
export RAG_DIR="./knowledge_base"
```

2. Run:

```bash
python prometheus_bridge.py
```

3. Configure Alertmanager to send webhooks:

```yaml
receivers:
  - name: al5gae
    webhook_configs:
      - url: http://<bridge-host>:9090/webhook
```

### Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/webhook` | POST | Receives Alertmanager JSON payloads |
| `/metrics` | GET | Prometheus scrape endpoint (`al5gae_alerts_received_total`, `al5gae_query_duration_seconds`, `al5gae_rag_hits_total`, …) |
| `/health` | GET | Liveness probe |

### Grafana dashboard ideas

- **Alerts processed** — `rate(al5gae_alerts_processed_total[5m])`
- **Query latency (p95)** — `histogram_quantile(0.95, rate(al5gae_query_duration_seconds_bucket[5m]))`
- **RAG hit rate** — `rate(al5gae_rag_hits_total[5m])`
- **Failure rate** — `rate(al5gae_alerts_failed_total[5m])`

## Enhanced Observability

AL-5G-AE ships with integrated OpenTelemetry tracing, structured JSON logging, and a pre-built Grafana dashboard.

### OpenTelemetry Tracing

Every query — whether from the CLI, Web UI, REST API, Slack, Teams, or Prometheus bridge — is traced end-to-end with span attributes (input length, RAG chunks retrieved, output length).

```powershell
# PowerShell
$env:OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4317"
$env:OTEL_SERVICE_NAME = "al-5g-ae"
```

```bash
# Bash
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"
export OTEL_SERVICE_NAME="al-5g-ae"
```

Then start any interface as usual. Spans will be exported to your OTLP collector (Jaeger, Grafana Tempo, etc.).

If the OpenTelemetry packages are not installed, tracing degrades gracefully to a noop — zero overhead.

### Structured JSON Logging

Set `AL5GAE_LOG_FORMAT=json` to switch all log output to single-line JSON records with `timestamp`, `level`, `logger`, `message`, `trace_id`, and `span_id` fields — ready for ELK, Loki, or any structured-log pipeline.

```bash
export AL5GAE_LOG_FORMAT=json
python api_server.py --rag-dir knowledge_base
```

### Prometheus Metrics

All interfaces emit unified metrics via `prometheus-client`:

| Metric | Type | Labels |
|---|---|---|
| `al5gae_queries_total` | Counter | `interface` |
| `al5gae_query_duration_seconds` | Histogram | `interface` |
| `al5gae_rag_retrievals_total` | Counter | `interface` |
| `al5gae_errors_total` | Counter | `interface` |

The Prometheus bridge additionally exposes `al5gae_alerts_received_total`, `al5gae_alerts_processed_total`, `al5gae_alerts_failed_total`, and `al5gae_rag_hits_total`.

### Grafana Dashboard

Import `grafana_dashboard.json` into Grafana (Dashboards → Import → Upload JSON). It includes:

| Panel | PromQL |
|---|---|
| Query rate per interface | `rate(al5gae_queries_total[5m])` |
| Latency p50 / p95 / p99 | `histogram_quantile(0.95, rate(al5gae_query_duration_seconds_bucket[5m]))` |
| RAG retrievals / sec | `rate(al5gae_rag_retrievals_total[5m])` |
| Error rate | `rate(al5gae_errors_total[5m])` |
| Alertmanager alerts processed | `rate(al5gae_alerts_processed_total[5m])` |
| Model throughput (queries/min) | `sum(rate(al5gae_queries_total[1m])) * 60` |
| Total queries / RAG hits / errors | `sum(al5gae_queries_total)` |

The dashboard uses a `DS_PROMETHEUS` template variable — select your Prometheus data source after import.

## Model & Embedding Improvements

### Quantized Model Serving (GGUF / llama.cpp)

For 2–5× faster CPU inference, use a GGUF-quantized model instead of the default HuggingFace weights:

```bash
# Install the backend (uncomment in requirements.txt or install manually)
pip install llama-cpp-python

# Download a GGUF model (example: Phi-2 Q4_K_M)
# Place it anywhere on disk, then pass the path as --model
python al_5g_ae.py --model ./models/phi-2.Q4_K_M.gguf --rag-dir knowledge_base
```

`load_model()` auto-detects `.gguf` files and uses `llama-cpp-python` instead of transformers. All interfaces (CLI, Web UI, API, Slack, Teams) work transparently with either backend.

| Parameter | Environment Variable | Default |
|---|---|---|
| GGUF context window | `n_ctx` kwarg in `load_model_gguf()` | 2048 |
| GPU offload layers | `n_gpu_layers` | 0 (CPU only) |
| Thread count | `n_threads` | auto |

### Hybrid BM25 + Vector Search

RAG now combines **BM25 keyword matching** with **FAISS vector similarity** using Reciprocal Rank Fusion (RRF). This improves recall for queries containing exact protocol names, error codes, or field identifiers that pure semantic search may miss.

```python
from al_5g_ae_core import RAG

# Hybrid is enabled automatically when rank_bm25 is installed
rag = RAG(hybrid=True, rrf_k=60)
rag.add_file("knowledge_base/ts_23501.txt")
results = rag.retrieve("PFCP Session Establishment Request", k=5)
```

To disable hybrid search: `RAG(hybrid=False)`.

Install:
```bash
pip install rank-bm25
```

### Embedding Model Fine-Tuning

Fine-tune `all-MiniLM-L6-v2` (or any sentence-transformer) on 5G domain pairs to improve retrieval accuracy:

```bash
# Prepare a JSONL dataset:
# {"query": "What is PFCP?", "positive": "PFCP (Packet Forwarding Control Protocol) is used between SMF and UPF..."}
# Optionally add "negative" for harder negatives (uses TripletLoss instead of MultipleNegativesRankingLoss)

python finetune.py --embedding \
  --dataset 5g_embedding_pairs.jsonl \
  --model all-MiniLM-L6-v2 \
  --output-dir ./embedding_finetuned \
  --epochs 3 --batch-size 16 --lr 2e-5
```

Then use the fine-tuned model in RAG:
```python
rag = RAG(embedding_model="./embedding_finetuned")
```

## Advanced RAG

Three optional enhancements improve retrieval quality beyond hybrid BM25 + vector search.

### Cross-encoder re-ranking

When `sentence-transformers` is installed (already in `requirements.txt`), the RAG pipeline automatically re-ranks candidates with `cross-encoder/ms-marco-MiniLM-L-6-v2`. The top 4×k candidates from BM25/FAISS fusion are scored pairwise against the query; only the best k survive.

```python
# Enabled by default. To disable:
rag = RAG(rerank=False)
# Custom cross-encoder model:
rag = RAG(rerank_model="cross-encoder/ms-marco-TinyBERT-L-2-v2")
```

### Contextual compression

For noisy knowledge bases (e.g., raw vendor logs mixed with specs), enable contextual compression. This uses the loaded LLM to judge each chunk as RELEVANT or IRRELEVANT before building the final prompt.

```python
rag = RAG(contextual_compression=True)
chunks = rag.retrieve(query, k=5)
# Filter with the loaded model
filtered = RAG.compress_chunks(query, chunks, tokenizer, model)
answer = generate_response(tokenizer, model, user_input, filtered)
```

> **Note:** Contextual compression adds one LLM call per chunk. Best used with small k values (3–5) or fast backends (GGUF).

### Multi-modal RAG (CLIP)

Index images (topology diagrams, architecture screenshots, Grafana panels) alongside text. Requires `Pillow` and `transformers` (both in `requirements.txt`).

```python
rag = RAG()
rag.add_image_dir("./diagrams/")          # Index all PNG/JPG/WEBP files
rag.add_file("knowledge_base/ts_23501.txt")  # Mix text + images
results = rag.retrieve("UPF N3 interface topology")
# Results include both text chunks and image references:
# [image: upf_topology.png (score: 0.312)] ./diagrams/upf_topology.png
```

Supported image formats: `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.webp`.

The CLIP model (`openai/clip-vit-base-patch32`) is loaded lazily on first `add_image()` call.

```python
# Custom CLIP model:
rag = RAG(clip_model="openai/clip-vit-large-patch14")
```

## Automated Knowledge Base Curation

### PDF Support

Extract text from 3GPP specs, vendor manuals, and any PDF documents:

```bash
# Include PDFs in a normal build
python kb_builder.py --input-dir ./specs --output-dir ./knowledge_base --extensions .pdf .md --clear

# PDF extraction uses PyMuPDF (fitz) — install if needed:
pip install PyMuPDF
```

Each page is extracted as `--- Page N ---` blocks. The output is a single `.txt` file per PDF.

### Confluence Crawler

Crawl an entire Confluence wiki space and save each page as plain text:

```bash
# Set credentials
export CONFLUENCE_USER="your-email@example.com"
export CONFLUENCE_TOKEN="your-api-token"

# Crawl
python kb_builder.py \
  --confluence-url https://wiki.example.com \
  --confluence-space 5GCore \
  --output-dir ./knowledge_base \
  --confluence-max-pages 500 \
  --verbose
```

| Flag | Description |
|---|---|
| `--confluence-url` | Confluence base URL |
| `--confluence-space` | Space key to crawl |
| `--confluence-user` | Username (or `CONFLUENCE_USER` env var) |
| `--confluence-token` | API token (or `CONFLUENCE_TOKEN` env var) |
| `--confluence-max-pages` | Max pages to retrieve (default: 500) |

HTML tags in Confluence storage format are stripped automatically.

### SharePoint Crawler

Download files from a SharePoint document library using Microsoft Graph:

```bash
# Set credentials (Azure AD app registration with Sites.Read.All permission)
export SHAREPOINT_CLIENT_ID="your-client-id"
export SHAREPOINT_CLIENT_SECRET="your-client-secret"
export SHAREPOINT_TENANT_ID="your-tenant-id"

# Crawl
python kb_builder.py \
  --sharepoint-site https://org.sharepoint.com/sites/5GOperations \
  --sharepoint-library "Shared Documents" \
  --output-dir ./knowledge_base \
  --sharepoint-max-files 500 \
  --verbose
```

| Flag | Description |
|---|---|
| `--sharepoint-site` | SharePoint site URL |
| `--sharepoint-library` | Document library name (default: `"Shared Documents"`) |
| `--sharepoint-max-files` | Max files to download (default: 500) |

PDF files are automatically converted to text. Other supported types: `.txt`, `.md`, `.log`, `.docx`.

### Automatic Folder Watching

After the initial build, keep the knowledge base in sync with source changes:

```bash
# Build, then watch for changes
python kb_builder.py --input-dir ./docs --output-dir ./knowledge_base --watch --verbose

# Custom polling interval (if watchdog is not installed)
python kb_builder.py --input-dir ./docs --output-dir ./knowledge_base --watch --poll-interval 5.0
```

When `watchdog` is installed, file system events trigger immediate re-indexing. Without it, a simple mtime-based polling loop detects changes at `--poll-interval` intervals.

New, modified, or deleted files in `--input-dir` are automatically processed, updated, or removed from `--output-dir`.

---

### Real-time 5G Core Integration

`realtime_5gc.py` provides live integration with 5G core network functions and automated root-cause correlation.

#### gNMI client

Fetch configuration and operational state from gNMI-enabled NFs:

```bash
# Install gNMI dependencies
pip install pygnmi grpcio

# Query AMF state (env vars or CLI flags)
export GNMI_TARGET="amf.lab:57400"
export GNMI_USER="admin"
export GNMI_PASSWORD="admin"
python realtime_5gc.py gnmi /amf/ue-contexts /amf/n2-connections

# With explicit flags
python realtime_5gc.py gnmi --target amf.lab:57400 --user admin --password admin --insecure /amf/ue-contexts
```

#### RESTCONF client

Query YANG-modelled NFs over HTTPS/JSON:

```bash
export RESTCONF_BASE_URL="https://smf.lab:443"
export RESTCONF_USER="admin"
export RESTCONF_PASSWORD="admin"
python realtime_5gc.py restconf ietf-smf:smf/pdu-sessions
```

Pre-canned 5GC paths: `amf-sessions`, `smf-sessions`, `upf-interfaces`, `nrf-nf-instances`, `pcf-policies`.

#### Kafka streaming telemetry

Consume metrics, logs, and traces from Kafka and index into RAG:

```bash
pip install kafka-python

export KAFKA_BOOTSTRAP="kafka.lab:9092"
export KAFKA_TOPICS="5gc-metrics,5gc-logs,5gc-traces"
python realtime_5gc.py kafka --rag-dir ./knowledge_base
```

Events are auto-classified as `metric`, `log`, `trace`, or `alert` based on JSON field heuristics.

#### Root cause correlation

Combine multiple data sources into a single timeline and get automated RCA:

```bash
python realtime_5gc.py correlate \
  --alerts-file alerts.json \
  --log-file amf.log \
  --pcap-file capture.pcap \
  --rag-dir ./knowledge_base \
  --model microsoft/phi-2
```

The correlator:
1. Ingests alerts (Alertmanager JSON), logs (text), and PCAP summaries
2. Sorts everything into a chronological timeline
3. Retrieves relevant KB context via RAG
4. Queries the model for root cause, affected NFs, and remediation steps

Programmatic usage:

```python
from realtime_5gc import RootCauseCorrelator, GNMIClient, TelemetryConsumer

correlator = RootCauseCorrelator()
correlator.add_alerts(alertmanager_alerts)
correlator.add_logs(log_lines)
correlator.add_pcap_summaries(pcap_summaries)

# Optionally enrich with live gNMI / RESTCONF data
gnmi = GNMIClient(target="amf.lab:57400")
correlator.add_gnmi_snapshot(gnmi, ["/amf/ue-contexts"])

timeline = correlator.build_timeline()
answer = correlator.analyse(tokenizer=tok, model=mdl, rag=rag)
```

| Environment Variable | Description | Default |
|---|---|---|
| `GNMI_TARGET` | gNMI target (host:port) | `localhost:57400` |
| `GNMI_USER` / `GNMI_PASSWORD` | gNMI credentials | `admin` / `admin` |
| `GNMI_TLS_CERT` | Path to TLS client certificate | — |
| `RESTCONF_BASE_URL` | RESTCONF base URL | `https://localhost:443` |
| `RESTCONF_USER` / `RESTCONF_PASSWORD` | RESTCONF credentials | `admin` / `admin` |
| `KAFKA_BOOTSTRAP` | Kafka bootstrap servers | `localhost:9092` |
| `KAFKA_TOPICS` | Comma-separated topics | `5gc-telemetry` |
| `AL5GAE_MODEL` | Model name or GGUF path | `microsoft/phi-2` |
| `RAG_DIR` | Knowledge base directory | `./knowledge_base` |

## Advanced Packet Analysis

`pcap_advanced.py` adds three capabilities on top of the existing PCAP pipeline:

### pyshark + tshark Deep Dissection

Uses pyshark (Python wrapper around tshark) for full Wireshark dissector access, including on-the-fly dissection with display filters:

```bash
# Offline dissection with 5G-aware summaries
python pcap_advanced.py dissect capture.pcap --filter "ngap || pfcp" --max-packets 2000

# Live capture from an interface
python pcap_advanced.py live eth0 --filter "http2" --timeout 60 --count 500

# With TLS decryption
python pcap_advanced.py dissect capture.pcap --tls-keylog /tmp/sslkeys.log

# With decode-as overrides
python pcap_advanced.py dissect capture.pcap --decode-as "tcp.port==29510:http2"
```

Programmatic use:

```python
from pcap_advanced import dissect_to_summaries, dissect_live

# Offline — returns RAG-friendly text summaries
summaries = dissect_to_summaries("capture.pcap", tls_keylog="/tmp/keys.log")

# Live — returns list of dicts with full layer info
packets = dissect_live("eth0", display_filter="pfcp", timeout=30)
```

### TLS Decryption

Decrypt TLS traffic with pre-master secret logs (set `SSLKEYLOGFILE` env var in your 5GC NFs):

```bash
# Produce a decrypted PCAP file
python pcap_advanced.py decrypt capture.pcap /tmp/sslkeys.log --output decrypted.pcapng

# Extract TLS handshake metadata (SNI, cipher suites, cert SANs)
python pcap_advanced.py tls-meta capture.pcap --tls-keylog /tmp/sslkeys.log
```

Programmatic:

```python
from pcap_advanced import decrypt_pcap, extract_tls_metadata

decrypt_pcap("capture.pcap", "sslkeys.log", output_path="decrypted.pcapng")
meta = extract_tls_metadata("capture.pcap", keylog_file="sslkeys.log")
```

### Flow-based Analysis

5-tuple flow aggregation with RTT, retransmissions, out-of-order packets, duplicate ACKs, and anomaly detection:

```bash
# Per-flow summary (sorted by bytes)
python pcap_advanced.py flows capture.pcap --max-packets 100000

# JSON output
python pcap_advanced.py flows capture.pcap --json

# Anomalies only (retransmissions, RSTs, high RTT, SYN-only)
python pcap_advanced.py flows capture.pcap --anomalies
```

Programmatic:

```python
from pcap_advanced import analyse_flows, flow_anomaly_report, flows_to_summaries

flows = analyse_flows("capture.pcap", tls_keylog="sslkeys.log")
for f in flows:
    print(f.to_summary())

# Detect problematic flows
for issue in flow_anomaly_report(flows):
    print(issue)  # e.g. "ANOMALY: [SBI] 10.0.0.1:29510 -> 10.0.0.2:443 TCP — retransmissions=42 (3.1%), high p95 RTT=150.2ms"

# Feed into RAG
from al_5g_ae_core import RAG
rag = RAG()
rag.add_documents(flows_to_summaries(flows))
```

The flow analyser uses tshark when available (more accurate TCP analysis fields) and falls back to Scapy with sequence-number heuristics.

5G-aware port tagging recognises PFCP (8805), GTPv2-C (2123), GTP-U (2152), NGAP/SCTP (38412), SBI (29510, 29518, etc.), and Diameter (3868).

## Collaboration & Knowledge Sharing

The `collaboration.py` module provides conversation management, export, feedback, and query suggestions.

### Thread management

All endpoints are registered on the API server by calling `register_collaboration_routes(app)` from `api_server.py`.

| Endpoint | Method | Description |
|---|---|---|
| `/threads` | POST | Create a new conversation thread |
| `/threads` | GET | List all thread IDs |
| `/threads/{id}` | GET | Retrieve a full thread (messages, comments, tags) |
| `/threads/{id}` | DELETE | Delete a thread |
| `/threads/{id}/messages` | POST | Add a message (user or assistant) |
| `/threads/{id}/messages/{mid}/comments` | POST | Add a feedback comment (author, text, rating 1–5) |
| `/threads/{id}/messages/{mid}/tags` | POST | Tag a specific message |
| `/threads/{id}/tags` | POST | Tag the entire thread |
| `/threads/{id}/export/markdown` | GET | Download thread as `.md` file |
| `/threads/{id}/export/pdf` | GET | Download thread as `.pdf` file (requires `fpdf2`) |
| `/suggestions` | GET | Get suggested queries (`?n=5`) |
| `/suggestions/alert` | POST | Feed Alertmanager alerts to improve suggestions |

### Programmatic usage

```python
from collaboration import (
    ConversationThread, ThreadStore, QuerySuggester,
    export_markdown, export_pdf,
)

# Create and populate a thread
thread = ConversationThread(title="PDU Session Failure Investigation")
thread.add_message("user", "Why are PDU sessions failing on SMF-01?")
thread.add_message("assistant", "The SMF logs show PFCP association lost with UPF-03...")
thread.add_tag("pfcp")
thread.add_tag("smf")

# Attach feedback
thread.add_comment(thread.messages[1].message_id, author="jdoe", text="Good catch!", rating=5)

# Export
export_markdown(thread)        # → Markdown string
export_pdf(thread, "out.pdf")  # → PDF file

# Persist
store = ThreadStore("./threads")
store.save(thread)
loaded = store.load(thread.thread_id)

# Suggested queries
qs = QuerySuggester()
qs.record_query("Why are PDU sessions failing?")
qs.record_alert({"labels": {"alertname": "PFCPDown", "instance": "upf-03"}, "annotations": {"summary": "PFCP association lost"}})
print(qs.suggest(5))
# → ["Alert 'PFCPDown' on upf-03: PFCP association lost. What could be the root cause...?",
#    "Why are pdu sessions failing?", ...common 5G questions...]
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `THREAD_STORE_DIR` | `./threads` | Directory for JSON thread persistence |

## Testing & Validation Suite

The project includes a comprehensive test suite under `tests/`.

### Running Tests

```bash
# All tests
pytest

# Unit tests only
pytest tests/test_core.py tests/test_pcap.py tests/test_kb_builder.py tests/test_collaboration.py tests/test_observability.py

# Integration tests (mock 5G core)
pytest tests/test_integration.py

# Performance benchmarks (with output)
pytest tests/test_benchmarks.py -s
```

### Test Modules

| Module | Covers | Key tests |
|---|---|---|
| `test_core.py` | `al_5g_ae_core.py` | Chunking (semantic, multiline, auto-detect), RAG CRUD, hybrid BM25+vector, RRF fusion, generate_response (mocked model) |
| `test_pcap.py` | `pcap_ingest.py`, `pcap_stream_reassembly.py` | Scapy ingestion, protocol tagging (PFCP/GTPv2-C/GTP-U/NGAP), label formatting, TCP stream reassembly |
| `test_kb_builder.py` | `kb_builder.py` | Markdown stripping, file processing (.md/.txt/.log), log slicing, PDF extraction (mocked), full pipeline |
| `test_collaboration.py` | `collaboration.py` | Thread CRUD, commenting with ratings, tagging, Markdown/PDF export, query suggester (alerts + frequency + cold-start) |
| `test_observability.py` | `observability.py` | JSON formatter, noop tracer, Prometheus helpers, structured logging config |
| `test_integration.py` | End-to-end flows | Query+RAG pipeline, API `/query`+`/health`, Prometheus bridge webhook, root-cause correlator, mock gNMI/RESTCONF/Kafka |
| `test_benchmarks.py` | Performance | QPS (mock model), RAG index/retrieval latency, chunking KB/s, PCAP packets/sec, memory footprint |

### Synthetic Data

`tests/conftest.py` provides generators for offline testing:
- `MockTokenizer` / `MockModel` — HuggingFace-compatible fakes
- `create_synthetic_pcap()` — valid pcap with Ethernet+IP+UDP/TCP frames
- `create_synthetic_logs()` — timestamped multi-component log lines
- `create_synthetic_kb()` — minimal knowledge-base directory
- `create_alertmanager_payload()` — Alertmanager webhook JSON

## User Experience Enhancements

The web UI includes three UX improvements that require **no extra dependencies** — they are pure CSS + browser JS injected into Gradio.

### Dark mode

A toggle button (top-right corner) switches between light and dark themes.

- **Auto-detect**: respects the OS `prefers-color-scheme` setting on first visit.
- **Persistent**: choice saved in `localStorage` and restored on reload.
- **Full coverage**: backgrounds, text, inputs, chat bubbles, code blocks all adapt.

### Mobile-responsive interface

- Breakpoints at 768 px and 480 px with optimised padding, font sizes, and chat height.
- Touch-friendly: minimum 44 × 44 px tap targets on `pointer: coarse` devices.
- iOS zoom prevention: text inputs use `font-size: 16px` on mobile.

### Voice input (Web Speech API)

A floating microphone button (bottom-right) activates the browser's built-in speech recognition.

- **Click to speak** — transcribed text fills the input box in real time.
- **Click again to stop** — or wait for the recognition to end automatically.
- **Interim results** shown in a small status badge.
- **Browser support**: Chrome, Edge, Safari (desktop & mobile). Firefox does not support the Web Speech API.
- No server-side processing — all recognition runs locally in the browser.

```bash
# Just launch the web UI — all enhancements are built in
python web_ui.py --rag-dir knowledge_base
```

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

- [x] Deploy to Hugging Face Spaces (automated via `deploy_spaces.py`)
- [x] GitHub release v1.0.0 (automated via `create_release.py`)
- [x] Real-time log streaming (WebSocket + Kafka via `stream_ingest.py`)
- [x] Fine-tune the SLM (LoRA via `finetune.py`)
- [x] Slack bot integration (`slack_bot.py`)
- [x] TCP stream reassembly (`pcap_stream_reassembly.py`)
- [x] Microsoft Teams bot integration (`teams_bot.py`)
- [x] Prometheus / Grafana alerting bridge (`prometheus_bridge.py`)
- [x] Enhanced observability — OpenTelemetry, JSON logging, Grafana dashboard (`observability.py`, `grafana_dashboard.json`)
- [x] Quantized model serving (GGUF) — `llama-cpp-python` backend in `al_5g_ae_core.py`
- [x] Hybrid BM25 + vector search — Reciprocal Rank Fusion in `RAG.retrieve()`
- [x] Embedding model fine-tuning — `finetune.py --embedding`
- [x] Cross-encoder re-ranking — `RAG(rerank=True)` with ms-marco-MiniLM
- [x] Contextual compression — `RAG.compress_chunks()` LLM-based relevance filter
- [x] Multi-modal RAG — CLIP image indexing via `RAG.add_image()` / `add_image_dir()`
- [x] PDF support — PyMuPDF text extraction in `kb_builder.py`
- [x] Confluence wiki crawler — `kb_builder.py --confluence-url --confluence-space`
- [x] SharePoint document library crawler — `kb_builder.py --sharepoint-site`
- [x] Automatic folder watching — `kb_builder.py --watch` with watchdog or polling fallback
- [x] gNMI / RESTCONF client — live config & state from 5G core NFs (`realtime_5gc.py`)
- [x] Streaming telemetry (Kafka) — metrics, logs, and traces ingestion into RAG
- [x] Root cause correlation — combine alerts, logs, PCAPs, and telemetry for automated RCA
- [x] pyshark + tshark deep dissection — live and offline with full Wireshark dissectors (`pcap_advanced.py`)
- [x] TLS decryption — SSLKEYLOGFILE support, handshake metadata extraction
- [x] Flow-based analysis — 5-tuple aggregation, RTT, retransmissions, anomaly detection
- [x] Conversation export (Markdown + PDF) — `collaboration.py` with `export_markdown()` / `export_pdf()`
- [x] Commenting & tagging — per-message feedback with ratings, thread/message tags
- [x] Suggested queries — alert-driven + frequency-based + common 5G cold-start suggestions
- [x] Unit tests — comprehensive pytest suite for all core modules (`tests/test_core.py`, `test_pcap.py`, `test_kb_builder.py`, `test_collaboration.py`, `test_observability.py`)
- [x] Integration tests — end-to-end flows with mock 5G core simulators (`tests/test_integration.py`)
- [x] Performance benchmarks — QPS, RAG latency, chunking throughput, memory usage (`tests/test_benchmarks.py`)
- [x] Dark mode — toggle light/dark theme in web UI with `localStorage` persistence
- [x] Mobile-responsive interface — adaptive CSS breakpoints, touch-friendly targets
- [x] Voice input — browser speech recognition (Web Speech API) for hands-free queries
