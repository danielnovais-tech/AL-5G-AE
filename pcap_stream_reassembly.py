#!/usr/bin/env python3
"""
TCP stream reassembly from PCAP files for AL-5G-AE.
Useful for reconstructing HTTP/2 (SBI) conversations.
"""

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple


def reassemble_tcp_streams(
    pcap_path: str, max_packets: int = 50000
) -> Dict[Tuple[str, int, str, int], str]:
    """
    Reassemble TCP streams from a pcap.

    Returns a dict keyed by (src_ip, src_port, dst_ip, dst_port)
    with the concatenated decoded payload for each direction.
    """
    try:
        from scapy.all import rdpcap, TCP, IP  # type: ignore[import-untyped]  # noqa: F811
    except ImportError:
        raise ImportError("Scapy is required.  pip install scapy")

    packets: List[Any] = list(rdpcap(pcap_path, count=max_packets))  # type: ignore[arg-type]
    streams: Dict[Tuple[str, int, str, int], List[bytes]] = defaultdict(list)

    for pkt in packets:
        if TCP in pkt and IP in pkt:  # type: ignore[operator]
            key: Tuple[str, int, str, int] = (
                str(pkt[IP].src),  # type: ignore[index]
                int(pkt[TCP].sport),  # type: ignore[index]
                str(pkt[IP].dst),  # type: ignore[index]
                int(pkt[TCP].dport),  # type: ignore[index]
            )
            payload = bytes(pkt[TCP].payload)  # type: ignore[index]
            if payload:
                streams[key].append(payload)

    reassembled: Dict[Tuple[str, int, str, int], str] = {}
    for key, chunks in streams.items():
        reassembled[key] = b"".join(chunks).decode(errors="ignore")
    return reassembled


def streams_to_text(
    streams: Dict[Tuple[str, int, str, int], str],
) -> List[str]:
    """Convert reassembled streams to tagged text chunks for RAG ingestion."""
    chunks: List[str] = []
    for (src_ip, src_port, dst_ip, dst_port), data in streams.items():
        if not data.strip():
            continue
        # Heuristic protocol tag
        tag = "[TCP]"
        if dst_port == 8805 or src_port == 8805:
            tag = "[PFCP]"
        elif dst_port == 2123 or src_port == 2123:
            tag = "[GTPv2-C]"
        elif dst_port == 2152 or src_port == 2152:
            tag = "[GTP-U]"
        elif dst_port in (80, 443, 8080) or src_port in (80, 443, 8080):
            tag = "[HTTP2/SBI]"

        header = f"{tag} Stream {src_ip}:{src_port} -> {dst_ip}:{dst_port}"
        # Truncate very long reassembled payloads (keep first 4 KB for RAG)
        truncated = data[:4096]
        if len(data) > 4096:
            truncated += f"\n... (truncated, {len(data)} bytes total)"
        chunks.append(f"{header}\n{truncated}")
    return chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="Reassemble TCP streams from a PCAP")
    parser.add_argument("pcap_file", help="Path to PCAP file")
    parser.add_argument("--max-packets", type=int, default=50000)
    parser.add_argument("--output", help="Output text file (default: stdout)")
    parser.add_argument(
        "--rag-index",
        action="store_true",
        help="Index reassembled streams into RAG (requires --rag-dir)",
    )
    parser.add_argument("--rag-dir", help="RAG knowledge-base directory")
    args = parser.parse_args()

    streams = reassemble_tcp_streams(args.pcap_file, args.max_packets)
    text_chunks = streams_to_text(streams)

    output = "\n\n".join(text_chunks)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Written {len(text_chunks)} streams to {args.output}")
    else:
        print(output)

    if args.rag_index and args.rag_dir:
        from al_5g_ae_core import RAG  # late import

        rag = RAG()
        rag_dir = Path(args.rag_dir)
        for f in rag_dir.glob("*.txt"):
            rag.add_file(str(f))
        rag.add_documents(text_chunks)
        print(f"Indexed {len(text_chunks)} stream chunks into RAG ({len(rag.chunks)} total)")


if __name__ == "__main__":
    main()
