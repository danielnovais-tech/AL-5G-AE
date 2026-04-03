#!/usr/bin/env python3
"""Hugging Face Spaces entrypoint.

Spaces expects an `app.py` that defines a Gradio `demo` object.

Environment variables (optional):
- `AL5GAE_MODEL`   : Hugging Face model name/path
- `AL5GAE_DEVICE`  : `cpu` or `cuda`
- `AL5GAE_RAG_DIR` : folder/file to index for retrieval (defaults to ./knowledge_base)
- `AL5GAE_RUN_LOG` : run log path (empty disables)
"""

import os

from al_5g_ae_core import DEFAULT_MODEL
from web_ui import create_ui


MODEL_NAME = os.environ.get("AL5GAE_MODEL", DEFAULT_MODEL)
DEVICE = os.environ.get("AL5GAE_DEVICE", "cpu")
RAG_DIR = os.environ.get("AL5GAE_RAG_DIR", "knowledge_base")
RUN_LOG = os.environ.get("AL5GAE_RUN_LOG", "")


# Gradio Spaces expects this global name.
demo = create_ui(
    MODEL_NAME,
    DEVICE,
    RAG_DIR,
    minimal_ui=False,
    run_log=RUN_LOG,
    verbose=False,
)


if __name__ == "__main__":
    # Local dev convenience.
    demo.launch(show_error=True)
