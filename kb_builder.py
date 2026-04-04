#!/usr/bin/env python3
"""Knowledge Base Builder for AL-5G-AE.

Converts Markdown docs, PDFs, logs, and wiki pages into a plain-text directory
suitable for RAG indexing (use with `--rag-dir`).

Supported sources:
- Local files (.md, .log, .pdf, .txt)
- Confluence wiki spaces (--confluence-url)
- SharePoint document libraries (--sharepoint-site)
- Folder watcher for automatic re-indexing (--watch)

Examples:
  python kb_builder.py --input-dir ./docs --output-dir ./knowledge_base --extensions .md .log .pdf --clear
  python kb_builder.py --confluence-url https://wiki.example.com --confluence-space 5GCore --output-dir ./knowledge_base
  python kb_builder.py --sharepoint-site https://org.sharepoint.com/sites/5G --sharepoint-library "Shared Documents" --output-dir ./knowledge_base
  python kb_builder.py --watch --input-dir ./docs --output-dir ./knowledge_base
"""

# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownParameterType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportMissingTypeStubs=false
# pyright: reportUntypedBaseClass=false

from __future__ import annotations

import argparse
import datetime as _dt
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Sequence

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kb_builder")

_FENCE_LINE_RE = re.compile(r"^\s*(```+|~~~+)\s*.*$")

# Timestamp at start of line (best-effort):
# - 2026-04-02T10:00:00
# - 2026-04-02 10:00:00
# - with optional .sss and optional timezone (Z or +00:00)
_TS_PREFIX_RE = re.compile(
    r"^\s*(?P<ts>\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?)"
)


# ---------------------------------------------------------------------------
# Lazy-loaded optional dependencies
# ---------------------------------------------------------------------------

def _ensure_pymupdf() -> bool:
    """Lazy-load PyMuPDF (fitz) for PDF extraction."""
    try:
        import fitz  # pyright: ignore[reportMissingImports]
        _ = fitz
        return True
    except ImportError:
        return False


def _ensure_requests() -> bool:
    try:
        import requests  # pyright: ignore[reportMissingImports]
        _ = requests
        return True
    except ImportError:
        return False


def _ensure_msal() -> bool:
    try:
        import msal  # pyright: ignore[reportMissingImports]
        _ = msal
        return True
    except ImportError:
        return False


def _ensure_watchdog() -> bool:
    try:
        from watchdog.observers import Observer  # pyright: ignore[reportMissingImports]
        from watchdog.events import FileSystemEventHandler  # pyright: ignore[reportMissingImports]
        _ = Observer, FileSystemEventHandler
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def extract_pdf_text(pdf_path: str | Path) -> str:
    """Extract text from a PDF using PyMuPDF (fitz).

    Falls back to a simple error message if fitz is not installed.
    """
    if not _ensure_pymupdf():
        return f"[PDF extraction requires PyMuPDF: pip install PyMuPDF]\n{pdf_path}"
    import fitz  # pyright: ignore[reportMissingImports]
    doc = fitz.open(str(pdf_path))  # pyright: ignore[reportUnknownMemberType]
    pages: list[str] = []
    for page_num in range(len(doc)):
        page = doc[page_num]  # pyright: ignore[reportUnknownMemberType]
        text = page.get_text()  # pyright: ignore[reportUnknownMemberType]
        if text and text.strip():
            pages.append(f"--- Page {page_num + 1} ---\n{text.strip()}")
    doc.close()  # pyright: ignore[reportUnknownMemberType]
    return "\n\n".join(pages) if pages else f"[No extractable text in {pdf_path}]"


# ---------------------------------------------------------------------------
# Confluence crawler
# ---------------------------------------------------------------------------

def crawl_confluence(
    base_url: str,
    space_key: str,
    output_dir: Path,
    *,
    username: Optional[str] = None,
    api_token: Optional[str] = None,
    max_pages: int = 500,
    verbose: bool = False,
) -> int:
    """Crawl a Confluence space and save each page as a .txt file.

    Uses the Confluence REST API v2 (``/wiki/api/v2/spaces/{key}/pages``).
    Authentication is via HTTP Basic (username + API token).

    Returns the number of pages saved.
    """
    if not _ensure_requests():
        print("ERROR: 'requests' is required for Confluence crawling. pip install requests", file=sys.stderr)
        return 0
    import requests  # pyright: ignore[reportMissingImports]

    base_url = base_url.rstrip("/")
    auth = (username, api_token) if username and api_token else None
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Resolve space ID
    spaces_url = f"{base_url}/wiki/api/v2/spaces"
    resp = requests.get(spaces_url, params={"keys": space_key, "limit": 1}, auth=auth, timeout=30)
    resp.raise_for_status()
    spaces_data = resp.json()
    results = spaces_data.get("results", [])
    if not results:
        print(f"Confluence space '{space_key}' not found.", file=sys.stderr)
        return 0
    space_id = results[0]["id"]

    # Step 2: Paginate through all pages
    pages_url = f"{base_url}/wiki/api/v2/spaces/{space_id}/pages"
    saved = 0
    cursor: Optional[str] = None

    while saved < max_pages:
        params: Dict[str, Any] = {"limit": 50, "body-format": "storage"}
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(pages_url, params=params, auth=auth, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for page in data.get("results", []):
            title = page.get("title", "untitled")
            body_storage = page.get("body", {}).get("storage", {}).get("value", "")
            # Strip HTML tags from Confluence storage format
            text = re.sub(r"<[^>]+>", " ", body_storage)
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                continue

            safe_title = re.sub(r'[<>:"/\\|?*]', "_", title)[:100]
            out_path = output_dir / f"{safe_title}.txt"
            out_path.write_text(f"# {title}\n\n{text}", encoding="utf-8")
            saved += 1
            if verbose:
                logger.info(f"Confluence: saved {title} -> {out_path}")
            if saved >= max_pages:
                break

        # Next cursor
        next_link = data.get("_links", {}).get("next")
        if not next_link:
            break
        # Extract cursor param from next link
        import urllib.parse
        parsed = urllib.parse.urlparse(next_link)
        qs = urllib.parse.parse_qs(parsed.query)
        cursor = qs.get("cursor", [None])[0]
        if not cursor:
            break

    logger.info(f"Confluence: saved {saved} page(s) from space '{space_key}' to {output_dir}")
    return saved


# ---------------------------------------------------------------------------
# SharePoint crawler
# ---------------------------------------------------------------------------

def crawl_sharepoint(
    site_url: str,
    library_name: str,
    output_dir: Path,
    *,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    tenant_id: Optional[str] = None,
    max_files: int = 500,
    extensions: Sequence[str] = (".pdf", ".docx", ".txt", ".md", ".log"),
    verbose: bool = False,
) -> int:
    """Download files from a SharePoint document library.

    Uses Microsoft Graph API with client-credential flow (MSAL).
    Supports downloading and converting PDF files to text.

    Returns the number of files saved.
    """
    if not _ensure_requests():
        print("ERROR: 'requests' is required for SharePoint crawling. pip install requests", file=sys.stderr)
        return 0
    if not _ensure_msal():
        print("ERROR: 'msal' is required for SharePoint crawling. pip install msal", file=sys.stderr)
        return 0
    import msal  # pyright: ignore[reportMissingImports]
    import requests  # pyright: ignore[reportMissingImports]

    client_id = client_id or os.environ.get("SHAREPOINT_CLIENT_ID", "")
    client_secret = client_secret or os.environ.get("SHAREPOINT_CLIENT_SECRET", "")
    tenant_id = tenant_id or os.environ.get("SHAREPOINT_TENANT_ID", "")

    if not all([client_id, client_secret, tenant_id]):
        print("ERROR: SharePoint requires SHAREPOINT_CLIENT_ID, SHAREPOINT_CLIENT_SECRET, SHAREPOINT_TENANT_ID", file=sys.stderr)
        return 0

    # Acquire token via MSAL client-credential flow
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    app = msal.ConfidentialClientApplication(  # pyright: ignore[reportUnknownMemberType]
        client_id, authority=authority, client_credential=client_secret
    )
    token_result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])  # pyright: ignore[reportUnknownMemberType]
    if "access_token" not in token_result:
        print(f"SharePoint auth failed: {token_result.get('error_description', 'unknown error')}", file=sys.stderr)
        return 0
    access_token: str = token_result["access_token"]

    headers = {"Authorization": f"Bearer {access_token}"}
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve site ID from URL
    # site_url: https://org.sharepoint.com/sites/SiteName
    import urllib.parse
    parsed = urllib.parse.urlparse(site_url)
    hostname = parsed.hostname or ""
    site_path = parsed.path.rstrip("/")
    graph_site_url = f"https://graph.microsoft.com/v1.0/sites/{hostname}:{site_path}"

    resp = requests.get(graph_site_url, headers=headers, timeout=30)
    resp.raise_for_status()
    site_id = resp.json()["id"]

    # List drives (document libraries)
    drives_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives"
    resp = requests.get(drives_url, headers=headers, timeout=30)
    resp.raise_for_status()
    drives = resp.json().get("value", [])
    drive_id = None
    for d in drives:
        if d.get("name", "").lower() == library_name.lower():
            drive_id = d["id"]
            break
    if not drive_id:
        print(f"SharePoint library '{library_name}' not found. Available: {[d['name'] for d in drives]}", file=sys.stderr)
        return 0

    # Recursively list files
    saved = 0
    norm_exts = tuple(e.lower() for e in extensions)

    def _download_drive_items(folder_url: str) -> None:
        nonlocal saved
        if saved >= max_files:
            return
        resp = requests.get(folder_url, headers=headers, timeout=30)
        resp.raise_for_status()
        items = resp.json().get("value", [])
        for item in items:
            if saved >= max_files:
                return
            if "folder" in item:
                children_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item['id']}/children"
                _download_drive_items(children_url)
            elif "file" in item:
                name: str = item.get("name", "")
                if not name.lower().endswith(norm_exts):
                    continue
                download_url = item.get("@microsoft.graph.downloadUrl", "")
                if not download_url:
                    continue
                file_resp = requests.get(download_url, timeout=60)
                file_resp.raise_for_status()

                safe_name = re.sub(r'[<>:"/\\|?*]', "_", name)[:150]
                if name.lower().endswith(".pdf"):
                    # Save PDF temporarily, extract text
                    tmp_path = output_dir / f"_tmp_{safe_name}"
                    tmp_path.write_bytes(file_resp.content)
                    text = extract_pdf_text(tmp_path)
                    tmp_path.unlink(missing_ok=True)
                    out_path = output_dir / f"{Path(safe_name).stem}.txt"
                    out_path.write_text(text, encoding="utf-8")
                else:
                    out_path = output_dir / safe_name
                    try:
                        out_path.write_text(file_resp.content.decode("utf-8", errors="ignore"), encoding="utf-8")
                    except Exception:
                        out_path.write_bytes(file_resp.content)

                saved += 1
                if verbose:
                    logger.info(f"SharePoint: saved {name} -> {out_path}")

    root_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/children"
    _download_drive_items(root_url)

    logger.info(f"SharePoint: saved {saved} file(s) from '{library_name}' to {output_dir}")
    return saved


# ---------------------------------------------------------------------------
# Folder watcher (auto re-index on change)
# ---------------------------------------------------------------------------

class _KBEventHandler:
    """Watchdog event handler that re-processes changed files."""

    def __init__(
        self,
        input_dir: Path,
        output_dir: Path,
        extensions: Sequence[str],
        *,
        verbose: bool = False,
        log_regex: Optional[re.Pattern[str]] = None,
        log_lines: Optional[int] = None,
        since: Optional[_dt.datetime] = None,
        until: Optional[_dt.datetime] = None,
        multiline: bool = False,
        on_change: Optional[Callable[[Path], None]] = None,
    ) -> None:
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.extensions = tuple(e.lower() for e in extensions)
        self.verbose = verbose
        self.log_regex = log_regex
        self.log_lines = log_lines
        self.since = since
        self.until = until
        self.multiline = multiline
        self.on_change = on_change

    def _should_process(self, path: str) -> bool:
        return Path(path).suffix.lower() in self.extensions

    def _reprocess(self, src_path: str) -> None:
        fp = Path(src_path)
        if not fp.is_file():
            return
        rel = fp.relative_to(self.input_dir)
        out = (self.output_dir / rel).with_suffix(".txt")
        if process_file(
            fp, out,
            verbose=self.verbose,
            log_regex=self.log_regex,
            log_lines=self.log_lines,
            since=self.since,
            until=self.until,
            multiline=self.multiline,
        ):
            logger.info(f"Watcher: re-indexed {fp}")
            if self.on_change:
                self.on_change(out)

    def dispatch(self, event: Any) -> None:
        """Called by watchdog for any file system event."""
        if getattr(event, "is_directory", False):
            return
        src = getattr(event, "src_path", "")
        if not self._should_process(src):
            return
        event_type = getattr(event, "event_type", "")
        if event_type in ("created", "modified"):
            self._reprocess(src)
        elif event_type == "deleted":
            fp = Path(src)
            try:
                rel = fp.relative_to(self.input_dir)
                out = (self.output_dir / rel).with_suffix(".txt")
                if out.exists():
                    out.unlink()
                    logger.info(f"Watcher: removed {out}")
            except ValueError:
                pass


def watch_directory(
    input_dir: Path,
    output_dir: Path,
    extensions: Sequence[str],
    *,
    verbose: bool = False,
    log_regex: Optional[re.Pattern[str]] = None,
    log_lines: Optional[int] = None,
    since: Optional[_dt.datetime] = None,
    until: Optional[_dt.datetime] = None,
    multiline: bool = False,
    on_change: Optional[Callable[[Path], None]] = None,
    poll_interval: float = 2.0,
) -> None:
    """Watch input_dir for changes and re-process files into output_dir.

    Blocks until interrupted (Ctrl+C).  If watchdog is not installed, falls back
    to a simple polling loop that checks mtimes.
    """
    handler = _KBEventHandler(
        input_dir, output_dir, extensions,
        verbose=verbose, log_regex=log_regex, log_lines=log_lines,
        since=since, until=until, multiline=multiline, on_change=on_change,
    )

    if _ensure_watchdog():
        from watchdog.observers import Observer  # pyright: ignore[reportMissingImports]
        from watchdog.events import FileSystemEventHandler  # pyright: ignore[reportMissingImports]

        class _WatchdogAdapter(FileSystemEventHandler):  # pyright: ignore[reportUntypedBaseClass]
            def on_any_event(self, event: Any) -> None:  # pyright: ignore[reportUnknownParameterType]
                handler.dispatch(event)

        observer = Observer()  # pyright: ignore[reportUnknownVariableType]
        observer.schedule(_WatchdogAdapter(), str(input_dir), recursive=True)  # pyright: ignore[reportUnknownMemberType]
        observer.start()  # pyright: ignore[reportUnknownMemberType]
        logger.info(f"Watching {input_dir} (watchdog) — press Ctrl+C to stop")
        try:
            while True:
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            observer.stop()  # pyright: ignore[reportUnknownMemberType]
        observer.join()  # pyright: ignore[reportUnknownMemberType]
    else:
        # Polling fallback (no extra dependencies)
        logger.info(f"Watching {input_dir} (polling, interval={poll_interval}s) — press Ctrl+C to stop")
        logger.info("Install 'watchdog' for faster, event-driven watching")
        mtimes: Dict[Path, float] = {}
        exts = tuple(e.lower() for e in extensions)
        try:
            while True:
                for fp in input_dir.rglob("*"):
                    if not fp.is_file() or fp.suffix.lower() not in exts:
                        continue
                    mtime = fp.stat().st_mtime
                    if fp not in mtimes or mtimes[fp] < mtime:
                        mtimes[fp] = mtime
                        # Simulate a "modified" event
                        class _FakeEvent:
                            is_directory = False
                            src_path = str(fp)
                            event_type = "modified"
                        handler.dispatch(_FakeEvent())
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            pass
    logger.info("Watcher stopped.")


def strip_markdown(text: str) -> str:
    """Strip common Markdown syntax, keeping readable plain text.

    This is intentionally conservative: it removes most markup while retaining the
    underlying content (including code block contents).
    """

    # Normalize newlines
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Drop fenced code markers but keep code content.
    lines: list[str] = []
    for line in text.split("\n"):
        if _FENCE_LINE_RE.match(line):
            continue
        lines.append(line)
    text = "\n".join(lines)

    # Remove Markdown headings markers, keep heading text
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.MULTILINE)

    # Blockquotes: "> text" -> "text"
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)

    # Links/images
    # Images first: ![alt](url) -> alt
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1", text)
    # Links: [text](url) -> text
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)

    # Inline code: `code` -> code
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # Bold/italic markers (best-effort)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)

    # List markers: "- item" / "* item" / "1. item" -> "item"
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)

    # Horizontal rules
    text = re.sub(r"^\s*(---+|\*\*\*+|___+)\s*$", "", text, flags=re.MULTILINE)

    # Simple HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def _iter_files(input_dir: Path, extensions: Sequence[str]) -> Iterable[Path]:
    normalized = tuple(ext.lower() for ext in extensions)
    seen: set[Path] = set()

    for ext in normalized:
        for path in input_dir.rglob(f"*{ext}"):
            if path.is_file() and path not in seen:
                seen.add(path)
                yield path


def _slice_log_text(
    text: str,
    *,
    log_regex: re.Pattern[str] | None,
    tail_lines: int | None,
    since: _dt.datetime | None,
    until: _dt.datetime | None,
    multiline: bool,
) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    def _to_utc(dt: _dt.datetime) -> _dt.datetime:
        # Treat naive timestamps as UTC for consistent comparisons.
        if dt.tzinfo is None:
            return dt.replace(tzinfo=_dt.timezone.utc)
        return dt.astimezone(_dt.timezone.utc)

    def _parse_ts(ts_str: str) -> _dt.datetime | None:
        s = ts_str.strip()
        if not s:
            return None

        # Normalize common forms to ISO 8601 for fromisoformat.
        # - Space separator -> 'T'
        # - 'Z' -> '+00:00'
        if " " in s and "T" not in s:
            parts = s.split(" ")
            if len(parts) >= 2:
                s = parts[0] + "T" + parts[1]
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        # Handle timezone like +0000 -> +00:00
        if re.search(r"[+-]\d{4}$", s):
            s = s[:-5] + s[-5:-2] + ":" + s[-2:]

        try:
            return _dt.datetime.fromisoformat(s)
        except ValueError:
            return None

    def _extract_line_ts(line: str) -> _dt.datetime | None:
        match = _TS_PREFIX_RE.match(line)
        if match is None:
            return None
        return _parse_ts(match.group("ts"))

    since_utc = _to_utc(since) if since else None
    until_utc = _to_utc(until) if until else None

    def _in_window(ts: _dt.datetime | None) -> bool:
        if ts is None:
            return True
        ts_utc = _to_utc(ts)
        if since_utc and ts_utc < since_utc:
            return False
        if until_utc and ts_utc > until_utc:
            return False
        return True

    if multiline:
        entries: list[tuple[_dt.datetime | None, list[str]]] = []
        current_ts: _dt.datetime | None = None
        current_lines: list[str] = []

        for line in lines:
            line_ts = _extract_line_ts(line)
            if line_ts is not None:
                if current_lines:
                    entries.append((current_ts, current_lines))
                current_ts = line_ts
                current_lines = [line]
            else:
                # Stacktrace / continuation line.
                current_lines.append(line)

        if current_lines:
            entries.append((current_ts, current_lines))

        # Filter entries
        filtered_entries: list[str] = []
        for entry_ts, entry_lines in entries:
            if not _in_window(entry_ts):
                continue

            if log_regex is not None:
                if not any(log_regex.search(ln) is not None for ln in entry_lines):
                    continue

            filtered_entries.append("\n".join(entry_lines).rstrip())

        joined = "\n".join(filtered_entries)
        # Apply tail after filtering (line-based)
        if tail_lines is not None:
            tail_lines = max(0, tail_lines)
            joined_lines = joined.split("\n")
            joined = "\n".join(joined_lines[-tail_lines:])
        return joined.strip()

    # Line-by-line mode (legacy behavior)
    filtered_lines: list[str] = []
    for line in lines:
        if not _in_window(_extract_line_ts(line)):
            continue
        if log_regex is not None and log_regex.search(line) is None:
            continue
        filtered_lines.append(line)

    if tail_lines is not None:
        tail_lines = max(0, tail_lines)
        filtered_lines = filtered_lines[-tail_lines:]

    return "\n".join(filtered_lines).strip()


def process_file(
    input_path: Path,
    output_path: Path,
    *,
    verbose: bool,
    log_regex: re.Pattern[str] | None,
    log_lines: int | None,
    since: _dt.datetime | None,
    until: _dt.datetime | None,
    multiline: bool,
) -> bool:
    """Convert/copy a single file into `.txt` output."""

    # PDF files: extract text with PyMuPDF
    if input_path.suffix.lower() == ".pdf":
        content = extract_pdf_text(input_path)
        output_path = output_path.with_suffix(".txt")
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
            if verbose:
                print(f"Processed (PDF): {input_path} -> {output_path}")
            return True
        except Exception as exc:
            print(f"Error writing {output_path}: {exc}", file=sys.stderr)
            return False

    try:
        content = input_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:  # noqa: BLE001
        print(f"Error reading {input_path}: {exc}", file=sys.stderr)
        return False

    if input_path.suffix.lower() == ".md":
        content = strip_markdown(content)

    if input_path.suffix.lower() == ".log" and (log_regex is not None or log_lines is not None):
        content = _slice_log_text(
            content,
            log_regex=log_regex,
            tail_lines=log_lines,
            since=since,
            until=until,
            multiline=multiline,
        )
    elif input_path.suffix.lower() == ".log" and (since is not None or until is not None or multiline):
        content = _slice_log_text(
            content,
            log_regex=log_regex,
            tail_lines=log_lines,
            since=since,
            until=until,
            multiline=multiline,
        )

    output_path = output_path.with_suffix(".txt")
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        if verbose:
            print(f"Processed: {input_path} -> {output_path}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"Error writing {output_path}: {exc}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a .txt knowledge base for AL-5G-AE RAG")
    parser.add_argument(
        "--input-dir",
        default="./docs",
        help="Directory containing source files (default: ./docs)",
    )
    parser.add_argument(
        "--output-dir",
        default="./knowledge_base",
        help="Directory to place processed .txt files (default: ./knowledge_base)",
    )
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=[".md", ".log", ".pdf"],
        help="File extensions to process (default: .md .log .pdf)",
    )
    parser.add_argument(
        "--since",
        default=None,
        help=(
            "For .log files: keep entries/lines at or after this timestamp "
            "(ISO 8601 recommended, e.g. 2026-04-02T10:00:00 or 2026-04-02 10:00:00)"
        ),
    )
    parser.add_argument(
        "--until",
        default=None,
        help=(
            "For .log files: keep entries/lines at or before this timestamp "
            "(ISO 8601 recommended, e.g. 2026-04-02T12:00:00)"
        ),
    )
    parser.add_argument(
        "--log-multiline",
        action="store_true",
        help=(
            "For .log files: group multiline entries/stacktraces (timestamped line starts a new entry) "
            "before applying filters."
        ),
    )
    parser.add_argument(
        "--log-lines",
        type=int,
        default=None,
        help="For .log files: keep only the last N lines (default: keep all)",
    )
    parser.add_argument(
        "--log-regex",
        default=None,
        help="For .log files: keep only lines matching this regex (default: keep all)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear output directory before building",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print per-file processing details",
    )
    # Confluence
    parser.add_argument(
        "--confluence-url",
        default=None,
        help="Confluence base URL (e.g., https://wiki.example.com)",
    )
    parser.add_argument(
        "--confluence-space",
        default=None,
        help="Confluence space key to crawl",
    )
    parser.add_argument(
        "--confluence-user",
        default=os.environ.get("CONFLUENCE_USER", ""),
        help="Confluence username (or set CONFLUENCE_USER env var)",
    )
    parser.add_argument(
        "--confluence-token",
        default=os.environ.get("CONFLUENCE_TOKEN", ""),
        help="Confluence API token (or set CONFLUENCE_TOKEN env var)",
    )
    parser.add_argument(
        "--confluence-max-pages",
        type=int,
        default=500,
        help="Max Confluence pages to crawl (default: 500)",
    )
    # SharePoint
    parser.add_argument(
        "--sharepoint-site",
        default=None,
        help="SharePoint site URL (e.g., https://org.sharepoint.com/sites/5G)",
    )
    parser.add_argument(
        "--sharepoint-library",
        default="Shared Documents",
        help='SharePoint document library name (default: "Shared Documents")',
    )
    parser.add_argument(
        "--sharepoint-max-files",
        type=int,
        default=500,
        help="Max SharePoint files to download (default: 500)",
    )
    # Watch mode
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch input directory for changes and re-index automatically",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds for --watch mode (default: 2.0)",
    )
    args = parser.parse_args()

    compiled_log_regex: re.Pattern[str] | None = None
    if args.log_regex:
        try:
            compiled_log_regex = re.compile(args.log_regex)
        except re.error as exc:
            print(f"Invalid --log-regex pattern: {exc}", file=sys.stderr)
            return 1

    def _parse_bound(value: str | None, flag: str) -> _dt.datetime | None:
        if not value:
            return None
        s = value.strip()
        if not s:
            return None
        # Accept a space separator too.
        if " " in s and "T" not in s:
            parts = s.split(" ")
            if len(parts) >= 2:
                s = parts[0] + "T" + parts[1]
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        if re.search(r"[+-]\d{4}$", s):
            s = s[:-5] + s[-5:-2] + ":" + s[-2:]
        try:
            return _dt.datetime.fromisoformat(s)
        except ValueError as exc:
            print(f"Invalid {flag} timestamp: {exc}", file=sys.stderr)
            raise

    try:
        since_dt = _parse_bound(args.since, "--since")
        until_dt = _parse_bound(args.until, "--until")
    except ValueError:
        return 1

    output_dir = Path(args.output_dir)

    # ---- Confluence crawl mode ----
    if args.confluence_url and args.confluence_space:
        if args.clear and output_dir.exists():
            shutil.rmtree(output_dir)
        saved = crawl_confluence(
            base_url=args.confluence_url,
            space_key=args.confluence_space,
            output_dir=output_dir,
            username=args.confluence_user,
            api_token=args.confluence_token,
            max_pages=args.confluence_max_pages,
            verbose=args.verbose,
        )
        print(f"Confluence: {saved} page(s) saved to {output_dir}.")
        if saved:
            print(f"Next: python al_5g_ae.py --rag-dir {output_dir}")
        return 0

    # ---- SharePoint crawl mode ----
    if args.sharepoint_site:
        if args.clear and output_dir.exists():
            shutil.rmtree(output_dir)
        saved = crawl_sharepoint(
            site_url=args.sharepoint_site,
            library_name=args.sharepoint_library,
            output_dir=output_dir,
            max_files=args.sharepoint_max_files,
            extensions=args.extensions,
            verbose=args.verbose,
        )
        print(f"SharePoint: {saved} file(s) saved to {output_dir}.")
        if saved:
            print(f"Next: python al_5g_ae.py --rag-dir {output_dir}")
        return 0

    # ---- Local file processing ----
    input_dir = Path(args.input_dir)

    if not input_dir.is_dir():
        print(f"Input directory not found: {input_dir}", file=sys.stderr)
        return 1

    if args.clear and output_dir.exists():
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    files = list(_iter_files(input_dir, args.extensions))
    if not files:
        exts = ", ".join(args.extensions)
        print(f"No files found with extensions [{exts}] in {input_dir}")
        return 0

    print(f"Found {len(files)} file(s) under {input_dir}.")

    processed = 0
    for file_path in files:
        rel_path = file_path.relative_to(input_dir)
        out_path = (output_dir / rel_path).with_suffix(".txt")
        if process_file(
            file_path,
            out_path,
            verbose=args.verbose,
            log_regex=compiled_log_regex,
            log_lines=args.log_lines,
            since=since_dt,
            until=until_dt,
            multiline=bool(args.log_multiline),
        ):
            processed += 1

    print(f"Processed {processed} file(s) into {output_dir}.")
    if processed:
        print(f"Next: python al_5g_ae.py --rag-dir {output_dir}")

    # ---- Watch mode ----
    if args.watch:
        print(f"Entering watch mode for {input_dir}...")
        watch_directory(
            input_dir, output_dir, args.extensions,
            verbose=args.verbose,
            log_regex=compiled_log_regex,
            log_lines=args.log_lines,
            since=since_dt,
            until=until_dt,
            multiline=bool(args.log_multiline),
            poll_interval=args.poll_interval,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
