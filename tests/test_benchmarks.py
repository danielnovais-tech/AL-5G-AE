#!/usr/bin/env python3
# pyright: reportMissingModuleSource=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownParameterType=false
# pyright: reportUnknownArgumentType=false, reportReturnType=false
# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false
# pyright: reportUntypedFunctionDecorator=false, reportUnusedImport=false
# pyright: reportCallIssue=false, reportAttributeAccessIssue=false
"""
Performance benchmarks for AL-5G-AE.

Measures:
  - Queries per second (model generation throughput)
  - RAG retrieval latency
  - Chunking throughput
  - PCAP ingestion rate
  - Memory footprint

Run with: pytest tests/test_benchmarks.py -v -s
Or standalone: python tests/test_benchmarks.py
"""

from __future__ import annotations

import gc
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.conftest import (
    MockModel,
    MockTokenizer,
    create_synthetic_kb,
    create_synthetic_pcap,
    create_synthetic_logs,
    create_temp_dir,
)


def _get_memory_mb() -> float:
    """Get current process memory usage in MB (cross-platform)."""
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except ImportError:
        pass
    # Fallback: use resource on Unix
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KB → MB on Linux
    except (ImportError, AttributeError):
        return 0.0


# ===================================================================
# Model generation throughput
# ===================================================================

class TestModelThroughput:
    """Benchmark queries per second with a mock model."""

    NUM_QUERIES = 100

    def test_queries_per_second(self) -> None:
        from al_5g_ae_core import generate_response

        tok = MockTokenizer()
        mdl = MockModel()
        questions = [
            "What is PFCP?",
            "Explain AMF registration flow.",
            "How does GTP-U work?",
            "What port does NGAP use?",
            "Describe PDU session establishment.",
        ]

        gc.collect()
        start = time.perf_counter()
        for _ in range(self.NUM_QUERIES):
            generate_response(tok, mdl, questions[_ % len(questions)])
        elapsed = time.perf_counter() - start

        qps = self.NUM_QUERIES / elapsed
        print(f"\n[BENCHMARK] Model generation: {qps:.1f} queries/sec "
              f"({self.NUM_QUERIES} queries in {elapsed:.3f}s)")
        # With mock model, should be extremely fast
        assert qps > 10, f"Expected >10 QPS with mock model, got {qps:.1f}"

    def test_generation_with_context(self) -> None:
        from al_5g_ae_core import generate_response

        tok = MockTokenizer()
        mdl = MockModel()
        context = [
            "PFCP is used between SMF and UPF on UDP port 8805.",
            "The AMF handles access and mobility management.",
            "GTP-U tunnels carry user-plane data on UDP 2152.",
        ]

        gc.collect()
        start = time.perf_counter()
        for _ in range(self.NUM_QUERIES):
            generate_response(tok, mdl, "What is PFCP?", context)
        elapsed = time.perf_counter() - start

        qps = self.NUM_QUERIES / elapsed
        print(f"\n[BENCHMARK] Generation with context: {qps:.1f} queries/sec "
              f"({self.NUM_QUERIES} queries in {elapsed:.3f}s)")
        assert qps > 10


# ===================================================================
# RAG retrieval latency
# ===================================================================

class TestRAGPerformance:
    """Benchmark RAG indexing and retrieval latency."""

    @pytest.fixture(autouse=True)
    def _check_rag(self) -> None:
        try:
            import faiss  # noqa: F401
            import sentence_transformers  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            pytest.skip("RAG dependencies not installed")

    def test_indexing_throughput(self) -> None:
        from al_5g_ae_core import RAG

        docs = [f"Document {i}: This is about 5G protocol number {i}. " * 10 for i in range(100)]

        gc.collect()
        start = time.perf_counter()
        rag = RAG(rerank=False, hybrid=False, contextual_compression=False)
        rag.add_documents(docs)
        elapsed = time.perf_counter() - start

        docs_per_sec = 100 / elapsed
        assert elapsed < 120  # Should complete in under 2 minutes even on slow CPU
        if rag.index is not None:
            print(f"\n[BENCHMARK] RAG indexing: {docs_per_sec:.1f} docs/sec "
                  f"(100 docs in {elapsed:.3f}s, {rag.index.ntotal} chunks)")

    def test_retrieval_latency(self) -> None:
        from al_5g_ae_core import RAG

        rag = RAG(rerank=False, hybrid=False, contextual_compression=False)
        docs = [f"Protocol {i}: port {8000 + i} is used for service {i}." for i in range(50)]
        rag.add_documents(docs)

        queries = [f"What port is used for service {i}?" for i in range(20)]

        gc.collect()
        start = time.perf_counter()
        for q in queries:
            rag.retrieve(q, k=3)
        elapsed = time.perf_counter() - start

        avg_latency_ms = (elapsed / len(queries)) * 1000
        qps = len(queries) / elapsed
        print(f"\n[BENCHMARK] RAG retrieval: {avg_latency_ms:.1f}ms avg, {qps:.1f} queries/sec "
              f"({len(queries)} queries in {elapsed:.3f}s)")
        assert avg_latency_ms < 5000  # Under 5 seconds per query even on slow hardware

    def test_hybrid_retrieval_latency(self) -> None:
        try:
            import rank_bm25  # noqa: F401
        except ImportError:
            pytest.skip("rank_bm25 not installed")

        from al_5g_ae_core import RAG

        rag = RAG(hybrid=True, rerank=False, contextual_compression=False)
        docs = [f"Protocol {i}: port {8000 + i} is used for service {i}." for i in range(50)]
        rag.add_documents(docs)

        queries = [f"What port is used for service {i}?" for i in range(20)]

        gc.collect()
        start = time.perf_counter()
        for q in queries:
            rag.retrieve(q, k=3)
        elapsed = time.perf_counter() - start

        avg_latency_ms = (elapsed / len(queries)) * 1000
        print(f"\n[BENCHMARK] Hybrid RAG retrieval: {avg_latency_ms:.1f}ms avg "
              f"({len(queries)} queries in {elapsed:.3f}s)")
        assert avg_latency_ms < 5000


# ===================================================================
# Chunking throughput
# ===================================================================

class TestChunkingPerformance:
    """Benchmark text chunking speed."""

    def test_semantic_chunking_throughput(self) -> None:
        from al_5g_ae_core import chunk_text_semantic

        # 100KB of prose-like text
        text = ("The 5G Core network uses a Service-Based Architecture. " * 200 + "\n") * 10
        text_size_kb = len(text.encode()) / 1024

        gc.collect()
        start = time.perf_counter()
        chunks = chunk_text_semantic(text, chunk_size=100, overlap=10)
        elapsed = time.perf_counter() - start

        print(f"\n[BENCHMARK] Semantic chunking: {text_size_kb:.0f}KB → {len(chunks)} chunks "
              f"in {elapsed:.3f}s ({text_size_kb / elapsed:.0f} KB/s)")
        assert len(chunks) > 0
        assert elapsed < 30

    def test_multiline_chunking_throughput(self) -> None:
        from al_5g_ae_core import chunk_text_multiline

        # 100KB of log-like text
        lines = [f"2026-04-03T10:{i % 60:02d}:{i % 60:02d}Z INFO [AMF] Event {i}" for i in range(2000)]
        text = "\n".join(lines)
        text_size_kb = len(text.encode()) / 1024

        gc.collect()
        start = time.perf_counter()
        chunks = chunk_text_multiline(text, chunk_size=100, overlap=10)
        elapsed = time.perf_counter() - start

        print(f"\n[BENCHMARK] Multiline chunking: {text_size_kb:.0f}KB → {len(chunks)} chunks "
              f"in {elapsed:.3f}s ({text_size_kb / elapsed:.0f} KB/s)")
        assert len(chunks) > 0
        assert elapsed < 30


# ===================================================================
# PCAP ingestion rate
# ===================================================================

class TestPCAPPerformance:
    """Benchmark PCAP parsing speed."""

    @pytest.fixture(autouse=True)
    def _check_scapy(self) -> None:
        try:
            import scapy  # noqa: F401
        except ImportError:
            pytest.skip("scapy not installed")

    def test_scapy_ingestion_rate(self) -> None:
        from pcap_ingest import process_pcap

        tmp = create_temp_dir()
        try:
            num = 200
            pcap = create_synthetic_pcap(os.path.join(tmp, "bench.pcap"), num_packets=num)

            gc.collect()
            start = time.perf_counter()
            summaries = process_pcap(pcap, max_packets=num, prefer_tshark=False)
            elapsed = time.perf_counter() - start

            pps = num / elapsed
            print(f"\n[BENCHMARK] PCAP Scapy ingestion: {pps:.0f} packets/sec "
                  f"({num} packets in {elapsed:.3f}s → {len(summaries)} summaries)")
            assert len(summaries) > 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================
# Memory footprint
# ===================================================================

class TestMemoryUsage:
    """Measure memory usage of key components."""

    @pytest.fixture(autouse=True)
    def _check_rag(self) -> None:
        try:
            import faiss  # noqa: F401
            import sentence_transformers  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            pytest.skip("RAG dependencies not installed")

    def test_rag_memory_footprint(self) -> None:
        from al_5g_ae_core import RAG

        gc.collect()
        mem_before = _get_memory_mb()

        rag = RAG(rerank=False, hybrid=False, contextual_compression=False)
        docs = [f"Document {i}: " + "x " * 200 for i in range(200)]
        rag.add_documents(docs)

        gc.collect()
        mem_after = _get_memory_mb()

        delta_mb = mem_after - mem_before
        if mem_before > 0:
            print(f"\n[BENCHMARK] RAG memory: {delta_mb:.1f}MB for 200 docs "
                  f"(before={mem_before:.0f}MB, after={mem_after:.0f}MB)")
        else:
            print(f"\n[BENCHMARK] RAG memory: measurement unavailable (install psutil)")

    def test_mock_model_memory(self) -> None:
        gc.collect()
        mem_before = _get_memory_mb()

        tok = MockTokenizer()
        mdl = MockModel()
        # Simulate 100 queries
        from al_5g_ae_core import generate_response
        for i in range(100):
            generate_response(tok, mdl, f"Query {i}")

        gc.collect()
        mem_after = _get_memory_mb()
        delta_mb = mem_after - mem_before

        if mem_before > 0:
            print(f"\n[BENCHMARK] 100 queries memory delta: {delta_mb:.1f}MB")
        else:
            print(f"\n[BENCHMARK] Memory measurement unavailable (install psutil)")


# ===================================================================
# KB Builder throughput
# ===================================================================

class TestKBBuilderPerformance:
    def test_markdown_processing_throughput(self) -> None:
        from kb_builder import process_file

        tmp = create_temp_dir()
        try:
            in_dir = os.path.join(tmp, "input")
            out_dir = os.path.join(tmp, "output")
            os.makedirs(in_dir)
            os.makedirs(out_dir)

            # Create 50 markdown files
            for i in range(50):
                Path(os.path.join(in_dir, f"doc_{i}.md")).write_text(
                    f"# Document {i}\n\n" + f"Content about 5G topic {i}. " * 100,
                    encoding="utf-8",
                )

            gc.collect()
            start = time.perf_counter()
            for f in sorted(Path(in_dir).iterdir()):
                process_file(str(f), out_dir)
            elapsed = time.perf_counter() - start

            fps = 50 / elapsed
            print(f"\n[BENCHMARK] KB builder: {fps:.1f} files/sec "
                  f"(50 markdown files in {elapsed:.3f}s)")
            assert elapsed < 30
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================
# Standalone runner with summary table
# ===================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--tb=short"])
