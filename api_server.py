#!/usr/bin/env python3
# pyright: reportUnknownVariableType=false, reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
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
import hmac
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response as StarletteResponse

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

# --------------------------------------------------------------------------- #
# Rate-limiter (slowapi – optional)
# --------------------------------------------------------------------------- #
try:
    from slowapi import Limiter  # type: ignore[import-untyped]
    from slowapi.util import get_remote_address  # type: ignore[import-untyped]
    from slowapi.errors import RateLimitExceeded  # type: ignore[import-untyped]
    _limiter: Any = Limiter(
        key_func=get_remote_address,
        default_limits=[os.environ.get("AL5GAE_RATE_LIMIT", "60/minute")],
        storage_uri=os.environ.get("AL5GAE_RATE_LIMIT_STORAGE", "memory://"),
    )
    _slowapi_available = True
except ImportError:
    _limiter = None
    _slowapi_available = False

# --------------------------------------------------------------------------- #
# API key authentication
# --------------------------------------------------------------------------- #
_api_keys: list[str] = []
_auth_enabled = False


def _init_api_keys() -> None:
    """Load API keys from AL5GAE_API_KEYS (comma-separated) env var."""
    global _api_keys, _auth_enabled
    raw = os.environ.get("AL5GAE_API_KEYS", "").strip()
    if raw:
        _api_keys = [k.strip() for k in raw.split(",") if k.strip()]
        _auth_enabled = True


_init_api_keys()

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _verify_api_key(
    api_key: Optional[str] = Depends(_api_key_header),
) -> Optional[str]:
    """Validate the API key if authentication is enabled."""
    if not _auth_enabled:
        return None
    if api_key is None:
        raise HTTPException(status_code=401, detail="Missing API key")
    # Constant-time comparison to prevent timing attacks
    for valid_key in _api_keys:
        if hmac.compare_digest(api_key, valid_key):
            return api_key
    raise HTTPException(status_code=403, detail="Invalid API key")


# --------------------------------------------------------------------------- #
# FastAPI application
# --------------------------------------------------------------------------- #
app = FastAPI(title="AL-5G-AE API", version="0.2")

# Attach rate limiter
if _slowapi_available and _limiter is not None:
    app.state.limiter = _limiter  # type: ignore[union-attr]

    async def _rate_limit_handler(request: Request, exc: Exception) -> StarletteResponse:
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded", "detail": str(exc)},
        )

    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)  # type: ignore[arg-type]

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
    """Health check — no auth required."""
    global _tokenizer, _model, _rag
    return {
        "status": "ok",
        "model_loaded": bool(_tokenizer is not None and _model is not None),
        "rag_loaded": bool(_rag is not None),
        "rag_chunks": len(_rag.chunks) if _rag else 0,
        "auth_enabled": _auth_enabled,
        "rate_limit_enabled": _slowapi_available,
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
async def query(
    req: QueryRequest,
    _key: Optional[str] = Depends(_verify_api_key),
) -> Dict[str, object]:
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
    _key: Optional[str] = Depends(_verify_api_key),
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
    _key: Optional[str] = Depends(_verify_api_key),
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
    parser.add_argument(
        "--api-keys",
        default=None,
        help="Comma-separated API keys (overrides AL5GAE_API_KEYS env var)",
    )
    parser.add_argument(
        "--generate-key",
        action="store_true",
        help="Generate a random API key and exit",
    )
    parser.add_argument(
        "--rate-limit",
        default=None,
        help="Rate limit string, e.g. '60/minute' (overrides AL5GAE_RATE_LIMIT env var)",
    )
    args = parser.parse_args()

    # Key generation utility
    if args.generate_key:
        key = secrets.token_urlsafe(32)
        print(f"Generated API key: {key}")
        print("Set via:  export AL5GAE_API_KEYS=\"{key}\"")
        print("Or pass:  --api-keys \"{key}\"")
        return

    # Override env-based config with CLI args
    if args.api_keys:
        os.environ["AL5GAE_API_KEYS"] = args.api_keys
        _init_api_keys()
    if args.rate_limit:
        os.environ["AL5GAE_RATE_LIMIT"] = args.rate_limit

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
