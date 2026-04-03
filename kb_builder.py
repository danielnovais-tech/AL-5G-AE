#!/usr/bin/env python3
"""Knowledge Base Builder for AL-5G-AE.

Converts Markdown docs and/or copies log files into a plain-text directory suitable
for RAG indexing (use with `--rag-dir`).

Design goals:
- Standard library only (no extra dependencies)
- Preserve folder structure from input directory
- Produce `.txt` outputs

Examples:
  python kb_builder.py --input-dir ./docs --output-dir ./knowledge_base --extensions .md .log --clear
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable, Sequence


_FENCE_LINE_RE = re.compile(r"^\s*(```+|~~~+)\s*.*$")

# Timestamp at start of line (best-effort):
# - 2026-04-02T10:00:00
# - 2026-04-02 10:00:00
# - with optional .sss and optional timezone (Z or +00:00)
_TS_PREFIX_RE = re.compile(
    r"^\s*(?P<ts>\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?)"
)


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
        default=[".md", ".log"],
        help="File extensions to process (default: .md .log)",
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

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)

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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
