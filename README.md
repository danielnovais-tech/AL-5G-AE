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
| `--model <name>` | `TinyLlama/TinyLlama-1.1B-Chat-v1.0` | Hugging Face model name or local path |
| `--device <cpu\|cuda>` | `cpu` | Device to run inference on |
| `--max-tokens <int>` | `512` | Maximum number of new tokens to generate |
| `--temperature <float>` | `0.7` | Sampling temperature (lower = more deterministic) |

**Examples:**

```bash
# Use a GPU for much faster inference
python al_5g_ae.py --device cuda

# Use a different model
python al_5g_ae.py --model microsoft/phi-2

# Increase response length and reduce randomness
python al_5g_ae.py --max-tokens 1024 --temperature 0.5
```

## How It Works

1. A small causal language model (TinyLlama, 1.1 B parameters) is loaded — it fits on most consumer hardware without a GPU.
2. A domain-specific system prompt hard-codes the assistant's personality and expertise (5G Core, logs, protocols, etc.).
3. Each user query is wrapped in a chat-style prompt (`<|system|>` / `<|user|>` / `<|assistant|>`) and fed to the model.
4. The interactive loop keeps the conversation going until you type `quit` or `exit` (or press Ctrl-D).

## Customization

- **Model** – Replace `DEFAULT_MODEL` in `al_5g_ae.py` with any Hugging Face causal LM, e.g. `microsoft/phi-2`, `Qwen/Qwen2.5-0.5B-Instruct`, or your own fine-tuned checkpoint.
- **System Prompt** – Edit `SYSTEM_PROMPT` in `al_5g_ae.py` to emphasize specific use cases, add company-specific knowledge, or include example outputs.
- **RAG / Knowledge Base** – For production, extend the script with retrieval-augmented generation (RAG) using a vector database of 3GPP specs, internal documents, or known troubleshooting cases.

## Notes

- TinyLlama uses the `<|system|>`, `<|user|>`, `<|assistant|>` chat format, which matches its training.
- If you run into memory issues, try a smaller model or enable CPU offloading.
- Swap to `--device cuda` for significantly faster response times when a GPU is available.
