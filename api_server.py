#!/usr/bin/env python3
"""FastAPI server for AL-5G-AE.

Endpoints:
- GET  /health
- POST /query            (JSON)
- POST /upload_log       (multipart/form-data)
- POST /upload_pcap      (multipart/form-data)

This reuses the shared backend utilities (al_5g_ae_core.py).
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from al_5g_ae_core import (
    DEFAULT_DEVICE,
    DEFAULT_MODEL,
    RAG,
    RAG_AVAILABLE,
    generate_response,
    load_model,
    setup_run_logger,
)
from pcap_ingest import process_pcap, summaries_to_text
from observability import (
    get_tracer,
    QueryTimer,
    record_rag_retrieval,
)

# --- OTEL auto-instrumentation for FastAPI (if available) ---
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore[import-untyped]
    _otel_fastapi = True
except ImportError:
    _otel_fastapi = False

app = FastAPI(title="AL-5G-AE API", version="0.1")

if _otel_fastapi:
    FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]

_tracer = get_tracer("api_server")

_state_lock = asyncio.Lock()
_tokenizer: Any = None
_model: Any = None
_rag: Optional[RAG] = None
_logger = None


async def _ensure_backend_loaded(*, model_name: str, device: str, rag_dir: Optional[str]) -> None:
    global _tokenizer, _model, _rag, _logger
    async with _state_lock:
        if _logger is None:
            _logger = setup_run_logger(str(Path("logs") / "al_5g_ae_api.log"), verbose=False)
            _logger.info("Starting AL-5G-AE API")

        if _tokenizer is None or _model is None:
            _logger.info("loading_model device=%s model=%s", device, model_name)
            _tokenizer, _model = await run_in_threadpool(load_model, model_name, device)

        if rag_dir and _rag is None:
            if not RAG_AVAILABLE:
                _logger.warning("rag_requested_but_unavailable rag_dir=%s", rag_dir)
            else:
                rag_path = Path(rag_dir)
                r = RAG()
                if rag_path.is_dir():
                    for fp in sorted(rag_path.glob("*.txt")):
                        r.add_file(str(fp), source_label=fp.name)
                else:
                    r.add_file(str(rag_path), source_label=rag_path.name)
                _rag = r
                _logger.info("rag_loaded chunks=%d rag_dir=%s", len(_rag.chunks), rag_dir)


@app.get("/health")
async def health() -> Dict[str, object]:
    global _tokenizer, _model, _rag
    return {
        "status": "ok",
        "model_loaded": bool(_tokenizer is not None and _model is not None),
        "rag_loaded": bool(_rag is not None),
        "rag_chunks": len(_rag.chunks) if _rag else 0,
    }


class QueryRequest(BaseModel):
    question: str
    rag_dir: Optional[str] = None
    top_k: int = 3
    max_tokens: int = 512
    temperature: float = 0.7
    model: str = DEFAULT_MODEL
    device: str = DEFAULT_DEVICE


@app.post("/query")
async def query(req: QueryRequest) -> Dict[str, object]:
    await _ensure_backend_loaded(model_name=req.model, device=req.device, rag_dir=req.rag_dir)

    with QueryTimer("api", _tracer, "api_query"):
        context = None
        if _rag is not None:
            context = await run_in_threadpool(_rag.retrieve, req.question, int(req.top_k))
            if context:
                record_rag_retrieval("api")

        if _logger:
            _logger.info("api_query=%r retrieved=%d", req.question, len(context or []))

        answer = await run_in_threadpool(
            generate_response,
            _tokenizer,
            _model,
            req.question,
            context,
            int(req.max_tokens),
            float(req.temperature),
        )

    return {"answer": answer, "retrieved": len(context or [])}


@app.post("/upload_log")
async def upload_log(
    file: UploadFile = File(...),
    rag_dir: Optional[str] = Form(None),
    model: str = Form(DEFAULT_MODEL),
    device: str = Form(DEFAULT_DEVICE),
) -> Any:
    await _ensure_backend_loaded(model_name=model, device=device, rag_dir=rag_dir)

    if _rag is None:
        return JSONResponse(
            status_code=400,
            content={"error": "RAG is not enabled/available. Start with rag_dir or install RAG deps."},
        )

    content = await file.read()
    text = content.decode("utf-8", errors="ignore")
    source: str = file.filename or "uploaded.log"
    await run_in_threadpool(_rag.add_documents, [text], sources=[source])

    if _logger:
        _logger.info("api_upload_log source=%s chunks=%d", source, len(_rag.chunks))

    return {"status": "indexed", "source": source, "rag_chunks": len(_rag.chunks)}


@app.post("/upload_pcap")
async def upload_pcap(
    file: UploadFile = File(...),
    rag_dir: Optional[str] = Form(None),
    max_packets: int = Form(2000),
    pcap_filter: Optional[str] = Form(None),
    model: str = Form(DEFAULT_MODEL),
    device: str = Form(DEFAULT_DEVICE),
) -> Any:
    await _ensure_backend_loaded(model_name=model, device=device, rag_dir=rag_dir)

    if _rag is None:
        return JSONResponse(
            status_code=400,
            content={"error": "RAG is not enabled/available. Start with rag_dir or install RAG deps."},
        )

    source: str = file.filename or "uploaded.pcap"
    suffix = Path(source).suffix or ".pcap"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        tmp.write(await file.read())

    try:
        summaries = await run_in_threadpool(
            process_pcap,
            tmp_path,
            max_packets=int(max_packets),
            tshark_display_filter=str(pcap_filter) if pcap_filter else None,
        )
        pcap_text = summaries_to_text(summaries, header=f"PCAP summary from {source}")
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass

    await run_in_threadpool(_rag.add_documents, [pcap_text], sources=[source])

    if _logger:
        _logger.info("api_upload_pcap source=%s chunks=%d", source, len(_rag.chunks))

    return {"status": "indexed", "source": source, "rag_chunks": len(_rag.chunks)}


def main() -> None:
    parser = argparse.ArgumentParser(description="AL-5G-AE FastAPI server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--device", default=DEFAULT_DEVICE, choices=["cpu", "cuda"])
    parser.add_argument("--rag-dir", default=None)
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev)")
    args = parser.parse_args()

    # Preload the backend in a best-effort way (still lazy for RAG uploads).
    async def _warmup():
        await _ensure_backend_loaded(model_name=args.model, device=args.device, rag_dir=args.rag_dir)

    try:
        asyncio.run(_warmup())
    except Exception:
        # If warmup fails (e.g., no model downloaded yet), still allow uvicorn to start.
        pass

    import uvicorn

    uvicorn.run(
        "api_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
