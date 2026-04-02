#!/usr/bin/env python3
"""Gradio web interface for AL-5G-AE.

Includes a minimal UI mode to help isolate browser-side JS issues.
"""

import argparse
import inspect
from pathlib import Path
from typing import Optional

import gradio as gr

from al_5g_ae import DEFAULT_MODEL, RAG, generate_response, load_model, setup_run_logger


def _load_rag(rag_path: str) -> RAG:
    rag = RAG()
    path = Path(rag_path)

    if path.is_dir():
        for file_path in sorted(path.glob("*.txt")):
            rag.add_file(str(file_path), source_label=file_path.name)
    else:
        rag.add_file(str(path), source_label=path.name)

    print(f"RAG indexed {len(rag.chunks)} chunks from {path}")
    return rag


def create_ui(
    model_name: str,
    device: str,
    rag_dir: Optional[str] = None,
    *,
    minimal_ui: bool = False,
    run_log: Optional[str] = str(Path("logs") / "al_5g_ae_web.log"),
    verbose: bool = False,
) -> gr.Blocks:
    logger = setup_run_logger(run_log if (run_log or "").strip() else None, verbose=verbose)
    logger.info("Starting web UI")
    logger.info("device=%s model=%s", device, model_name)
    if rag_dir:
        logger.info("rag_dir=%s", rag_dir)

    # Minimal UI is meant to isolate frontend/browser issues.
    # Keep it model-free so it starts instantly.
    if minimal_ui:
        demo = gr.Interface(
            fn=lambda message: f"OK (minimal UI). You said: {message}",
            inputs=gr.Textbox(label="Your question"),
            outputs=gr.Textbox(label="Answer"),
            title="AL-5G-AE",
            description="Minimal UI mode (no model load) to isolate browser-side JS issues.",
        )

        return demo

    # Lazy-load the model on first request to reduce startup time.
    tokenizer = None
    model = None

    rag = None
    if rag_dir:
        try:
            rag = _load_rag(rag_dir)
        except Exception as exc:
            logger.warning("Failed to load RAG from %s: %s", rag_dir, exc)
            rag = None

    def respond(message, history):
        nonlocal tokenizer, model
        if tokenizer is None or model is None:
            tokenizer, model = load_model(model_name, device)

        context = rag.retrieve(message, k=3) if rag else None
        logger.info("query=%r retrieved=%d", message, len(context or []))
        if verbose and context:
            logger.debug("retrieved_chunks=%r", context)
        return generate_response(tokenizer, model, message, context)

    title = "AL-5G-AE"
    description = "Ask anything about 5G Core troubleshooting, logs, protocols, or workflows."

    # Prefer ChatInterface when available (less custom wiring, generally more stable).
    chat_interface = getattr(gr, "ChatInterface", None)
    if chat_interface is not None:
        demo = gr.ChatInterface(
            fn=respond,
            title=title,
            description=description,
            theme=gr.themes.Soft(),
        )
    else:
        with gr.Blocks(title=title, theme=gr.themes.Soft()) as demo:
            gr.Markdown("# AL-5G-AE – 5G Core Specialist Copilot")
            gr.Markdown(description)

            chatbot = gr.Chatbot()
            msg = gr.Textbox(label="Your question")
            clear = gr.Button("Clear")

            def user(user_message, history):
                return "", (history or []) + [[user_message, None]]

            def bot(history):
                history = history or []
                user_message = history[-1][0]
                response = respond(user_message, history)
                history[-1][1] = response
                return history

            msg.submit(user, [msg, chatbot], [msg, chatbot], queue=False).then(
                bot, chatbot, chatbot
            )
            clear.click(lambda: [], None, chatbot, queue=False)

    return demo


def launch_ui(
    demo,
    *,
    host: str,
    port: int,
    debug: bool,
    logger,
):
    # Gradio's Python API has changed across major versions (notably 3.x -> 4.x).
    # To keep this UI working across versions, only pass launch() kwargs that
    # exist in the installed Gradio version.
    launch_kwargs = {
        "server_name": host,
        "server_port": port,
        "debug": debug,
        "show_error": True,
        "analytics_enabled": False,
    }
    supported = set(inspect.signature(demo.launch).parameters.keys())
    logger.info("Launching UI on http://%s:%s", host, port)
    demo.launch(**{k: v for k, v in launch_kwargs.items() if k in supported})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--rag-dir", help="Directory (or file) with text files for RAG")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable Gradio debug mode (more verbose logs + error display).",
    )
    parser.add_argument(
        "--minimal-ui",
        action="store_true",
        help="Run a minimal UI to isolate browser-side JS issues.",
    )
    parser.add_argument(
        "--run-log",
        default=str(Path("logs") / "al_5g_ae_web.log"),
        help="Write a run log to this file (set to empty string to disable)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging (debug-level).",
    )
    args = parser.parse_args()

    logger = setup_run_logger(args.run_log if str(args.run_log).strip() else None, verbose=args.verbose)
    demo = create_ui(
        args.model,
        args.device,
        args.rag_dir,
        minimal_ui=args.minimal_ui,
        run_log=args.run_log,
        verbose=args.verbose,
    )
    launch_ui(demo, host=args.host, port=args.port, debug=args.debug, logger=logger)
