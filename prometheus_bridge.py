#!/usr/bin/env python3
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportReturnType=false, reportUnknownArgumentType=false
# pyright: reportGeneralTypeIssues=false
"""
Prometheus Alertmanager webhook bridge for AL-5G-AE.

Receives firing alerts from Alertmanager, queries the model/RAG for
root-cause analysis and remediation, then forwards the answer to a
configurable webhook (Slack, Teams, or any endpoint that accepts JSON
``{"text": "..."}``).

Also exposes ``/metrics`` for Prometheus scraping and ``/health`` for
liveness probes.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp
from fastapi import FastAPI, Request, Response, HTTPException
from prometheus_client import (
    Counter,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
import uvicorn

from al_5g_ae_core import load_model, generate_response, RAG, DEFAULT_MODEL
from observability import get_tracer, QueryTimer, record_rag_retrieval

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("prometheus_bridge")
_tracer = get_tracer("prometheus_bridge")

# ---------------------------------------------------------------------------
# Configuration (all from environment)
# ---------------------------------------------------------------------------
MODEL_NAME = os.environ.get("AL5GAE_MODEL", DEFAULT_MODEL)
RAG_DIR = os.environ.get("RAG_DIR", "./knowledge_base")
FORWARD_WEBHOOK_URL = os.environ.get("FORWARD_WEBHOOK_URL", "")
PORT = int(os.environ.get("BRIDGE_PORT", "9090"))

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
ALERTS_RECEIVED = Counter(
    "al5gae_alerts_received_total", "Total alerts received", ["alertname"],
)
ALERTS_PROCESSED = Counter(
    "al5gae_alerts_processed_total", "Alerts successfully processed", ["alertname"],
)
ALERTS_FAILED = Counter(
    "al5gae_alerts_failed_total", "Alerts that failed processing", ["alertname"],
)
QUERY_DURATION = Histogram(
    "al5gae_query_duration_seconds", "Time to generate a response",
)
RAG_HITS = Counter("al5gae_rag_hits_total", "Number of RAG retrievals")

# ---------------------------------------------------------------------------
# Global state (initialised at startup)
# ---------------------------------------------------------------------------
tokenizer: Any = None
model: Any = None
rag: Optional[RAG] = None


def _init_model_and_rag() -> None:
    global tokenizer, model, rag
    logger.info("Loading model %s …", MODEL_NAME)
    tokenizer, model = load_model(MODEL_NAME, "cpu")

    rag_path = Path(RAG_DIR)
    if rag_path.exists():
        rag = RAG()
        for f in rag_path.rglob("*.txt"):
            rag.add_file(str(f))
        logger.info("Loaded RAG from %s", RAG_DIR)
    else:
        logger.warning("RAG directory %s not found", RAG_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_alert(alert: Dict[str, Any]) -> str:
    """Turn an Alertmanager alert dict into a natural-language question."""
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})
    alertname = labels.get("alertname", "unknown")
    instance = labels.get("instance", "unknown")
    severity = labels.get("severity", "unknown")
    summary = annotations.get("summary", "No summary")
    description = annotations.get("description", "No description")
    return (
        f"Alert: {alertname} on {instance} (severity: {severity})\n"
        f"Summary: {summary}\n"
        f"Description: {description}\n"
        "What could be the root cause and what actions should be taken?"
    )


async def _forward_to_webhook(text: str) -> None:
    """POST the analysis to a configured webhook (Slack / Teams / generic)."""
    if not FORWARD_WEBHOOK_URL:
        return
    payload = {"text": text}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                FORWARD_WEBHOOK_URL, json=payload, timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status >= 400:
                    logger.error("Webhook returned HTTP %s", resp.status)
    except Exception:
        logger.exception("Failed to forward to webhook")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def _lifespan(_app: FastAPI):  # type: ignore[override]
    _init_model_and_rag()
    yield


app = FastAPI(title="AL-5G-AE Prometheus Bridge", lifespan=_lifespan)


@app.post("/webhook")
async def alertmanager_webhook(request: Request):
    """Receive an Alertmanager webhook payload and analyse each firing alert."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    alerts = data.get("alerts", [])
    for alert in alerts:
        if alert.get("status") != "firing":
            continue

        alertname = alert.get("labels", {}).get("alertname", "unknown")
        ALERTS_RECEIVED.labels(alertname=alertname).inc()

        question = _format_alert(alert)
        logger.info("Processing alert: %s", alertname)

        with QueryTimer("prometheus_bridge", _tracer, f"alert_{alertname}"):
            # RAG retrieval
            context = None
            if rag:
                context = rag.retrieve(question, k=3)
                if context:
                    RAG_HITS.inc()
                    record_rag_retrieval("prometheus_bridge")

            # Generate response
            try:
                with QUERY_DURATION.time():
                    answer = generate_response(tokenizer, model, question, context)
                await _forward_to_webhook(f"*Alert: {alertname}*\n{answer}")
                ALERTS_PROCESSED.labels(alertname=alertname).inc()
            except Exception:
                logger.exception("Response generation failed for %s", alertname)
                ALERTS_FAILED.labels(alertname=alertname).inc()
                await _forward_to_webhook(
                    f"*Alert: {alertname}*\nFailed to analyse — see bridge logs.",
                )

    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    """Prometheus scrape endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health() -> Dict[str, object]:
    return {
        "status": "ok",
        "model_loaded": model is not None,
        "rag_loaded": rag is not None,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
