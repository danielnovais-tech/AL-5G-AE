#!/usr/bin/env python3
# pyright: reportMissingModuleSource=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownParameterType=false
# pyright: reportUnknownArgumentType=false, reportReturnType=false
# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false
# pyright: reportUntypedFunctionDecorator=false, reportUnusedImport=false
"""
Unit tests for al_5g_ae_core.py.

Tests chunking, RAG, model loading (mocked), and response generation
using synthetic data only — no network or GPU required.
"""

from __future__ import annotations

import os
import sys
import shutil
import tempfile
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from al_5g_ae_core import (
    SYSTEM_PROMPT,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    chunk_text,
    chunk_text_semantic,
    chunk_text_multiline,
    _looks_like_log_text,
    _looks_like_timestamped_log,
    _split_into_sentences,
    _group_log_entries,
    setup_run_logger,
)
from tests.conftest import (
    MockTokenizer,
    MockModel,
    create_synthetic_kb,
    create_temp_dir,
)


# ===================================================================
# System prompt
# ===================================================================

class TestSystemPrompt:
    def test_prompt_mentions_5g_nfs(self) -> None:
        for nf in ("AMF", "SMF", "UPF", "NRF", "PCF"):
            assert nf in SYSTEM_PROMPT

    def test_prompt_mentions_protocols(self) -> None:
        for proto in ("NGAP", "GTP-U", "PFCP", "SBI", "NAS"):
            assert proto in SYSTEM_PROMPT

    def test_prompt_is_non_empty_string(self) -> None:
        assert isinstance(SYSTEM_PROMPT, str)
        assert len(SYSTEM_PROMPT) > 100


# ===================================================================
# Chunking — semantic
# ===================================================================

class TestChunkTextSemantic:
    def test_empty_text(self) -> None:
        assert chunk_text_semantic("", chunk_size=100) == []

    def test_short_text_single_chunk(self) -> None:
        text = "Hello world. This is a short text."
        chunks = chunk_text_semantic(text, chunk_size=100)
        assert len(chunks) >= 1
        assert "Hello world" in chunks[0]

    def test_respects_chunk_size(self) -> None:
        text = " ".join(f"Word{i}" for i in range(500))
        chunks = chunk_text_semantic(text, chunk_size=50, overlap=0)
        for c in chunks:
            assert len(c.split()) <= 55  # Small tolerance for boundary sentences

    def test_overlap_produces_more_chunks(self) -> None:
        text = " ".join(f"Sentence number {i}." for i in range(100))
        no_overlap = chunk_text_semantic(text, chunk_size=20, overlap=0)
        with_overlap = chunk_text_semantic(text, chunk_size=20, overlap=5)
        assert len(with_overlap) >= len(no_overlap)

    def test_preserves_all_content(self) -> None:
        words = [f"w{i}" for i in range(100)]
        text = " ".join(words)
        chunks = chunk_text_semantic(text, chunk_size=20, overlap=0)
        combined = " ".join(chunks)
        for w in words:
            assert w in combined


# ===================================================================
# Chunking — multiline / log-aware
# ===================================================================

class TestChunkTextMultiline:
    def test_empty_text(self) -> None:
        assert chunk_text_multiline("", chunk_size=100) == []

    def test_timestamp_log_entries(self) -> None:
        log = "\n".join(
            f"2026-04-03T10:{i:02d}:00Z INFO [AMF] Event {i}"
            for i in range(20)
        )
        chunks = chunk_text_multiline(log, chunk_size=30, overlap=0)
        assert len(chunks) >= 1
        assert "AMF" in chunks[0]

    def test_stacktrace_kept_together(self) -> None:
        log = (
            "2026-04-03T10:00:00Z ERROR [SMF] NullPointerException\n"
            "  at com.example.Foo.bar(Foo.java:42)\n"
            "  at com.example.Main.run(Main.java:10)\n"
            "Caused by: java.lang.NullPointerException\n"
            "  at com.example.Baz.qux(Baz.java:99)\n"
            "\n"
            "2026-04-03T10:00:01Z INFO [SMF] Recovery complete\n"
        )
        chunks = chunk_text_multiline(log, chunk_size=200, overlap=0)
        # The stacktrace should be in a single chunk
        assert any("NullPointerException" in c and "Foo.java" in c for c in chunks)


# ===================================================================
# Chunking — auto-detect
# ===================================================================

class TestChunkTextAuto:
    def test_auto_detects_log(self) -> None:
        log = "\n".join(
            f"2026-04-03T10:{i:02d}:00Z INFO test {i}" for i in range(20)
        )
        assert _looks_like_timestamped_log(log)
        assert _looks_like_log_text(log)

    def test_auto_detects_prose(self) -> None:
        prose = "The 5G Core uses SBA. " * 50
        assert not _looks_like_log_text(prose)

    def test_mode_param(self) -> None:
        text = "Word " * 100
        c_sem = chunk_text(text, mode="semantic", chunk_size=20, overlap=0)
        c_ml = chunk_text(text, mode="multiline", chunk_size=20, overlap=0)
        assert len(c_sem) >= 1
        assert len(c_ml) >= 1


# ===================================================================
# Sentence splitting
# ===================================================================

class TestSentenceSplitting:
    def test_basic_sentences(self) -> None:
        text = "First sentence. Second sentence. Third one."
        sents = _split_into_sentences(text)
        assert len(sents) >= 2

    def test_empty(self) -> None:
        assert _split_into_sentences("") == []

    def test_key_value_lines(self) -> None:
        text = "key1: value1\nkey2: value2\nkey3: value3"
        parts = _split_into_sentences(text)
        assert len(parts) >= 3


# ===================================================================
# Log entry grouping
# ===================================================================

class TestLogEntryGrouping:
    def test_groups_by_timestamp(self) -> None:
        log = (
            "2026-04-03T10:00:00Z INFO line1\n"
            "  continuation\n"
            "2026-04-03T10:00:01Z ERROR line2\n"
        )
        entries = _group_log_entries(log)
        assert len(entries) == 2
        assert "continuation" in entries[0]

    def test_groups_by_blank_lines(self) -> None:
        text = "block1 line1\nblock1 line2\n\nblock2 line1\n\nblock3 line1"
        entries = _group_log_entries(text)
        assert len(entries) == 3


# ===================================================================
# Logger setup
# ===================================================================

class TestRunLogger:
    def test_creates_log_file(self) -> None:
        tmp = create_temp_dir()
        try:
            log_path = os.path.join(tmp, "test.log")
            logger = setup_run_logger(log_path, name="test_logger_create")
            logger.info("test message")
            assert Path(log_path).exists()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_file(self) -> None:
        logger = setup_run_logger(None, name="test_logger_nofile")
        assert logger is not None
        logger.info("should not crash")


# ===================================================================
# generate_response (mocked model)
# ===================================================================

class TestGenerateResponse:
    def test_with_mock_model(self) -> None:
        """generate_response should call model.generate and tokenizer.decode."""
        from al_5g_ae_core import generate_response
        tok = MockTokenizer()
        mdl = MockModel()
        result = generate_response(tok, mdl, "What is PFCP?")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_with_context(self) -> None:
        from al_5g_ae_core import generate_response
        tok = MockTokenizer()
        mdl = MockModel()
        ctx = ["PFCP is used between SMF and UPF on port 8805."]
        result = generate_response(tok, mdl, "What is PFCP?", ctx)
        assert isinstance(result, str)

    def test_empty_question(self) -> None:
        from al_5g_ae_core import generate_response
        tok = MockTokenizer()
        mdl = MockModel()
        result = generate_response(tok, mdl, "")
        assert isinstance(result, str)


# ===================================================================
# RAG (with FAISS + sentence-transformers, if available)
# ===================================================================

class TestRAG:
    @pytest.fixture(autouse=True)
    def _check_rag_deps(self) -> None:
        try:
            import faiss  # noqa: F401
            import sentence_transformers  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            pytest.skip("RAG dependencies not installed")

    def test_add_documents_and_retrieve(self) -> None:
        from al_5g_ae_core import RAG
        rag = RAG(rerank=False, hybrid=False, contextual_compression=False)
        rag.add_documents(
            ["PFCP runs on UDP port 8805.", "NGAP runs on SCTP port 38412."],
            sources=["proto.txt", "proto.txt"],
        )
        results = rag.retrieve("What port does PFCP use?", k=1)
        assert len(results) >= 1
        assert "8805" in results[0]

    def test_add_file(self) -> None:
        from al_5g_ae_core import RAG
        tmp = create_temp_dir()
        try:
            kb = create_synthetic_kb(tmp)
            rag = RAG(rerank=False, hybrid=False, contextual_compression=False)
            rag.add_file(os.path.join(kb, "protocols.txt"))
            results = rag.retrieve("PFCP port", k=1)
            assert len(results) >= 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_empty_retrieve(self) -> None:
        from al_5g_ae_core import RAG
        rag = RAG(rerank=False, hybrid=False, contextual_compression=False)
        results = rag.retrieve("anything", k=3)
        assert results == []

    def test_source_length_mismatch_raises(self) -> None:
        from al_5g_ae_core import RAG
        rag = RAG(rerank=False, hybrid=False, contextual_compression=False)
        with pytest.raises(ValueError):
            rag.add_documents(["text1", "text2"], sources=["only_one"])


class TestRAGHybrid:
    @pytest.fixture(autouse=True)
    def _check_deps(self) -> None:
        try:
            import faiss  # noqa: F401
            import sentence_transformers  # noqa: F401
            import numpy  # noqa: F401
            import rank_bm25  # noqa: F401
        except ImportError:
            pytest.skip("Hybrid RAG dependencies not installed")

    def test_hybrid_retrieval(self) -> None:
        from al_5g_ae_core import RAG
        rag = RAG(hybrid=True, rerank=False, contextual_compression=False)
        rag.add_documents(
            [
                "PFCP runs on UDP port 8805 between SMF and UPF.",
                "NGAP uses SCTP port 38412 for gNB-AMF signalling.",
                "GTP-U tunnels carry user data on UDP 2152.",
            ],
        )
        results = rag.retrieve("What is PFCP used for?", k=2)
        assert len(results) >= 1
        assert any("PFCP" in r for r in results)

    def test_rrf_fuse(self) -> None:
        from al_5g_ae_core import RAG
        fused = RAG._rrf_fuse([[0, 1, 2], [2, 0, 3]], k=60, top_n=2)
        assert len(fused) == 2
        assert 0 in fused  # appears in both lists


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
