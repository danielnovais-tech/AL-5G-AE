#!/usr/bin/env python3
"""AL-5G-AE – CLI entrypoint.

This file intentionally contains only CLI orchestration.
Shared logic lives in `al_5g_ae_core.py`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from al_5g_ae_core import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    DEFAULT_DEVICE,
    DEFAULT_MODEL,
    FALLBACK_MODEL,
    RAG,
    RAG_AVAILABLE,
    generate_response,
    load_model,
    setup_run_logger,
)
from pcap_ingest import process_pcap, summaries_to_text


def main() -> None:
    parser = argparse.ArgumentParser(description="AL-5G-AE: 5G Core specialist copilot")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model name or path")
    parser.add_argument("--device", default=DEFAULT_DEVICE, choices=["cpu", "cuda"])
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.7)

    parser.add_argument("--rag-dir", help="Directory (or file) with .txt files for RAG knowledge base")
    parser.add_argument("--log-file", help="Log file to ingest (optional, can query about it)")

    parser.add_argument("--pcap-file", help="PCAP/PCAPNG file to ingest (optional)")
    parser.add_argument(
        "--pcap-max-packets",
        type=int,
        default=2000,
        help="Max packets to parse from PCAP (default: 2000)",
    )
    parser.add_argument(
        "--pcap-filter",
        default=None,
        help=(
            "Optional tshark display filter (only used when tshark is installed). "
            "Example: 'udp.port==8805' or 'pfcp || gtpv2 || gtp'."
        ),
    )

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
    logger = setup_run_logger(run_log_path, verbose=args.verbose, name="al_5g_ae")
    logger.info("Starting AL-5G-AE")
    logger.info("device=%s model=%s", args.device, args.model)

    # Load model (fallback if default fails)
    try:
        tokenizer, model = load_model(args.model, args.device)
    except Exception:
        logger.exception("Model load failed; falling back")
        tokenizer, model = load_model(FALLBACK_MODEL, args.device)
        logger.info("fallback_model=%s", FALLBACK_MODEL)

    rag = None
    if args.rag_dir and RAG_AVAILABLE:
        rag = RAG()
        path = Path(args.rag_dir)
        if path.is_dir():
            for fp in sorted(path.glob("*.txt")):
                rag.add_file(str(fp), source_label=fp.name)
        else:
            rag.add_file(str(path), source_label=path.name)
        logger.info("rag_enabled=1 rag_chunks=%d rag_path=%s", len(rag.chunks), args.rag_dir)
    elif args.rag_dir and not RAG_AVAILABLE:
        logger.warning("rag_requested_but_unavailable rag_path=%s", args.rag_dir)
        print("RAG requested but libraries missing. Install faiss-cpu, sentence-transformers, numpy", file=sys.stderr)

    # Log file ingestion
    if args.log_file:
        try:
            text = Path(args.log_file).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            logger.exception("log_read_failed log_file=%s", args.log_file)
            text = ""
        if rag:
            rag.add_documents([text], sources=[Path(args.log_file).name], chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
            logger.info("log_ingested=1 log_file=%s", args.log_file)
        else:
            logger.info("log_loaded_without_rag=1 log_file=%s chars=%d", args.log_file, len(text))
            print(f"Log file loaded ({len(text)} chars). Use RAG for better retrieval.", file=sys.stderr)

    # PCAP ingestion
    pcap_context: Optional[str] = None
    if args.pcap_file:
        try:
            summaries = process_pcap(
                args.pcap_file,
                max_packets=max(1, int(args.pcap_max_packets)),
                tshark_display_filter=str(args.pcap_filter) if args.pcap_filter else None,
            )
            pcap_text = summaries_to_text(summaries, header=f"PCAP summary from {Path(args.pcap_file).name}")
            if rag:
                rag.add_documents([pcap_text], sources=[Path(args.pcap_file).name])
                logger.info("pcap_ingested_into_rag=1 pcap_file=%s packets=%d", args.pcap_file, len(summaries))
                print("PCAP indexed. You can now ask questions about it.", file=sys.stderr)
            else:
                pcap_context = "\n".join(pcap_text.splitlines()[:200]).strip()
                logger.info(
                    "pcap_loaded_without_rag=1 pcap_file=%s summary_lines=%d",
                    args.pcap_file,
                    len(pcap_context.splitlines()) if pcap_context else 0,
                )
                print(
                    "PCAP loaded (summary only). Enable RAG for better retrieval: --rag-dir ./knowledge_base",
                    file=sys.stderr,
                )
        except Exception as exc:
            logger.exception("pcap_ingestion_failed")
            print(f"PCAP ingestion failed: {exc}", file=sys.stderr)

    def build_context(question: str):
        context = rag.retrieve(question, k=3) if rag else None
        if pcap_context:
            context = (context or []) + [f"[source: pcap_summary]\n{pcap_context}"]
        return context

    # Single query mode
    if args.query:
        context = build_context(args.query)
        logger.info("single_query=1 query=%r retrieved=%d", args.query, len(context or []))
        answer = generate_response(tokenizer, model, args.query, context, args.max_tokens, args.temperature)
        print(answer)
        return

    # Interactive mode
    print("\n" + "=" * 60)
    print("AL-5G-AE – 5G Core Specialist Copilot")
    if rag:
        print(f"RAG active with {len(rag.chunks)} chunks.")
    if args.log_file:
        print(f"Log file loaded: {args.log_file}")
    if args.pcap_file:
        print(f"PCAP loaded: {args.pcap_file}")
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

        context = build_context(user_input)
        logger.info("query=%r retrieved=%d", user_input, len(context or []))
        try:
            response = generate_response(tokenizer, model, user_input, context, args.max_tokens, args.temperature)
            print("\n" + response)
        except Exception as exc:
            logger.exception("generation_error")
            print(f"\n[Error: {exc}]", file=sys.stderr)


if __name__ == "__main__":
    main()
