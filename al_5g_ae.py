#!/usr/bin/env python3
"""\
AL-5G-AE – 5G Core specialist copilot with RAG, log ingestion, and web UI support.
"""

import argparse
import logging
import sys
import warnings
from pathlib import Path
import re
from typing import List, Optional

warnings.filterwarnings("ignore", category=UserWarning, module="transformers")

# ---- Dependencies check ----
try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
except ImportError:
    print("ERROR: Install required packages: transformers torch")
    sys.exit(1)

try:
    import faiss
    import numpy as np
    from sentence_transformers import SentenceTransformer

    RAG_AVAILABLE = True
except ImportError:
    RAG_AVAILABLE = False
    print("Warning: RAG not available. Install faiss-cpu, sentence-transformers, numpy")

# ---- Configuration ----
DEFAULT_MODEL = "microsoft/phi-2"  # Good small model (2.7B)
# Fallback to TinyLlama if phi-2 is too heavy
FALLBACK_MODEL = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
DEFAULT_DEVICE = "cpu"
MAX_CONTEXT_TOKENS = 2048
CHUNK_SIZE = 512
CHUNK_OVERLAP = 50

SYSTEM_PROMPT = """You are AL-5G-AE, a highly specialized assistant for 5G Core operations.
Your expertise covers:
- 5G Core network functions: AMF, SMF, UPF, NRF, PCF, NSSF, AUSF, UDM
- Protocols: NGAP, GTP-U, GTPv2, PFCP, HTTP/2, SBI, NAS
- Troubleshooting: log analysis (vendor: Nokia, Ericsson, Huawei), alarm interpretation, signaling traces
- Call flows: registration, PDU session establishment, handover
- Packet captures: Wireshark filters, protocol dissection

When answering, be concise but thorough. If you use retrieved knowledge, mention the source.
If you are unsure, state so and suggest steps to investigate.
"""


def setup_run_logger(log_path: Optional[str], *, verbose: bool = False) -> logging.Logger:
    """Configure a simple file + stderr logger.

    This is intentionally lightweight (no external deps) and safe to call multiple times.
    """
    logger = logging.getLogger("al_5g_ae")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    stream_handler = logging.StreamHandler(stream=sys.stderr)
    stream_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if log_path:
        log_file = Path(log_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


# ---- Helper functions ----
def load_model(model_name, device):
    """Load tokenizer and model."""
    print(f"Loading model {model_name}...", file=sys.stderr)
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
        device_map="auto" if device == "cuda" else None,
        low_cpu_mem_usage=True,
    )
    if device == "cpu":
        model = model.to("cpu")
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer, model


def chunk_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    """Split text into overlapping chunks.

    This chunker is paragraph/sentence-aware to preserve meaning better than naive
    fixed-word windows.

    `chunk_size` and `overlap` are treated as approximate *word* counts.
    """
    return _chunk_text_semantic(text, chunk_size_words=chunk_size, overlap_words=overlap)


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")

_NLTK_STATE = {"ready": False, "disabled": False}


def _try_get_nltk_sentences(text: str) -> Optional[List[str]]:
    """Return sentence list using NLTK if available, else None.

    We attempt to ensure the punkt tokenizer exists; on failure we fall back.
    """
    if _NLTK_STATE["disabled"]:
        return None
    try:
        import nltk  # type: ignore
        from nltk.tokenize import sent_tokenize  # type: ignore

        if not _NLTK_STATE["ready"]:
            try:
                nltk.data.find("tokenizers/punkt")
            except LookupError:
                # Best-effort download; may fail in offline environments.
                nltk.download("punkt", quiet=True)
            _NLTK_STATE["ready"] = True

        sentences = [s.strip() for s in sent_tokenize(text) if s.strip()]
        return sentences or None
    except Exception:
        _NLTK_STATE["disabled"] = True
        return None


def _split_into_sentences(text: str) -> List[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Prefer NLTK's tokenizer if available.
    nltk_sentences = _try_get_nltk_sentences(text)
    if nltk_sentences:
        return nltk_sentences

    # First split into paragraphs to avoid gluing unrelated sections together.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    sentences: List[str] = []
    for paragraph in paragraphs:
        # If the paragraph is mostly log lines / key=value blocks, keep line-level.
        lines = [ln.strip() for ln in paragraph.split("\n") if ln.strip()]
        if len(lines) >= 3 and sum(1 for ln in lines[:10] if ":" in ln or "=" in ln) >= 2:
            sentences.extend(lines)
            continue

        parts = [s.strip() for s in _SENTENCE_SPLIT_RE.split(paragraph) if s.strip()]
        sentences.extend(parts if parts else [paragraph])
    return sentences


def _chunk_text_semantic(text: str, *, chunk_size_words: int, overlap_words: int) -> List[str]:
    if chunk_size_words <= 0:
        return []
    overlap_words = max(0, min(overlap_words, chunk_size_words - 1))

    sentences = _split_into_sentences(text)
    if not sentences:
        return []

    chunks: List[str] = []
    current: List[str] = []
    current_words = 0

    def flush():
        nonlocal current, current_words
        if not current:
            return
        chunk = "\n".join(current).strip()
        if chunk:
            chunks.append(chunk)

        if overlap_words <= 0:
            current = []
            current_words = 0
            return

        # Keep an overlap tail by words (approx.)
        tail: List[str] = []
        tail_words = 0
        for item in reversed(current):
            item_words = len(item.split())
            if tail_words + item_words > overlap_words and tail:
                break
            tail.insert(0, item)
            tail_words += item_words
            if tail_words >= overlap_words:
                break
        current = tail
        current_words = sum(len(s.split()) for s in current)

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        words = sentence.split()
        if len(words) > chunk_size_words:
            # Hard-wrap very long sentences/log blobs.
            for i in range(0, len(words), chunk_size_words):
                piece = " ".join(words[i : i + chunk_size_words])
                if current_words + len(piece.split()) > chunk_size_words and current:
                    flush()
                current.append(piece)
                current_words += len(piece.split())
                flush()
            continue

        if current_words + len(words) > chunk_size_words and current:
            flush()
        current.append(sentence)
        current_words += len(words)

    flush()
    return chunks


class RAG:
    def __init__(self, embedding_model="all-MiniLM-L6-v2"):
        if not RAG_AVAILABLE:
            raise ImportError("RAG libraries not installed")
        self.embedder = SentenceTransformer(embedding_model)
        self.index = None
        self.chunks: List[str] = []
        self.chunk_sources: List[str] = []

    def add_documents(
        self,
        texts: List[str],
        *,
        sources: Optional[List[str]] = None,
        chunk_size=CHUNK_SIZE,
        overlap=CHUNK_OVERLAP,
    ):
        """Add documents to the index.

        `sources` is an optional list of labels parallel to `texts`.
        """
        all_chunks: List[str] = []
        all_sources: List[str] = []
        if sources is not None and len(sources) != len(texts):
            raise ValueError("sources must be the same length as texts")

        for idx, text in enumerate(texts):
            chunks_for_text = chunk_text(text, chunk_size, overlap)
            if not chunks_for_text:
                continue
            all_chunks.extend(chunks_for_text)
            src = sources[idx] if sources is not None else "(unknown)"
            all_sources.extend([src] * len(chunks_for_text))
        if not all_chunks:
            return
        self.chunks.extend(all_chunks)
        self.chunk_sources.extend(all_sources)
        embeddings = self.embedder.encode(all_chunks, show_progress_bar=False)
        dim = embeddings.shape[1]
        if self.index is None:
            self.index = faiss.IndexFlatL2(dim)
        self.index.add(np.array(embeddings).astype("float32"))

    def add_file(self, filepath: str, *, source_label: Optional[str] = None):
        """Add a text file to the index."""
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        label = source_label or Path(filepath).name
        self.add_documents([text], sources=[label])

    def retrieve(self, query: str, k=3) -> List[str]:
        """Retrieve top-k chunks for a query."""
        if self.index is None or self.index.ntotal == 0:
            return []
        query_emb = self.embedder.encode([query], show_progress_bar=False)
        distances, indices = self.index.search(np.array(query_emb).astype("float32"), k)
        results: List[str] = []
        for i in indices[0]:
            if i < 0 or i >= len(self.chunks):
                continue
            src = self.chunk_sources[i] if i < len(self.chunk_sources) else "(unknown)"
            results.append(f"[source: {src}]\n{self.chunks[i]}")
        return results


def generate_response(
    tokenizer,
    model,
    user_input,
    context=None,
    max_new_tokens=512,
    temperature=0.7,
):
    """Build prompt with optional context and generate."""
    if context:
        context_str = "\n".join(f"- {c}" for c in context)
        prompt = (
            f"<|system|>\n{SYSTEM_PROMPT}\n\nRelevant knowledge:\n{context_str}"
            f"\n<|user|>\n{user_input}\n<|assistant|>\n"
        )
    else:
        prompt = f"<|system|>\n{SYSTEM_PROMPT}\n<|user|>\n{user_input}\n<|assistant|>\n"
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=MAX_CONTEXT_TOKENS)
    inputs = {k: v.to(model.device) for k, v in inputs.items()}
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            do_sample=True,
            top_p=0.9,
            repetition_penalty=1.1,
            pad_token_id=tokenizer.eos_token_id,
        )
    input_len = inputs["input_ids"].shape[1]
    response = tokenizer.decode(outputs[0][input_len:], skip_special_tokens=True)
    return response.strip()


# ---- Main CLI ----
def main():
    parser = argparse.ArgumentParser(description="AL-5G-AE: 5G Core specialist copilot")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model name or path")
    parser.add_argument("--device", default=DEFAULT_DEVICE, choices=["cpu", "cuda"])
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--rag-dir", help="Directory with text files for RAG knowledge base")
    parser.add_argument("--log-file", help="Log file to ingest (optional, can query about it)")
    parser.add_argument(
        "--run-log",
        default=str(Path("logs") / "al_5g_ae.log"),
        help="Write a run log to this file (set to empty string to disable)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging (debug-level).",
    )
    parser.add_argument("--query", help="Single question (non-interactive mode)")
    args = parser.parse_args()

    run_log_path = args.run_log if str(args.run_log).strip() else None
    logger = setup_run_logger(run_log_path, verbose=args.verbose)
    logger.info("Starting AL-5G-AE")
    logger.info("device=%s model=%s", args.device, args.model)

    # Load model (fallback if default fails)
    try:
        tokenizer, model = load_model(args.model, args.device)
    except Exception:
        print(f"Failed to load {args.model}, falling back to {FALLBACK_MODEL}", file=sys.stderr)
        logger.exception("Model load failed; falling back")
        tokenizer, model = load_model(FALLBACK_MODEL, args.device)
        logger.info("fallback_model=%s", FALLBACK_MODEL)

    # Build RAG index if requested
    rag = None
    if args.rag_dir and RAG_AVAILABLE:
        rag = RAG()
        path = Path(args.rag_dir)
        if path.is_dir():
            for f in sorted(path.glob("*.txt")):
                print(f"Indexing {f}...", file=sys.stderr)
                rag.add_file(str(f), source_label=f.name)
        else:
            rag.add_file(str(path), source_label=path.name)
        print(f"Indexed {len(rag.chunks)} chunks.", file=sys.stderr)
        logger.info("rag_enabled=1 rag_chunks=%d rag_path=%s", len(rag.chunks), args.rag_dir)
    elif args.rag_dir and not RAG_AVAILABLE:
        print("RAG requested but libraries missing. Install faiss-cpu, sentence-transformers, numpy")
        logger.warning("rag_requested_but_unavailable rag_path=%s", args.rag_dir)

    # Handle log file ingestion
    if args.log_file:
        with open(args.log_file, "r", encoding="utf-8", errors="ignore") as f:
            log_content = f.read()
        # We can either feed the whole log as context for the first query, or index it.
        # Here we'll index it if RAG is available, else store as variable for manual use.
        if rag:
            print(f"Ingesting log file {args.log_file} into RAG...", file=sys.stderr)
            rag.add_file(args.log_file, source_label=Path(args.log_file).name)
            print("Log indexed. You can now ask questions about it.", file=sys.stderr)
            logger.info("log_ingested=1 log_file=%s", args.log_file)
        else:
            # Without RAG, we'll just mention it's available.
            print(
                f"Log file loaded ({len(log_content)} chars). Use RAG for better retrieval.",
                file=sys.stderr,
            )
            logger.info("log_loaded_without_rag=1 log_file=%s chars=%d", args.log_file, len(log_content))

    # Single query mode
    if args.query:
        context = None
        if rag:
            context = rag.retrieve(args.query, k=3)
        logger.info("single_query=1 query=%r retrieved=%d", args.query, len(context or []))
        response = generate_response(
            tokenizer,
            model,
            args.query,
            context,
            args.max_tokens,
            args.temperature,
        )
        logger.info("single_query_done chars=%d", len(response))
        print(response)
        return

    # Interactive mode
    print("\n" + "=" * 60)
    print("AL-5G-AE – 5G Core Specialist Copilot")
    if rag:
        print(f"RAG active with {len(rag.chunks)} chunks.")
    if args.log_file:
        print(f"Log file loaded: {args.log_file}")
    print("=" * 60)
    print("Type your questions. Enter 'quit', 'exit', or Ctrl-D to stop.")
    print("-" * 60)

    while True:
        try:
            user_input = input("\n>>> ")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break
        user_input = user_input.strip()
        if user_input.lower() in ("quit", "exit"):
            break
        if not user_input:
            continue

        context = None
        if rag:
            context = rag.retrieve(user_input, k=3)
            if context:
                print("\n[Retrieved relevant chunks from knowledge base]", file=sys.stderr)
        logger.info("query=%r retrieved=%d", user_input, len(context or []))
        try:
            response = generate_response(
                tokenizer,
                model,
                user_input,
                context,
                args.max_tokens,
                args.temperature,
            )
            print("\n" + response)
            logger.info("answer_chars=%d", len(response))
        except Exception as e:
            print(f"\n[Error: {e}]")
            logger.exception("generation_error")


if __name__ == "__main__":
    main()
