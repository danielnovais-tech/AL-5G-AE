#!/usr/bin/env python3
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportReturnType=false, reportUnusedFunction=false
# pyright: reportUnknownParameterType=false, reportUnknownArgumentType=false
# pyright: reportUntypedBaseClass=false
"""
Microsoft Teams bot for AL-5G-AE.
Responds to @mentions and direct messages using the Bot Framework SDK.

Required environment variables:
  MICROSOFT_APP_ID       – Azure Bot registration Application (client) ID
  MICROSOFT_APP_PASSWORD – Azure Bot registration client secret

Optional:
  RAG_DIR       – Path to knowledge-base directory (default: ./knowledge_base)
  AL5GAE_MODEL  – Model name (default: microsoft/phi-2)
  AL5GAE_DEVICE – cpu or cuda (default: cpu)
  PORT          – HTTP port for the bot endpoint (default: 3978)
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("teams_bot")

# ---------- Environment ----------

APP_ID: str = os.environ.get("MICROSOFT_APP_ID", "")
APP_PASSWORD: str = os.environ.get("MICROSOFT_APP_PASSWORD", "")
RAG_DIR: str = os.environ.get("RAG_DIR", "./knowledge_base")
MODEL_NAME: str = os.environ.get(
    "AL5GAE_MODEL", os.environ.get("DEFAULT_MODEL", "microsoft/phi-2")
)
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

    _tracer = get_tracer("teams_bot")
    with QueryTimer("teams", _tracer, "teams_answer"):
        context = _rag.retrieve(question, k=3) if _rag else None
        if context:
            record_rag_retrieval("teams")
        return str(generate_response(_tokenizer, _model, question, context))


# ---------- Bot handler ----------

def _build_bot() -> Any:
    try:
        from botbuilder.core import (  # type: ignore[import-untyped]
            BotFrameworkAdapterSettings,
            BotFrameworkAdapter,
            TurnContext,
            ActivityHandler,
            MessageFactory,
        )
        from botbuilder.schema import Activity  # type: ignore[import-untyped]
    except ImportError:
        logger.error(
            "botbuilder packages not installed.  "
            "pip install botbuilder-core botbuilder-integration-aiohttp"
        )
        sys.exit(1)

    settings = BotFrameworkAdapterSettings(APP_ID, APP_PASSWORD)
    adapter = BotFrameworkAdapter(settings)

    async def on_error(context: TurnContext, error: Exception) -> None:
        logger.exception("Unhandled error: %s", error)
        await context.send_activity("Sorry, something went wrong.")

    adapter.on_turn_error = on_error

    class TeamsBot(ActivityHandler):
        async def on_message_activity(self, turn_context: TurnContext) -> None:
            user_input = turn_context.activity.text or ""
            # Strip the @mention prefix that Teams prepends
            user_input = user_input.strip()
            if not user_input:
                return

            user_id = (
                turn_context.activity.from_property.id
                if turn_context.activity.from_property
                else "unknown"
            )
            logger.info("Question from %s: %s", user_id, user_input)

            answer = _answer(user_input)
            await turn_context.send_activity(MessageFactory.text(answer))

        async def on_members_added_activity(
            self,
            members_added: List[Any],
            turn_context: TurnContext,
        ) -> None:
            for member in members_added:
                if member.id != turn_context.activity.recipient.id:
                    welcome = MessageFactory.text(
                        "Hello! I'm **AL-5G-AE**, your 5G Core specialist copilot. "
                        "Ask me anything about 5G protocols, procedures, or troubleshooting."
                    )
                    await turn_context.send_activity(welcome)

    bot = TeamsBot()
    return adapter, bot, Activity


# ---------- aiohttp server ----------

def main() -> None:
    try:
        from aiohttp import web  # type: ignore[import-untyped]
    except ImportError:
        logger.error("aiohttp not installed.  pip install aiohttp")
        sys.exit(1)

    adapter, bot, Activity = _build_bot()

    async def handle_messages(request: web.Request) -> web.Response:
        """POST /api/messages – Bot Framework messaging endpoint."""
        if request.content_type != "application/json":
            return web.Response(status=415, text="Unsupported media type")

        body = await request.json()
        activity = Activity().deserialize(body)
        auth_header = request.headers.get("Authorization", "")

        try:
            await adapter.process_activity(activity, auth_header, bot.on_turn)
            return web.Response(status=200)
        except Exception:
            logger.exception("Error processing activity")
            return web.Response(status=500, text="Internal server error")

    app = web.Application()
    app.router.add_post("/api/messages", handle_messages)

    port = int(os.environ.get("PORT", "3978"))
    logger.info("Teams bot listening on http://0.0.0.0:%d/api/messages", port)
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
