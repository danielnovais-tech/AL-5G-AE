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
_Llama: Any = None
_BM25Okapi: Any = None
_CrossEncoder: Any = None
_CLIPModel: Any = None
_CLIPProcessor: Any = None
_PILImage: Any = None


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


def _ensure_llama_cpp() -> bool:
    """Lazy-load llama-cpp-python for GGUF model serving."""
    global _Llama
    if _Llama is not None:
        return True
    try:
        from llama_cpp import Llama  # type: ignore[import-untyped]
        _Llama = Llama  # pyright: ignore[reportUnknownVariableType]
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


def _ensure_bm25() -> bool:
    """Lazy-load rank_bm25 for hybrid search."""
    global _BM25Okapi
    if _BM25Okapi is not None:
        return True
    try:
        from rank_bm25 import BM25Okapi  # type: ignore[import-untyped]
        _BM25Okapi = BM25Okapi  # pyright: ignore[reportUnknownVariableType]
        return True
    except ImportError:
        return False


def _ensure_cross_encoder() -> bool:
    """Lazy-load sentence-transformers CrossEncoder for re-ranking."""
    global _CrossEncoder
    if _CrossEncoder is not None:
        return True
    try:
        from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]
        _CrossEncoder = CrossEncoder
        return True
    except ImportError:
        return False


def _ensure_clip() -> bool:
    """Lazy-load CLIP model and processor for multi-modal RAG."""
    global _CLIPModel, _CLIPProcessor, _PILImage
    if _CLIPModel is not None:
        return True
    try:
        from transformers import CLIPModel, CLIPProcessor  # type: ignore[import-untyped]
        from PIL import Image as PILImage  # type: ignore[import-untyped]
        _CLIPModel = CLIPModel
        _CLIPProcessor = CLIPProcessor
        _PILImage = PILImage
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
LLAMA_CPP_AVAILABLE = _LazyFlag(_ensure_llama_cpp)
BM25_AVAILABLE = _LazyFlag(_ensure_bm25)
CROSS_ENCODER_AVAILABLE = _LazyFlag(_ensure_cross_encoder)
CLIP_AVAILABLE = _LazyFlag(_ensure_clip)
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
    """Load tokenizer and model.

    Auto-detects GGUF files (by extension) and delegates to load_model_gguf().
    For HuggingFace models, uses transformers.
    """
    # Auto-detect GGUF
    if model_name.lower().endswith(".gguf") or Path(model_name).suffix.lower() == ".gguf":
        return load_model_gguf(model_name)

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


def load_model_gguf(
    model_path: str,
    *,
    n_ctx: int = 2048,
    n_threads: int = 0,
    n_gpu_layers: int = 0,
    verbose: bool = False,
) -> tuple[None, Any]:
    """Load a GGUF model via llama-cpp-python for fast CPU inference.

    Returns (None, llama_model). The tokenizer is built into llama.cpp,
    so generate_response() handles this transparently.
    """
    if not _ensure_llama_cpp():
        raise ImportError("Install llama-cpp-python: pip install llama-cpp-python")

    model = _Llama(
        model_path=model_path,
        n_ctx=n_ctx,
        n_threads=n_threads or 0,
        n_gpu_layers=n_gpu_layers,
        verbose=verbose,
    )
    return None, model


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
    """Retrieval-Augmented Generation index with hybrid BM25 + vector search.

    When ``rank_bm25`` is installed, ``retrieve()`` fuses BM25 keyword scores with
    FAISS vector similarity using Reciprocal Rank Fusion (RRF).  If ``rank_bm25``
    is not available, it falls back to pure vector search.

    Optional advanced features (enabled when dependencies are installed):
    - **Cross-encoder re-ranking**: re-scores top candidates with a cross-encoder.
    - **Contextual compression**: uses the LLM to filter irrelevant chunks.
    - **Multi-modal RAG**: indexes images via CLIP embeddings alongside text.
    """

    def __init__(
        self,
        embedding_model: str = "all-MiniLM-L6-v2",
        *,
        chunk_mode: str = "auto",
        hybrid: bool = True,
        rrf_k: int = 60,
        rerank: bool = True,
        rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        contextual_compression: bool = False,
        clip_model: str = "openai/clip-vit-base-patch32",
    ):
        if not _ensure_rag():
            raise ImportError(
                "RAG libraries not installed. Install: sentence-transformers faiss-cpu numpy"
            )
        self.embedder = _SentenceTransformer(embedding_model)
        self.index = None
        self.chunks: List[str] = []
        self.chunk_sources: List[str] = []
        self.chunk_mode = chunk_mode
        self.hybrid = hybrid and _ensure_bm25()
        self.rrf_k = rrf_k
        self._bm25: Any = None
        self._tokenized_corpus: List[List[str]] = []

        # Cross-encoder re-ranking
        self.rerank = rerank and _ensure_cross_encoder()
        self._cross_encoder: Any = None
        if self.rerank:
            self._cross_encoder = _CrossEncoder(rerank_model)

        # Contextual compression (requires model to be available at retrieve time)
        self.contextual_compression = contextual_compression

        # Multi-modal CLIP
        self._clip_model: Any = None
        self._clip_processor: Any = None
        self._clip_index: Any = None
        self._clip_sources: List[str] = []
        self._clip_paths: List[str] = []
        self._clip_model_name = clip_model

    def _rebuild_bm25(self) -> None:
        """(Re)build the BM25 index from the current chunk corpus."""
        if not self.hybrid or not self.chunks:
            return
        self._tokenized_corpus = [c.lower().split() for c in self.chunks]
        self._bm25 = _BM25Okapi(self._tokenized_corpus)

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

        # Rebuild BM25 when hybrid is enabled
        self._rebuild_bm25()

    def add_file(self, filepath: str, *, source_label: Optional[str] = None) -> None:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        label = source_label or Path(filepath).name
        self.add_documents([text], sources=[label])

    def add_image(self, filepath: str, *, source_label: Optional[str] = None) -> None:
        """Index an image (e.g., topology diagram) using CLIP embeddings."""
        if not _ensure_clip():
            raise ImportError("Install CLIP dependencies: pip install transformers Pillow")
        if self._clip_model is None:
            self._clip_model = _CLIPModel.from_pretrained(self._clip_model_name)
            self._clip_processor = _CLIPProcessor.from_pretrained(self._clip_model_name)
        image = _PILImage.open(filepath).convert("RGB")
        inputs = self._clip_processor(images=image, return_tensors="pt")
        outputs = self._clip_model.get_image_features(**inputs)
        embedding = outputs.detach().numpy().astype("float32")
        # Normalize for cosine similarity
        norm = _np.linalg.norm(embedding, axis=1, keepdims=True)
        if norm > 0:
            embedding = embedding / norm
        dim = embedding.shape[1]
        if self._clip_index is None:
            self._clip_index = _faiss.IndexFlatIP(dim)  # Inner product = cosine on normalized vecs
        self._clip_index.add(embedding)
        label = source_label or Path(filepath).name
        self._clip_sources.append(label)
        self._clip_paths.append(str(filepath))

    def add_image_dir(self, dirpath: str, extensions: Optional[List[str]] = None) -> None:
        """Index all images in a directory."""
        extensions = extensions or [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"]
        dirp = Path(dirpath)
        if not dirp.exists():
            return
        for f in dirp.iterdir():
            if f.suffix.lower() in extensions:
                self.add_image(str(f), source_label=f.name)

    def _retrieve_images(self, query: str, k: int = 2) -> List[str]:
        """Retrieve relevant images for a text query using CLIP."""
        if self._clip_index is None or self._clip_model is None or self._clip_index.ntotal == 0:
            return []
        inputs = self._clip_processor(text=[query], return_tensors="pt", padding=True)
        outputs = self._clip_model.get_text_features(**inputs)
        query_emb = outputs.detach().numpy().astype("float32")
        norm = _np.linalg.norm(query_emb, axis=1, keepdims=True)
        if norm > 0:
            query_emb = query_emb / norm
        scores, indices = self._clip_index.search(query_emb, min(k, self._clip_index.ntotal))
        results: List[str] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._clip_sources):
                continue
            results.append(f"[image: {self._clip_sources[idx]} (score: {score:.3f})] {self._clip_paths[idx]}")
        return results

    def _rerank_chunks(self, query: str, chunk_indices: List[int], top_k: int) -> List[int]:
        """Re-rank candidate chunks using a cross-encoder model."""
        if not self._cross_encoder or not chunk_indices:
            return chunk_indices[:top_k]
        pairs = [(query, self.chunks[i]) for i in chunk_indices if i < len(self.chunks)]
        scores = self._cross_encoder.predict(pairs)
        ranked = sorted(zip(chunk_indices, scores), key=lambda x: x[1], reverse=True)
        return [idx for idx, _ in ranked[:top_k]]

    @staticmethod
    def compress_chunks(
        query: str,
        chunks: List[str],
        tokenizer: Any,
        model: Any,
    ) -> List[str]:
        """Contextual compression: use the LLM to filter irrelevant chunks.

        Asks the model to judge each chunk's relevance and drops those that
        are clearly off-topic, reducing noise in the final prompt.
        """
        if not chunks:
            return chunks
        compressed: List[str] = []
        for chunk in chunks:
            prompt = (
                f"<|system|>\nYou are a relevance filter. Given a user query and a text chunk, "
                f"reply ONLY with 'RELEVANT' or 'IRRELEVANT'.\n"
                f"<|user|>\nQuery: {query}\nChunk: {chunk[:500]}\n<|assistant|>\n"
            )
            try:
                answer = generate_response(tokenizer, model, prompt, max_new_tokens=10, temperature=0.0)
                if "IRRELEVANT" not in answer.upper():
                    compressed.append(chunk)
            except Exception:
                compressed.append(chunk)  # Keep on error
        return compressed if compressed else chunks[:1]  # Always return at least one

    def _retrieve_vector(self, query: str, k: int) -> List[tuple[int, float]]:
        """Return (chunk_index, score) from FAISS. Lower distance = better."""
        if self.index is None or self.index.ntotal == 0:
            return []
        query_emb = self.embedder.encode([query], show_progress_bar=False)
        distances, indices = self.index.search(_np.array(query_emb).astype("float32"), int(k))  # type: ignore[call-arg]
        results: List[tuple[int, float]] = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(self.chunks):
                continue
            results.append((int(idx), float(dist)))
        return results

    def _retrieve_bm25(self, query: str, k: int) -> List[tuple[int, float]]:
        """Return (chunk_index, bm25_score) sorted descending."""
        if self._bm25 is None:
            return []
        query_tokens = query.lower().split()
        scores = self._bm25.get_scores(query_tokens)
        top_k = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [(i, float(scores[i])) for i in top_k if scores[i] > 0]

    @staticmethod
    def _rrf_fuse(
        ranked_lists: List[List[int]],
        k: int = 60,
        top_n: int = 3,
    ) -> List[int]:
        """Reciprocal Rank Fusion across multiple ranked lists."""
        scores: dict[int, float] = {}
        for ranked in ranked_lists:
            for rank, doc_id in enumerate(ranked):
                scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
        sorted_ids = sorted(scores, key=lambda d: scores[d], reverse=True)
        return sorted_ids[:top_n]

    def retrieve(self, query: str, k: int = 3) -> List[str]:
        if self.index is None or self.index.ntotal == 0:
            return []

        # --- OTEL span ---
        try:
            from observability import get_tracer
            _t = get_tracer("al_5g_ae_core")
        except Exception:
            _t = None
        _sp = _t.start_as_current_span("rag_retrieve") if _t else None
        if _sp is not None:
            _sp.__enter__()
            _sp.set_attribute("query_length", len(query))
            _sp.set_attribute("k", k)
            _sp.set_attribute("hybrid", self.hybrid)
            _sp.set_attribute("rerank", self.rerank)

        # Fetch more candidates for re-ranking / fusion
        fetch_k = k * 4 if self.rerank else (k * 2 if self.hybrid else k)

        vector_hits = self._retrieve_vector(query, fetch_k)
        vector_ranking = [idx for idx, _ in vector_hits]

        if self.hybrid and self._bm25 is not None:
            bm25_hits = self._retrieve_bm25(query, fetch_k)
            bm25_ranking = [idx for idx, _ in bm25_hits]
            fused_ids = self._rrf_fuse([vector_ranking, bm25_ranking], k=self.rrf_k, top_n=fetch_k)
        else:
            fused_ids = vector_ranking

        # Cross-encoder re-ranking
        if self.rerank and self._cross_encoder is not None:
            fused_ids = self._rerank_chunks(query, fused_ids, top_k=k)
        else:
            fused_ids = fused_ids[:k]

        results: List[str] = []
        for i in fused_ids:
            if i < 0 or i >= len(self.chunks):
                continue
            src: str = str(self.chunk_sources[int(i)]) if int(i) < len(self.chunk_sources) else "(unknown)"
            results.append(f"[source: {src}]\n{self.chunks[i]}")

        # Append relevant images (multi-modal CLIP)
        if self._clip_index is not None and self._clip_index.ntotal > 0:
            image_results = self._retrieve_images(query, k=2)
            results.extend(image_results)

        if _sp is not None:
            _sp.set_attribute("results_count", len(results))
            _sp.__exit__(None, None, None)

        return results


def _is_llama_cpp_model(model: Any) -> bool:
    """Check if model is a llama-cpp-python Llama instance."""
    return hasattr(model, "create_completion") and hasattr(model, "n_ctx")


def generate_response(
    tokenizer: Any,
    model: Any,
    user_input: str,
    context: Optional[List[str]] = None,
    max_new_tokens: int = 512,
    temperature: float = 0.7,
) -> str:
    # --- OpenTelemetry span (noop if OTEL is not installed) ---
    try:
        from observability import get_tracer
        _tracer = get_tracer("al_5g_ae_core")
    except Exception:
        _tracer = None
    _span_ctx = _tracer.start_as_current_span("generate_response") if _tracer else None
    if _span_ctx is not None:
        _span_ctx.__enter__()
        _span_ctx.set_attribute("input_length", len(user_input))
        _span_ctx.set_attribute("max_new_tokens", max_new_tokens)
        _span_ctx.set_attribute("context_chunks", len(context) if context else 0)

    if context:
        context_str = "\n".join(f"- {c}" for c in context)
        prompt = (
            f"<|system|>\n{SYSTEM_PROMPT}\n\nRelevant knowledge:\n{context_str}"
            f"\n<|user|>\n{user_input}\n<|assistant|>\n"
        )
    else:
        prompt = f"<|system|>\n{SYSTEM_PROMPT}\n<|user|>\n{user_input}\n<|assistant|>\n"

    # --- GGUF / llama.cpp path ---
    if _is_llama_cpp_model(model):
        result = model.create_completion(
            prompt,
            max_tokens=int(max_new_tokens),
            temperature=float(temperature),
            top_p=0.9,
            repeat_penalty=1.1,
            stop=["<|user|>", "<|system|>"],
        )
        response = result["choices"][0]["text"]  # type: ignore[index]

        if _span_ctx is not None:
            _span_ctx.set_attribute("output_length", len(response))
            _span_ctx.set_attribute("backend", "llama_cpp")
            _span_ctx.__exit__(None, None, None)
        return response.strip()

    # --- HuggingFace transformers path ---
    if not _ensure_transformers():
        raise ImportError("Install required packages: transformers torch")

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

    if _span_ctx is not None:
        _span_ctx.set_attribute("output_length", len(response))
        _span_ctx.set_attribute("backend", "transformers")
        _span_ctx.__exit__(None, None, None)

    return response.strip()
