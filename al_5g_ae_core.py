#!/usr/bin/env python3
"""Shared core for AL-5G-AE.

This module centralizes the reusable parts of the project:
- model loading
- system prompt
- chunking (semantic + multiline/log-aware)
- RAG index
- response generation
- run logging setup

Entry points (CLI, web UI, API server) should import from here.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any, List, Optional

# ---- Lazy-loaded optional dependencies ---------------------------------------
# Heavy libraries (torch, transformers, faiss, sentence-transformers, nltk) are
# imported on first use so that lightweight entry points (e.g. web_ui --minimal-ui)
# start in under a second.

_torch: Any = None
_AutoModelForCausalLM: Any = None
_AutoTokenizer: Any = None
_faiss: Any = None
_np: Any = None
_SentenceTransformer: Any = None
_nltk: Any = None
_sent_tokenize: Any = None


def _ensure_transformers() -> bool:
    global _torch, _AutoModelForCausalLM, _AutoTokenizer
    if _torch is not None:
        return True
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        _torch = torch
        _AutoModelForCausalLM = AutoModelForCausalLM
        _AutoTokenizer = AutoTokenizer
        return True
    except ImportError:
        return False


def _ensure_rag() -> bool:
    global _faiss, _np, _SentenceTransformer
    if _faiss is not None:
        return True
    try:
        import faiss  # type: ignore[import-untyped]
        import numpy as np
        from sentence_transformers import SentenceTransformer
        _faiss = faiss
        _np = np
        _SentenceTransformer = SentenceTransformer
        return True
    except ImportError:
        return False


def _ensure_nltk() -> bool:
    global _nltk, _sent_tokenize
    if _nltk is not None:
        return True
    try:
        import nltk  # type: ignore[import-untyped]
        from nltk.tokenize import sent_tokenize  # type: ignore[import-untyped]
        _nltk = nltk
        _sent_tokenize = sent_tokenize  # type: ignore[assignment]
        return True
    except ImportError:
        return False


# Backward-compatible availability flags (lazy — imports only happen on first check)
class _LazyFlag:
    __slots__ = ("_checker", "_cache")
    def __init__(self, checker: Any) -> None:
        self._checker = checker
        self._cache: Optional[bool] = None
    def __bool__(self) -> bool:
        if self._cache is None:
            self._cache = bool(self._checker())
        return bool(self._cache)

RAG_AVAILABLE = _LazyFlag(_ensure_rag)
TRANSFORMERS_AVAILABLE = _LazyFlag(_ensure_transformers)
NLTK_AVAILABLE = _LazyFlag(_ensure_nltk)


DEFAULT_MODEL = "microsoft/phi-2"
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


def setup_run_logger(log_path: Optional[str], *, verbose: bool = False, name: str = "al_5g_ae") -> logging.Logger:
    """Configure a simple file + stderr logger.

    Safe to call multiple times.
    """
    logger = logging.getLogger(name)
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


def load_model(model_name: str, device: str) -> tuple[Any, Any]:
    """Load tokenizer and model."""
    if not _ensure_transformers():
        raise ImportError("Install required packages: transformers torch")

    tokenizer = _AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    model = _AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=_torch.float16 if device == "cuda" else _torch.float32,
        device_map="auto" if device == "cuda" else None,
        low_cpu_mem_usage=True,
    )
    if device == "cpu":
        model = model.to("cpu")
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer, model


# ---- Chunking ----
_TS_PREFIX_RE = re.compile(
    r"^\s*(?P<ts>\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?)"
)

_STACKTRACE_RE = re.compile(
    r"(^\s+at\s+\S+\()|(^Traceback \(most recent call last\):)|(^\w*Exception\b)|(^Caused by:)"  # noqa: E501
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")

_NLTK_STATE = {"ready": False, "disabled": False}


def _looks_like_timestamped_log(text: str) -> bool:
    if not text:
        return False
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sample = lines[:200]
    hits = sum(1 for ln in sample if _TS_PREFIX_RE.match(ln or "") is not None)
    return hits >= max(3, int(len(sample) * 0.10))


def _looks_like_log_text(text: str) -> bool:
    if _looks_like_timestamped_log(text):
        return True
    if not text:
        return False
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sample = lines[:400]
    stack_hits = sum(1 for ln in sample if _STACKTRACE_RE.search(ln or "") is not None)
    return stack_hits >= 3


def chunk_text_multiline(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Preserve multi-line log entries (stacktraces) while chunking by word budget."""
    return _chunk_text_log_entries(text, chunk_size_words=chunk_size, overlap_words=overlap)


def _try_get_nltk_sentences(text: str) -> Optional[List[str]]:
    if _NLTK_STATE["disabled"]:
        return None
    if not _ensure_nltk():
        return None
    try:
        if not _NLTK_STATE["ready"]:
            try:
                _nltk.data.find("tokenizers/punkt")
            except LookupError:
                _nltk.download("punkt", quiet=True)
            _NLTK_STATE["ready"] = True
        sentences = [s.strip() for s in _sent_tokenize(text) if s.strip()]
        return sentences or None
    except Exception:
        _NLTK_STATE["disabled"] = True
        return None


def _split_into_sentences(text: str) -> List[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    nltk_sentences = _try_get_nltk_sentences(text)
    if nltk_sentences:
        return nltk_sentences

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    sentences: List[str] = []
    for paragraph in paragraphs:
        lines = [ln.strip() for ln in paragraph.split("\n") if ln.strip()]
        if len(lines) >= 3 and sum(1 for ln in lines[:10] if ":" in ln or "=" in ln) >= 2:
            sentences.extend(lines)
            continue
        parts = [s.strip() for s in _SENTENCE_SPLIT_RE.split(paragraph) if s.strip()]
        sentences.extend(parts if parts else [paragraph])
    return sentences


def chunk_text_semantic(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Split text into chunks using sentence boundaries when possible."""
    return _chunk_text_semantic(text, chunk_size_words=chunk_size, overlap_words=overlap)


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


def _group_log_entries(text: str) -> List[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    has_timestamp = any(_TS_PREFIX_RE.match(ln or "") is not None for ln in lines[:400])

    entries: List[List[str]] = []
    current: List[str] = []

    def flush():
        nonlocal current
        if current:
            entries.append(current)
            current = []

    for line in lines:
        if has_timestamp:
            if _TS_PREFIX_RE.match(line or "") is not None:
                flush()
                current = [line]
            else:
                if current:
                    current.append(line)
                else:
                    current = [line]
            continue

        # No timestamps: group by blank lines + stacktrace boundaries.
        if not (line or "").strip():
            flush()
            continue
        if _STACKTRACE_RE.search(line) is not None and current:
            flush()
        current.append(line)

    flush()
    return ["\n".join(e).strip() for e in entries if "\n".join(e).strip()]


def _chunk_text_log_entries(text: str, *, chunk_size_words: int, overlap_words: int) -> List[str]:
    if chunk_size_words <= 0:
        return []
    overlap_words = max(0, min(overlap_words, chunk_size_words - 1))

    entries = _group_log_entries(text)
    if not entries:
        return []

    chunks: List[str] = []
    current: List[str] = []
    current_words = 0

    def flush():
        nonlocal current, current_words
        if not current:
            return
        chunk = "\n\n".join(current).strip()
        if chunk:
            chunks.append(chunk)

        if overlap_words <= 0:
            current = []
            current_words = 0
            return

        tail: List[str] = []
        tail_words = 0
        for entry in reversed(current):
            entry_words = len(entry.split())
            if tail_words + entry_words > overlap_words and tail:
                break
            tail.insert(0, entry)
            tail_words += entry_words
            if tail_words >= overlap_words:
                break
        current = tail
        current_words = sum(len(e.split()) for e in current)

    for entry in entries:
        entry_words = entry.split()
        if len(entry_words) > chunk_size_words:
            for i in range(0, len(entry_words), chunk_size_words):
                piece = " ".join(entry_words[i : i + chunk_size_words])
                if current_words + len(piece.split()) > chunk_size_words and current:
                    flush()
                current.append(piece)
                current_words += len(piece.split())
                flush()
            continue

        if current_words + len(entry_words) > chunk_size_words and current:
            flush()
        current.append(entry)
        current_words += len(entry_words)

    flush()
    return chunks


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP, *, mode: str = "auto") -> List[str]:
    """Chunk text for retrieval.

    mode:
      - auto: detect log-like text, else semantic
      - semantic: sentence/paragraph-aware
      - multiline: log-entry-aware
    """
    mode = (mode or "auto").lower().strip()
    if mode == "multiline":
        return chunk_text_multiline(text, chunk_size, overlap)
    if mode == "semantic":
        return chunk_text_semantic(text, chunk_size, overlap)
    if _looks_like_log_text(text):
        return chunk_text_multiline(text, chunk_size, overlap)
    return chunk_text_semantic(text, chunk_size, overlap)


# ---- RAG ----
class RAG:
    def __init__(self, embedding_model: str = "all-MiniLM-L6-v2", *, chunk_mode: str = "auto"):
        if not _ensure_rag():
            raise ImportError(
                "RAG libraries not installed. Install: sentence-transformers faiss-cpu numpy"
            )
        self.embedder = _SentenceTransformer(embedding_model)
        self.index = None
        self.chunks: List[str] = []
        self.chunk_sources: List[str] = []
        self.chunk_mode = chunk_mode

    def add_documents(
        self,
        texts: List[str],
        *,
        sources: Optional[List[str]] = None,
        chunk_size: int = CHUNK_SIZE,
        overlap: int = CHUNK_OVERLAP,
    ) -> None:
        if sources is not None and len(sources) != len(texts):
            raise ValueError("sources must be the same length as texts")

        all_chunks: List[str] = []
        all_sources: List[str] = []

        for idx, text in enumerate(texts):
            chunks_for_text = chunk_text(text, chunk_size, overlap, mode=self.chunk_mode)
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
            self.index = _faiss.IndexFlatL2(dim)
        self.index.add(_np.array(embeddings).astype("float32"))  # type: ignore[call-arg]

    def add_file(self, filepath: str, *, source_label: Optional[str] = None) -> None:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        label = source_label or Path(filepath).name
        self.add_documents([text], sources=[label])

    def retrieve(self, query: str, k: int = 3) -> List[str]:
        if self.index is None or self.index.ntotal == 0:
            return []
        query_emb = self.embedder.encode([query], show_progress_bar=False)
        _, indices = self.index.search(_np.array(query_emb).astype("float32"), int(k))  # type: ignore[call-arg]
        results: List[str] = []
        for i in indices[0]:
            if i < 0 or i >= len(self.chunks):
                continue
            src: str = str(self.chunk_sources[int(i)]) if int(i) < len(self.chunk_sources) else "(unknown)"
            results.append(f"[source: {src}]\n{self.chunks[i]}")
        return results


def generate_response(
    tokenizer: Any,
    model: Any,
    user_input: str,
    context: Optional[List[str]] = None,
    max_new_tokens: int = 512,
    temperature: float = 0.7,
) -> str:
    if not _ensure_transformers():
        raise ImportError("Install required packages: transformers torch")

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

    with _torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=int(max_new_tokens),
            temperature=float(temperature),
            do_sample=True,
            top_p=0.9,
            repetition_penalty=1.1,
            pad_token_id=tokenizer.eos_token_id,
        )

    input_len = inputs["input_ids"].shape[1]
    response = tokenizer.decode(outputs[0][input_len:], skip_special_tokens=True)
    return response.strip()
