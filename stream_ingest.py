#!/usr/bin/env python3
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownParameterType=false, reportUnknownArgumentType=false
"""
Real-time log streaming for AL-5G-AE.
Accepts log lines over WebSocket and indexes them into RAG on the fly.
Optionally consumes from Kafka.
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("stream_ingest")


class StreamIngestor:
    """Buffer incoming log lines and periodically index them into RAG."""

    def __init__(self, rag: Any = None, buffer_size: int = 100):
        self.rag = rag
        self.buffer: List[str] = []
        self.buffer_size = buffer_size

    def _flush(self) -> int:
        """Index buffered lines into RAG.  Returns number of lines flushed."""
        if not self.buffer or not self.rag:
            return 0
        count = len(self.buffer)
        text = "\n".join(self.buffer)
        self.rag.add_documents([text])
        self.buffer.clear()
        logger.info("Indexed %d lines into RAG", count)
        return count

    # ---- WebSocket path ----

    async def handle_websocket(self, websocket: Any) -> None:
        """Handle a single WebSocket connection."""
        async for message in websocket:
            try:
                data = json.loads(message)
                log_line = data.get("log_line", "")
                if not log_line:
                    await websocket.send(json.dumps({"error": "missing log_line"}))
                    continue

                self.buffer.append(log_line)
                if len(self.buffer) >= self.buffer_size:
                    count = self._flush()
                    await websocket.send(json.dumps({"status": "indexed", "count": count}))
                else:
                    await websocket.send(
                        json.dumps({"status": "buffered", "buffer_size": len(self.buffer)})
                    )
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"error": "invalid JSON"}))
            except Exception as exc:
                logger.exception("Handler error")
                await websocket.send(json.dumps({"error": str(exc)}))

    async def start_websocket_server(self, host: str = "0.0.0.0", port: int = 8765) -> None:
        try:
            from websockets.server import serve  # type: ignore[import-untyped]
        except ImportError:
            logger.error("websockets not installed.  pip install websockets")
            return
        async with serve(self.handle_websocket, host, port):
            logger.info("WebSocket server listening on ws://%s:%s", host, port)
            await asyncio.Future()  # run forever

    # ---- Kafka path (optional) ----

    def start_kafka_consumer(
        self,
        bootstrap_servers: str = "localhost:9092",
        topic: str = "al5gae-logs",
        group_id: str = "al5gae",
    ) -> None:
        try:
            from kafka import KafkaConsumer  # type: ignore[import-untyped]
        except ImportError:
            logger.error("kafka-python not installed.  pip install kafka-python")
            return

        def _deserialize(m: Any) -> str:
            return bytes(m).decode("utf-8", errors="ignore") if m else ""

        consumer: Any = KafkaConsumer(
            topic,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            auto_offset_reset="latest",
            value_deserializer=_deserialize,
        )
        logger.info("Kafka consumer started on %s/%s", bootstrap_servers, topic)
        for msg in consumer:  # type: ignore[union-attr]
            self.buffer.append(str(msg.value))  # type: ignore[union-attr]
            if len(self.buffer) >= self.buffer_size:
                self._flush()


def _build_rag(rag_dir: Optional[str]) -> Any:
    """Build a RAG index from a directory of .txt files (or return None)."""
    if not rag_dir:
        return None
    from al_5g_ae_core import RAG  # late import to keep startup fast

    path = Path(rag_dir)
    if not path.exists():
        logger.warning("RAG directory %s not found – continuing without RAG", rag_dir)
        return None
    rag = RAG()
    for f in path.glob("*.txt"):
        rag.add_file(str(f))
    logger.info("Loaded RAG from %s (%d chunks)", rag_dir, len(rag.chunks))
    return rag


def main() -> None:
    parser = argparse.ArgumentParser(description="Real-time log streaming for AL-5G-AE")
    parser.add_argument("--rag-dir", help="Directory with .txt files for RAG (optional)")
    parser.add_argument("--buffer-size", type=int, default=100, help="Lines to buffer before indexing")

    sub = parser.add_subparsers(dest="mode")

    ws = sub.add_parser("websocket", help="Start a WebSocket server")
    ws.add_argument("--host", default="0.0.0.0")
    ws.add_argument("--port", type=int, default=8765)

    kf = sub.add_parser("kafka", help="Start a Kafka consumer")
    kf.add_argument("--bootstrap-servers", default="localhost:9092")
    kf.add_argument("--topic", default="al5gae-logs")
    kf.add_argument("--group-id", default="al5gae")

    args = parser.parse_args()

    rag = _build_rag(args.rag_dir)
    ingestor = StreamIngestor(rag=rag, buffer_size=args.buffer_size)

    if args.mode == "kafka":
        ingestor.start_kafka_consumer(
            bootstrap_servers=args.bootstrap_servers,
            topic=args.topic,
            group_id=args.group_id,
        )
    else:
        # Default to WebSocket
        host = getattr(args, "host", "0.0.0.0")
        port = getattr(args, "port", 8765)
        try:
            asyncio.run(ingestor.start_websocket_server(host, port))
        except KeyboardInterrupt:
            logger.info("Shutting down")


if __name__ == "__main__":
    main()
