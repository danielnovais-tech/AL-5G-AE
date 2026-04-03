# Docker image for AL-5G-AE (Gradio web UI)
# Note: Model weights are downloaded at runtime (first request).

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/data/hf \
    TRANSFORMERS_CACHE=/data/hf \
    SENTENCE_TRANSFORMERS_HOME=/data/hf

WORKDIR /app

# System deps (minimal). Add build-essential only if your env needs compilation.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git ca-certificates tshark \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/requirements.txt

# Best-effort: pre-download NLTK tokenizer data used by semantic chunking.
RUN python -c "import nltk; nltk.download('punkt', quiet=True)" || true

COPY . /app

EXPOSE 7860

# Default: run the supported web UI and index the bundled knowledge base.
CMD ["python", "web_ui.py", "--host", "0.0.0.0", "--port", "7860", "--rag-dir", "./knowledge_base"]
