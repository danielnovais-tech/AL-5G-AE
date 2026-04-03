#!/usr/bin/env python3
"""Gradio web interface for AL-5G-AE.

Includes a minimal UI mode to help isolate browser-side JS issues.
"""

import argparse
import inspect
import socket
import traceback
from pathlib import Path
from typing import Any, List, Optional, Tuple

import gradio as gr

from al_5g_ae_core import (
    DEFAULT_MODEL,
    RAG,
    generate_response,
    load_model,
    setup_run_logger,
)
from pcap_ingest import process_pcap, summaries_to_text

# JS polyfill injected into every Gradio page to suppress the
# "Dft.clearMarks is not a function" console error.  The error is
# caused by React DevTools (or similar extensions) monkey-patching
# performance timing APIs.  This snippet ensures the required
# methods always exist.
_DFT_CLEARMARKS_POLYFILL = """
() => {
  // Ensure performance.clearMarks is always callable.
  if (typeof performance !== 'undefined' && typeof performance.clearMarks !== 'function') {
    performance.clearMarks = function() {};
  }
  // Patch the Dft global if it exists (React DevTools internals).
  if (typeof window.Dft !== 'undefined' && typeof window.Dft.clearMarks !== 'function') {
    window.Dft.clearMarks = function() {};
  }
  // Proactively define Dft.clearMarks even if Dft doesn't exist yet,
  // in case it gets created later by an extension.
  if (typeof window.Dft === 'undefined') {
    window.Dft = { clearMarks: function() {} };
  }
}
"""


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
    pcap_file: Optional[str] = None,
    pcap_max_packets: int = 2000,
    pcap_filter: Optional[str] = None,
    run_log: Optional[str] = str(Path("logs") / "al_5g_ae_web.log"),
    verbose: bool = False,
) -> gr.Blocks:
    logger = setup_run_logger(
        run_log if (run_log or "").strip() else None,
        verbose=verbose,
        name="al_5g_ae_web",
    )
    logger.info("Starting web UI")
    logger.info("device=%s model=%s", device, model_name)
    if rag_dir:
        logger.info("rag_dir=%s", rag_dir)

    # Minimal UI is meant to isolate frontend/browser issues.
    # Keep it model-free so it starts instantly.
    if minimal_ui:
        demo = gr.Interface(
            fn=lambda message: f"OK (minimal UI). You said: {message}",  # type: ignore[misc]
            inputs=gr.Textbox(label="Your question"),
            outputs=gr.Textbox(label="Answer"),
            title="AL-5G-AE",
            description="Minimal UI mode (no model load) to isolate browser-side JS issues.",
            js=_DFT_CLEARMARKS_POLYFILL,
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

    if pcap_file and rag is not None:
        try:
            summaries = process_pcap(
                pcap_file,
                max_packets=max(1, int(pcap_max_packets)),
                tshark_display_filter=str(pcap_filter) if pcap_filter else None,
            )
            pcap_text = summaries_to_text(
                summaries,
                header=f"PCAP summary from {Path(pcap_file).name}",
            )
            rag.add_documents([pcap_text], sources=[Path(pcap_file).name])
            logger.info("pcap_ingested_into_rag=1 pcap_file=%s", pcap_file)
        except Exception as exc:
            logger.warning("pcap_ingestion_failed pcap_file=%s err=%s", pcap_file, exc)

    def respond(message: Any, history: Any) -> str:
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
        # Some Gradio versions/stubs don't accept theme= here.
        # Inject JS polyfill to suppress Dft.clearMarks browser errors.
        ci_kwargs: dict[str, Any] = dict(fn=respond, title=title, description=description)
        if "js" in set(inspect.signature(chat_interface).parameters.keys()):
            ci_kwargs["js"] = _DFT_CLEARMARKS_POLYFILL
        demo = gr.ChatInterface(**ci_kwargs)
    else:
        themes_mod = getattr(gr, "themes", None)
        theme_obj = themes_mod.Soft() if themes_mod is not None else None

        with gr.Blocks(title=title, theme=theme_obj, js=_DFT_CLEARMARKS_POLYFILL) as demo:
            gr.Markdown("# AL-5G-AE – 5G Core Specialist Copilot")
            gr.Markdown(description)

            chatbot = gr.Chatbot()
            msg = gr.Textbox(label="Your question")
            clear = gr.Button("Clear")

            def user(user_message: Any, history: Any) -> Tuple[str, List[Any]]:
                hist: List[Any] = history or []
                return "", hist + [[user_message, None]]

            def bot(history: Any) -> List[Any]:
                history = history or []
                user_message: str = history[-1][0]
                response = respond(user_message, history)
                history[-1][1] = response
                return history

            msg.submit(user, [msg, chatbot], [msg, chatbot], queue=False).then(
                bot, chatbot, chatbot
            )
            clear.click(lambda: [], None, chatbot, queue=False)

    return demo


def launch_ui(
    demo: Any,
    *,
    host: str,
    port: int,
    debug: bool,
    logger: Any,
) -> None:
    def _pick_free_port(*, start_port: int, scan: int = 50) -> int:
        """Pick a free TCP port.

        - If start_port == 0, ask the OS for an ephemeral free port.
        - Otherwise, scan from start_port upward (best-effort).

        Note: this reduces (but cannot fully eliminate) races between the scan
        and Gradio binding the port.
        """

        if start_port == 0:
            with socket.create_server((host, 0)) as srv:
                return int(srv.getsockname()[1])

        for candidate in range(start_port, start_port + max(1, int(scan))):
            try:
                with socket.create_server((host, candidate)):
                    return candidate
            except OSError:
                continue

        raise OSError(
            f"No free port found in range {start_port}-{start_port + max(1, int(scan)) - 1} on {host}"
        )

    # Gradio's Python API has changed across major versions (notably 3.x -> 4.x).
    # To keep this UI working across versions, only pass launch() kwargs that
    # exist in the installed Gradio version.
    launch_kwargs: dict[str, Any] = {
        "server_name": host,
        "server_port": port,
        "debug": debug,
        "show_error": True,
        "analytics_enabled": False,
    }
    supported = set(inspect.signature(demo.launch).parameters.keys())

    def _do_launch(chosen_port: int):
        kwargs = dict(launch_kwargs)
        kwargs["server_port"] = chosen_port
        url = f"http://{host}:{chosen_port}"
        logger.info("Launching UI on %s", url)
        # Also print for environments where logger output is swallowed.
        print(f"Launching UI on {url}", flush=True)
        demo.launch(**{k: v for k, v in kwargs.items() if k in supported})

    # Pre-scan for a free port to avoid repeated Gradio retries.
    # If the requested port range is exhausted, fall back to an OS-assigned
    # ephemeral port (equivalent to --port 0).
    try:
        chosen_port = _pick_free_port(start_port=port, scan=50)
    except OSError:
        chosen_port = _pick_free_port(start_port=0, scan=1)
    launch_kwargs["server_port"] = chosen_port

    try:
        _do_launch(chosen_port)
    except OSError as exc:
        message = str(exc)
        port_in_use = (
            "Cannot find empty port" in message
            or "winerror 10048" in message.lower()
            or "address already in use" in message.lower()
        )
        # If the chosen port is already taken (common during development), try a small range.
        if port_in_use:
            for alt_port in range(chosen_port + 1, chosen_port + 51):
                try:
                    _do_launch(alt_port)
                    return
                except OSError:
                    continue
        raise


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--model", default=DEFAULT_MODEL)
        parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
        parser.add_argument("--rag-dir", help="Directory (or file) with text files for RAG")
        parser.add_argument(
            "--pcap-file",
            help="PCAP/PCAPNG file to ingest (indexed only if RAG is enabled)",
        )
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

        logger = setup_run_logger(
            args.run_log if str(args.run_log).strip() else None,
            verbose=args.verbose,
            name="al_5g_ae_web",
        )

        demo = create_ui(
            args.model,
            args.device,
            args.rag_dir,
            minimal_ui=args.minimal_ui,
            pcap_file=args.pcap_file,
            pcap_max_packets=args.pcap_max_packets,
            pcap_filter=args.pcap_filter,
            run_log=args.run_log,
            verbose=args.verbose,
        )
        launch_ui(demo, host=args.host, port=args.port, debug=args.debug, logger=logger)
    except Exception:
        # Ensure failures never look "silent" even if Gradio swallows logging.
        traceback.print_exc()
        raise
