# AL-5G-AE

AL-5G-AE is a highly specialized small language model (SLM) copilot for 5G Core operations. It is designed to punch above its weight by combining a lightweight conversational model with a domain-focused system prompt tailored to telecom engineering workflows.

## Best Fit Use Cases

- 5G Core troubleshooting and Root Cause Analysis (RCA)
- Interpreting logs, alarms, and signaling traces
- Explaining AMF, SMF, UPF, NRF, PCF, and related workflows
- Assisting engineers with packet captures and protocol flows
- Telecom Q&A and internal knowledge support
- Supporting field engineers and NOC/SOC workflows
- Acting as a specialist copilot for 5G Core operations

## Getting Started

### 1. Install dependencies

It is recommended to use a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Run the assistant

```bash
python al_5g_ae.py
```

The first run will download the model from Hugging Face (may take a few minutes).

### 3. CLI arguments

| Argument | Default | Description |
|---|---|---|
| `--model <name>` | `microsoft/phi-2` | Hugging Face model name or local path (falls back to TinyLlama if load fails) |
| `--device <cpu\|cuda>` | `cpu` | Device to run inference on |
| `--max-tokens <int>` | `512` | Maximum number of new tokens to generate |
| `--temperature <float>` | `0.7` | Sampling temperature (lower = more deterministic) |
| `--rag-dir <path>` | *(none)* | Index a directory of `.txt` files (or a single text file) for retrieval (RAG knowledge base) |
| `--log-file <path>` | *(none)* | Load a log file; it is indexed only if RAG is enabled |
| `--run-log <path>` | `logs/al_5g_ae.log` | Write a run log (queries, retrieval count, errors). Set to empty string to disable |
| `--verbose` | *(off)* | Enable verbose logging (debug-level) |
| `--query <text>` | *(none)* | Single-shot question (non-interactive mode) |

**Examples:**

```bash
# Use a GPU for much faster inference
python al_5g_ae.py --device cuda

# Use a different model
python al_5g_ae.py --model microsoft/phi-2

# Increase response length and reduce randomness
python al_5g_ae.py --max-tokens 1024 --temperature 0.5

# Enable RAG from local docs/runbooks
python al_5g_ae.py --rag-dir ./knowledge_base

# Ingest a log file and ask questions about it
python al_5g_ae.py --log-file ./logs/amf.log

# Single-shot query
python al_5g_ae.py --query "What causes AMF registration reject?" --rag-dir ./knowledge_base
```

## How to Use the New Features

### Basic CLI (no RAG)

```bash
python al_5g_ae.py
```

### With RAG (knowledge base)

```bash
python al_5g_ae.py --rag-dir ./3gpp_specs
```

This indexes `.txt` files in the directory and retrieves relevant chunks for each query.

### With Log File Ingestion

```bash
python al_5g_ae.py --log-file ./amf_log.txt
```

The log is indexed (if RAG is enabled) or just loaded for later manual queries.

### Single-shot Query

```bash
python al_5g_ae.py --query "What causes AMF registration reject?" --rag-dir ./knowledge
```

## Web interface

Run the Gradio web UI:

```bash
python web_ui.py --rag-dir ./knowledge
```

`web_ui.py` is the supported web entrypoint.

Then open `http://localhost:7860`.

This repo also ships a small starter knowledge base you can try immediately:

```bash
python web_ui.py --rag-dir ./knowledge_base
```

Troubleshooting browser JS errors (e.g., `Dft.clearMarks is not a function`):

```bash
# Enable debug mode (more verbose logs + errors displayed in UI)
python web_ui.py --debug

# If the error persists, isolate with a minimal UI (no chat components)
python web_ui.py --minimal-ui --debug
```

If you still see the error even with `--minimal-ui`, it’s almost always browser-side (cache and/or an extension injecting scripts).

Quick fixes:

- Open the page in an Incognito/Private window
- Hard refresh (`Ctrl+Shift+R`)
- Temporarily disable React DevTools (and other React/DOM inspector extensions)

This warning is typically cosmetic and does not affect the assistant’s functionality.

## Starter knowledge base

`knowledge_base/` is included as a non-copyrighted starter pack (original notes, checklists, and filters).

- Use it as-is: `python al_5g_ae.py --rag-dir ./knowledge_base`
- Add your own runbooks/postmortems and point `--rag-dir` at that folder.

## Logging

By default, the CLI writes a run log to `logs/al_5g_ae.log` (and the web UI to `logs/al_5g_ae_web.log`).

```bash
# Disable run logging
python al_5g_ae.py --run-log ""

# Use a custom run log path
python al_5g_ae.py --run-log ./logs/session-001.log

# Verbose (debug-level) logging
python al_5g_ae.py --verbose
```

## Docker

Build and run the Gradio web UI in a container:

```bash
docker build -t al-5g-ae .
docker run --rm -p 7860:7860 -v al5gae_data:/data al-5g-ae
```

Then open `http://localhost:7860`.

Notes:

- Model weights and embedding caches are stored under `/data` in the container.
- The model is lazy-loaded on first request in the web UI.

## Hugging Face Spaces

This repo includes an `app.py` entrypoint suitable for **Gradio Spaces**.

- Entry file: `app.py`
- Optional environment variables:
	- `AL5GAE_MODEL` (defaults to `microsoft/phi-2`)
	- `AL5GAE_DEVICE` (`cpu` or `cuda`)
	- `AL5GAE_RAG_DIR` (defaults to `knowledge_base`)

If your Space is CPU-only or memory-constrained, consider setting `AL5GAE_MODEL` to a smaller chat model.

## How It Works

1. A causal language model is loaded via Transformers.
2. A domain-specific system prompt encodes the assistant’s operating principles and troubleshooting workflow.
3. Optional RAG builds a local vector index from your `.txt` knowledge-base files (and optionally the provided log file) and retrieves relevant chunks per query.
	- Chunking is sentence-aware (NLTK-backed when available, with a safe fallback).
4. The interactive loop keeps the conversation going until you type `quit` or `exit` (or press Ctrl-D).

## Customization

- **Model** – Use `--model` to swap models.
- **System Prompt** – Edit `SYSTEM_PROMPT` in `al_5g_ae.py`.
- **RAG / Knowledge Base** – Point `--rag-dir` at a folder with your runbooks/spec excerpts and use `--log-file` for session logs.

## Notes

- Some chat-tuned models may not use a tokenizer chat template; this assistant uses a simple prompt format.
- If you run into memory issues, try a smaller model or enable CPU offloading.
- Swap to `--device cuda` for significantly faster response times when a GPU is available.
