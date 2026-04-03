#!/usr/bin/env python3
"""Deploy AL-5G-AE to Hugging Face Spaces.

Prerequisites:
    pip install huggingface_hub
    huggingface-cli login          # or set HF_TOKEN env var

Usage:
    python deploy_spaces.py                              # uses defaults
    python deploy_spaces.py --owner your-hf-username     # custom owner
    python deploy_spaces.py --space-name al-5g-ae        # custom Space name
    python deploy_spaces.py --private                    # create a private Space
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict

try:
    from huggingface_hub import HfApi, create_repo
except ImportError:
    print("ERROR: Install huggingface_hub first:  pip install huggingface_hub")
    sys.exit(1)


# Files to upload (exclude dev/local artifacts)
EXCLUDE_PATTERNS = {
    ".venv", "__pycache__", ".git", "logs", "*.pyc",
    "deploy_spaces.py", ".dockerignore", "Dockerfile",
}

SPACE_FILES = [
    "app.py",
    "al_5g_ae.py",
    "al_5g_ae_core.py",
    "api_server.py",
    "kb_builder.py",
    "pcap_ingest.py",
    "web_ui.py",
    "requirements.txt",
    "packages.txt",
    "LICENSE",
    "README.md",
]

KNOWLEDGE_BASE_DIR = "knowledge_base"


def should_upload(p: Path) -> bool:
    """Check whether a path should be included in the Space upload."""
    name = p.name
    for pattern in EXCLUDE_PATTERNS:
        if pattern.startswith("*"):
            if name.endswith(pattern[1:]):
                return False
        elif name == pattern:
            return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy AL-5G-AE to Hugging Face Spaces")
    parser.add_argument("--owner", default=None,
                        help="HF username or org (default: your logged-in user)")
    parser.add_argument("--space-name", default="AL-5G-AE",
                        help="Name for the Space (default: AL-5G-AE)")
    parser.add_argument("--private", action="store_true",
                        help="Create a private Space")
    parser.add_argument("--hardware", default="cpu-basic",
                        choices=["cpu-basic", "cpu-upgrade", "t4-small", "t4-medium"],
                        help="Hardware tier (default: cpu-basic, free)")
    parser.add_argument("--token", default=None,
                        help="HF token (default: from huggingface-cli login or HF_TOKEN env)")
    args = parser.parse_args()

    token = args.token or os.environ.get("HF_TOKEN")
    api = HfApi(token=token)

    # Resolve owner
    owner = args.owner
    if not owner:
        user_info: Dict[str, Any] = api.whoami()  # type: ignore[assignment]
        owner = str(user_info["name"])
        print(f"Using HF account: {owner}")

    repo_id = f"{owner}/{args.space_name}"
    print(f"Target Space: https://huggingface.co/spaces/{repo_id}")

    # Step 1: Create the Space repo (idempotent, CPU-basic by default)
    print(f"Creating Space repository (Gradio SDK, hardware={args.hardware})...")
    create_repo(
        repo_id=repo_id,
        repo_type="space",
        space_sdk="gradio",
        space_hardware=args.hardware,
        private=args.private,
        exist_ok=True,
        token=token,
    )
    print(f"Space created/exists: {repo_id} (hardware: {args.hardware})")

    # Step 1b: Ensure hardware is set (even for existing Spaces)
    try:
        api.request_space_hardware(repo_id=repo_id, hardware=args.hardware, token=token)
        print(f"Hardware confirmed: {args.hardware}")
    except Exception as e:
        print(f"Note: Could not set hardware (may already be set): {e}")

    # Step 2: Upload files
    root = Path(__file__).parent.resolve()
    print("Uploading files...")

    # Upload individual top-level files
    for fname in SPACE_FILES:
        fpath = root / fname
        if fpath.exists():
            print(f"  {fname}")
            api.upload_file(
                path_or_fileobj=str(fpath),
                path_in_repo=fname,
                repo_id=repo_id,
                repo_type="space",
                token=token,
            )

    # Upload knowledge_base/ directory
    kb_dir = root / KNOWLEDGE_BASE_DIR
    if kb_dir.is_dir():
        for fpath in sorted(kb_dir.rglob("*")):
            if fpath.is_file() and should_upload(fpath):
                rel = fpath.relative_to(root)
                print(f"  {rel}")
                api.upload_file(
                    path_or_fileobj=str(fpath),
                    path_in_repo=str(rel).replace("\\", "/"),
                    repo_id=repo_id,
                    repo_type="space",
                    token=token,
                )

    print()
    print("=" * 60)
    print("Deployment complete!")
    print(f"Space URL: https://huggingface.co/spaces/{repo_id}")
    print("It may take a few minutes to build. Check the Logs tab for progress.")
    print("=" * 60)


if __name__ == "__main__":
    main()
