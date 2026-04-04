#!/usr/bin/env python3
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportReturnType=false, reportUnusedFunction=false
"""
Slack bot for AL-5G-AE.
Responds to /al5gae slash commands and @mentions.

Required environment variables:
  SLACK_BOT_TOKEN   – Bot User OAuth Token (xoxb-...)
  SLACK_APP_TOKEN   – App-Level Token for Socket Mode (xapp-...)

Optional:
  RAG_DIR           – Path to knowledge-base directory (default: ./knowledge_base)
  AL5GAE_MODEL      – Model name (default: microsoft/phi-2)
  AL5GAE_DEVICE     – cpu or cuda (default: cpu)
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("slack_bot")

# ---------- Environment ----------

SLACK_BOT_TOKEN: Optional[str] = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN: Optional[str] = os.environ.get("SLACK_APP_TOKEN")
RAG_DIR: str = os.environ.get("RAG_DIR", "./knowledge_base")
MODEL_NAME: str = os.environ.get("AL5GAE_MODEL", os.environ.get("DEFAULT_MODEL", "microsoft/phi-2"))
DEVICE: str = os.environ.get("AL5GAE_DEVICE", "cpu")

# ---------- Lazy globals ----------

_tokenizer: Any = None
_model: Any = None
_rag: Any = None


def _ensure_model() -> None:
    global _tokenizer, _model
    if _tokenizer is not None:
        return
    from al_5g_ae_core import load_model  # late import – fast startup
    logger.info("Loading model %s on %s …", MODEL_NAME, DEVICE)
    _tokenizer, _model = load_model(MODEL_NAME, DEVICE)


def _ensure_rag() -> None:
    global _rag
    if _rag is not None:
        return
    from al_5g_ae_core import RAG  # late import
    rag_dir = Path(RAG_DIR)
    if not rag_dir.exists():
        logger.warning("RAG directory %s not found – running without RAG", RAG_DIR)
        return
    _rag = RAG()
    for f in rag_dir.glob("*.txt"):
        _rag.add_file(str(f))
    logger.info("Loaded RAG from %s (%d chunks)", RAG_DIR, len(_rag.chunks))


def _answer(question: str) -> str:
    from al_5g_ae_core import generate_response  # late import
    from observability import get_tracer, QueryTimer, record_rag_retrieval

    _ensure_model()
    _ensure_rag()

    _tracer = get_tracer("slack_bot")
    with QueryTimer("slack", _tracer, "slack_answer"):
        context = _rag.retrieve(question, k=3) if _rag else None
        if context:
            record_rag_retrieval("slack")
        return str(generate_response(_tokenizer, _model, question, context))

def _build_app() -> Any:
    try:
        from slack_bolt import App  # type: ignore[import-untyped]
    except ImportError:
        logger.error("slack-bolt not installed.  pip install slack-bolt")
        sys.exit(1)

    app: Any = App(token=SLACK_BOT_TOKEN)

    @app.command("/al5gae")  # type: ignore[misc]
    def handle_command(ack: Any, command: Any, say: Any) -> None:  # noqa: F811
        ack()
        question = command.get("text", "").strip()
        if not question:
            say("Please provide a question. Example: `/al5gae What is PFCP?`")
            return
        user = command.get("user_name", "unknown")
        logger.info("Question from %s: %s", user, question)
        answer = _answer(question)
        say(answer)

    @app.event("app_mention")  # type: ignore[misc]
    def handle_mention(event: Any, say: Any) -> None:  # noqa: F811
        question = event.get("text", "").strip()
        if not question:
            return
        logger.info("Mention from %s: %s", event.get("user", "?"), question)
        answer = _answer(question)
        say(answer)

    return app


def main() -> None:
    if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
        logger.error(
            "Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN environment variables.\n"
            "  PowerShell:  $env:SLACK_BOT_TOKEN = 'xoxb-...'\n"
            "  Bash:        export SLACK_BOT_TOKEN=xoxb-..."
        )
        sys.exit(1)

    from slack_bolt.adapter.socket_mode import SocketModeHandler  # type: ignore[import-untyped]

    app: Any = _build_app()
    handler: Any = SocketModeHandler(app, SLACK_APP_TOKEN)
    logger.info("Slack bot started – listening for /al5gae and @mentions")
    handler.start()  # type: ignore[no-untyped-call]


if __name__ == "__main__":
    main()
