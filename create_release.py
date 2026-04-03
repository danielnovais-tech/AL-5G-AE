#!/usr/bin/env python3
"""Create a GitHub release for AL-5G-AE.

Prerequisites:
    pip install PyGithub
    Set GITHUB_TOKEN env var (or pass --token).

Usage:
    python create_release.py                          # creates v1.0.0
    python create_release.py --tag v1.1.0             # custom version
    python create_release.py --draft                  # create as draft
"""

import argparse
import os
import sys

try:
    from github import Github
except ImportError:
    print("ERROR: Install PyGithub first:  pip install PyGithub")
    sys.exit(1)


REPO_NAME = "danielnovais-tech/AL-5G-AE"

CHANGELOG_V1 = """\
# AL-5G-AE v1.0.0 — Production Release

**AL-5G-AE** is a specialized Small Language Model (SLM) copilot for 5G Core \
network operations, troubleshooting, and protocol analysis.

## Highlights

- **5G-focused SLM** — Phi-2 (2.7B) default with TinyLlama (1.1B) fallback; \
runs on CPU or GPU.
- **RAG (Retrieval-Augmented Generation)** — Index specs, runbooks, and logs \
with semantic or multiline-aware chunking (FAISS + sentence-transformers).
- **PCAP ingestion** — Deep packet decode via `tshark -T ek` (JSON export) \
with Scapy fallback. Protocol-aware tagging: `[PFCP]`, `[GTPv2-C]`, `[GTP-U]`, \
`[NGAP]`, `[HTTP/2]`.
- **Log file ingestion** — Index any `.log`/`.txt` file into RAG.
- **Knowledge Base Builder** (`kb_builder.py`) — Convert Markdown to plain text, \
slice large logs (`--log-lines`, `--log-regex`, `--since`/`--until`), group \
multiline entries (`--log-multiline`).
- **CLI** — Interactive or single-query mode with full flag set.
- **Web UI (Gradio)** — Chat interface with auto port fallback (`--port 0`), \
`--minimal-ui` for fast frontend testing, `Dft.clearMarks` polyfill.
- **REST API (FastAPI)** — `/query`, `/upload_log`, `/upload_pcap`, `/health`.
- **Lazy loading** — Heavy deps (torch, transformers, faiss, NLTK) imported on \
demand; `web_ui.py --minimal-ui` starts in ~1 second.
- **Docker & Hugging Face Spaces** — `Dockerfile` with tshark pre-installed; \
`packages.txt` for Spaces apt packages; `app.py` as Spaces entrypoint.

## Starter Knowledge Base

Includes non-copyrighted reference files:
- `ts_23501.txt` — 5G Core architecture
- `ts_23502.txt` — Registration procedure
- `vendor_troubleshooting.txt` — Common AMF issues
- `pcap-protocol-map.txt` — Protocol dissection reference (ports, filters, \
JSON paths)
- `protocols-cheatsheet.md` — Quick protocol reference
- `wireshark-filters.md` — Useful Wireshark display filters
- `troubleshooting-checklist.md` — Step-by-step triage guide

## Quick Start

```bash
git clone https://github.com/danielnovais-tech/AL-5G-AE.git
cd AL-5G-AE
pip install -r requirements.txt

# CLI
python al_5g_ae.py --rag-dir knowledge_base

# Web UI
python web_ui.py --rag-dir knowledge_base --debug --port 0

# REST API
python api_server.py --rag-dir knowledge_base

# Docker
docker build -t al-5g-ae .
docker run --rm -p 7860:7860 al-5g-ae
```

## Files

| File | Purpose |
|---|---|
| `al_5g_ae.py` | CLI entrypoint |
| `al_5g_ae_core.py` | Shared core (model, RAG, chunking, generation) |
| `web_ui.py` | Gradio web interface |
| `api_server.py` | FastAPI REST API |
| `pcap_ingest.py` | PCAP ingestion (tshark + Scapy) |
| `kb_builder.py` | Knowledge base builder |
| `app.py` | Hugging Face Spaces entrypoint |
| `deploy_spaces.py` | Automated HF Spaces deployment |
| `Dockerfile` | Docker image |
| `packages.txt` | HF Spaces apt packages |
| `knowledge_base/` | Starter reference documents |

## License

Apache License 2.0
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a GitHub release for AL-5G-AE")
    parser.add_argument("--tag", default="v1.0.0", help="Release tag (default: v1.0.0)")
    parser.add_argument("--title", default=None, help="Release title (default: AL-5G-AE <tag>)")
    parser.add_argument("--token", default=None, help="GitHub token (default: GITHUB_TOKEN env var)")
    parser.add_argument("--draft", action="store_true", help="Create as draft release")
    parser.add_argument("--prerelease", action="store_true", help="Mark as pre-release")
    parser.add_argument("--dry-run", action="store_true", help="Print changelog and exit")
    args = parser.parse_args()

    title = args.title or f"AL-5G-AE {args.tag}"

    if args.dry_run:
        print(f"Tag: {args.tag}")
        print(f"Title: {title}")
        print(f"Draft: {args.draft}")
        print()
        print(CHANGELOG_V1)
        return

    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("ERROR: Set GITHUB_TOKEN env var or pass --token")
        sys.exit(1)

    g = Github(token)
    repo = g.get_repo(REPO_NAME)

    # Create or get the tag (points to the current default branch HEAD)
    print(f"Creating release {args.tag} on {REPO_NAME}...")
    release = repo.create_git_release(
        tag=args.tag,
        name=title,
        message=CHANGELOG_V1,
        draft=args.draft,
        prerelease=args.prerelease,
    )
    print(f"Release created: {release.html_url}")
    print()
    print("To attach additional assets (e.g., a .tar.gz), use:")
    print(f"  release.upload_asset('al-5g-ae-{args.tag}.tar.gz')")


if __name__ == "__main__":
    main()
