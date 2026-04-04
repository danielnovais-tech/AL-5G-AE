#!/usr/bin/env python3
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownParameterType=false
# pyright: reportUnknownArgumentType=false, reportReturnType=false
# pyright: reportUnusedFunction=false, reportMissingModuleSource=false
"""
Collaboration & Knowledge Sharing module for AL-5G-AE.

Features:
- Export conversation threads to Markdown or PDF.
- Commenting, tagging, and feedback on answers.
- Suggested queries based on recent alerts and common issues.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("collaboration")

# ---------------------------------------------------------------------------
# Lazy PDF generation (fpdf2)
# ---------------------------------------------------------------------------
_fpdf: Any = None


def _ensure_fpdf() -> bool:
    global _fpdf
    if _fpdf is not None:
        return True
    try:
        import fpdf as _mod  # noqa: F811
        _fpdf = _mod
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# 1. Conversation thread model
# ---------------------------------------------------------------------------

@dataclass
class Message:
    """A single message in a conversation thread."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: str = ""
    message_id: str = ""
    comments: List["Comment"] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        if not self.message_id:
            self.message_id = f"msg_{int(time.time() * 1000)}"


@dataclass
class Comment:
    """Feedback comment attached to a message."""
    author: str
    text: str
    rating: Optional[int] = None  # 1-5 or None
    timestamp: str = ""
    comment_id: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        if not self.comment_id:
            self.comment_id = f"cmt_{int(time.time() * 1000)}"


@dataclass
class ConversationThread:
    """A full conversation with metadata."""
    thread_id: str = ""
    title: str = ""
    messages: List[Message] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.thread_id:
            self.thread_id = f"thread_{int(time.time() * 1000)}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def add_message(self, role: str, content: str, tags: Optional[List[str]] = None) -> Message:
        msg = Message(role=role, content=content, tags=tags or [])
        self.messages.append(msg)
        return msg

    def add_comment(
        self,
        message_id: str,
        author: str,
        text: str,
        rating: Optional[int] = None,
    ) -> Optional[Comment]:
        for msg in self.messages:
            if msg.message_id == message_id:
                comment = Comment(author=author, text=text, rating=rating)
                msg.comments.append(comment)
                return comment
        return None

    def add_tag(self, tag: str) -> None:
        tag = tag.strip().lower()
        if tag and tag not in self.tags:
            self.tags.append(tag)

    def tag_message(self, message_id: str, tag: str) -> bool:
        tag = tag.strip().lower()
        for msg in self.messages:
            if msg.message_id == message_id:
                if tag and tag not in msg.tags:
                    msg.tags.append(tag)
                return True
        return False


# ---------------------------------------------------------------------------
# 2. Export — Markdown
# ---------------------------------------------------------------------------

def export_markdown(thread: ConversationThread) -> str:
    """Export a conversation thread as a Markdown string."""
    lines: List[str] = []
    title = thread.title or f"Conversation {thread.thread_id}"
    lines.append(f"# {title}\n")
    lines.append(f"**Thread ID:** {thread.thread_id}  ")
    lines.append(f"**Created:** {thread.created_at}  ")
    if thread.tags:
        lines.append(f"**Tags:** {', '.join(thread.tags)}  ")
    lines.append("")

    for _, msg in enumerate(thread.messages, 1):
        role_label = "**User**" if msg.role == "user" else "**AL-5G-AE**"
        lines.append(f"### {role_label} ({msg.timestamp})")
        if msg.tags:
            lines.append(f"*Tags: {', '.join(msg.tags)}*")
        lines.append("")
        lines.append(msg.content)
        lines.append("")

        if msg.comments:
            lines.append("#### Comments")
            for c in msg.comments:
                rating_str = f" (rating: {c.rating}/5)" if c.rating is not None else ""
                lines.append(f"- **{c.author}**{rating_str} — {c.text}")
            lines.append("")

    return "\n".join(lines)


def export_markdown_file(thread: ConversationThread, output_path: str) -> str:
    """Export a conversation thread to a Markdown file."""
    md = export_markdown(thread)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(md, encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# 3. Export — PDF (via fpdf2)
# ---------------------------------------------------------------------------

def export_pdf(thread: ConversationThread, output_path: str) -> str:
    """Export a conversation thread to a PDF file.

    Requires ``fpdf2`` (``pip install fpdf2``).
    """
    if not _ensure_fpdf():
        raise ImportError("fpdf2 is required for PDF export: pip install fpdf2")

    FPDF = _fpdf.FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Title
    pdf.set_font("Helvetica", "B", 16)
    title = thread.title or f"Conversation {thread.thread_id}"
    pdf.cell(0, 10, _sanitise_text(title), new_x="LMARGIN", new_y="NEXT")

    # Metadata
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, f"Thread ID: {thread.thread_id}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Created: {thread.created_at}", new_x="LMARGIN", new_y="NEXT")
    if thread.tags:
        pdf.cell(0, 6, f"Tags: {', '.join(thread.tags)}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    for msg in thread.messages:
        # Role header
        role_label = "User" if msg.role == "user" else "AL-5G-AE"
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, f"{role_label}  ({msg.timestamp})", new_x="LMARGIN", new_y="NEXT")

        if msg.tags:
            pdf.set_font("Helvetica", "I", 8)
            pdf.cell(0, 5, f"Tags: {', '.join(msg.tags)}", new_x="LMARGIN", new_y="NEXT")

        # Body
        pdf.set_font("Courier", "", 9)
        for line in _sanitise_text(msg.content).split("\n"):
            pdf.multi_cell(0, 5, line)
        pdf.ln(2)

        # Comments
        if msg.comments:
            pdf.set_font("Helvetica", "I", 8)
            for c in msg.comments:
                rating_str = f" (rating: {c.rating}/5)" if c.rating is not None else ""
                pdf.multi_cell(
                    0, 4,
                    _sanitise_text(f"  [{c.author}]{rating_str}: {c.text}"),
                )
            pdf.ln(2)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    pdf.output(output_path)
    return output_path


def _sanitise_text(text: str) -> str:
    """Remove characters that fpdf2 cannot encode in latin-1."""
    return text.encode("latin-1", errors="replace").decode("latin-1")


# ---------------------------------------------------------------------------
# 4. Persistence (JSON)
# ---------------------------------------------------------------------------

class ThreadStore:
    """Simple JSON-backed store for conversation threads and feedback."""

    def __init__(self, store_dir: str = "./threads") -> None:
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, thread_id: str) -> Path:
        # Prevent path traversal
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", thread_id)
        return self.store_dir / f"{safe_id}.json"

    def save(self, thread: ConversationThread) -> str:
        path = self._path(thread.thread_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(thread), f, indent=2, ensure_ascii=False)
        return str(path)

    def load(self, thread_id: str) -> Optional[ConversationThread]:
        path = self._path(thread_id)
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return _dict_to_thread(data)

    def list_threads(self) -> List[str]:
        return sorted(
            p.stem for p in self.store_dir.glob("*.json")
        )

    def delete(self, thread_id: str) -> bool:
        path = self._path(thread_id)
        if path.exists():
            path.unlink()
            return True
        return False


def _dict_to_thread(d: Dict[str, Any]) -> ConversationThread:
    """Reconstruct a ConversationThread from a dict (JSON deserialized)."""
    messages = []
    for md in d.get("messages", []):
        comments = [Comment(**cd) for cd in md.pop("comments", [])]
        msg = Message(**md)
        msg.comments = comments
        messages.append(msg)
    return ConversationThread(
        thread_id=d.get("thread_id", ""),
        title=d.get("title", ""),
        messages=messages,
        tags=d.get("tags", []),
        created_at=d.get("created_at", ""),
    )


# ---------------------------------------------------------------------------
# 5. Suggested queries
# ---------------------------------------------------------------------------

class QuerySuggester:
    """Generates query suggestions based on recent alerts and common issues.

    Tracks:
    - Recent Alertmanager alerts
    - Historical query frequency
    - Common 5G troubleshooting topics
    """

    # Pre-canned suggestions for cold start (no history)
    COMMON_5G_QUERIES: List[str] = [
        "What are common causes of PFCP session establishment failures?",
        "How do I troubleshoot N2 NGAP setup failures between gNB and AMF?",
        "What causes PDU session release and how to diagnose it?",
        "How to interpret GTP-U tunnel errors between UPF and gNB?",
        "What are typical NRF service registration failures?",
        "How do I diagnose HTTP/2 SBI communication errors between NFs?",
        "What causes UE authentication failures in AUSF/UDM?",
        "How to analyze SCTP association issues for NGAP?",
        "What are common UPF data path issues affecting user throughput?",
        "How to troubleshoot network slice selection failures in NSSF?",
    ]

    def __init__(self, max_history: int = 500) -> None:
        self._alert_history: List[Dict[str, str]] = []
        self._query_counter: Counter[str] = Counter()
        self._max_history = max_history

    def record_query(self, query: str) -> None:
        """Record a user query for frequency analysis."""
        normalised = query.strip().lower()
        if normalised:
            self._query_counter[normalised] += 1

    def record_alert(self, alert: Dict[str, Any]) -> None:
        """Record an Alertmanager alert for suggestion generation."""
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        entry = {
            "alertname": str(labels.get("alertname", "unknown")),
            "severity": str(labels.get("severity", "unknown")),
            "instance": str(labels.get("instance", "unknown")),
            "summary": str(annotations.get("summary", "")),
            "description": str(annotations.get("description", "")),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._alert_history.append(entry)
        if len(self._alert_history) > self._max_history:
            self._alert_history = self._alert_history[-self._max_history:]

    def record_alerts_bulk(self, alerts: List[Dict[str, Any]]) -> None:
        """Record multiple Alertmanager alerts at once."""
        for alert in alerts:
            self.record_alert(alert)

    def suggest(self, n: int = 5) -> List[str]:
        """Return up to *n* suggested queries.

        Priority:
        1. Queries derived from recent alerts (most recent first).
        2. Frequently asked historical queries.
        3. Pre-canned common 5G troubleshooting questions.
        """
        suggestions: List[str] = []
        seen: set[str] = set()

        # -- 1. Recent alerts → questions --------------------------------
        for alert in reversed(self._alert_history):
            if len(suggestions) >= n:
                break
            q = self._alert_to_query(alert)
            key = q.lower()
            if key not in seen:
                seen.add(key)
                suggestions.append(q)

        # -- 2. Top historical queries ------------------------------------
        for query_text, _count in self._query_counter.most_common(n * 2):
            if len(suggestions) >= n:
                break
            if query_text not in seen:
                seen.add(query_text)
                # Capitalise first letter for display
                suggestions.append(query_text[0].upper() + query_text[1:])

        # -- 3. Common 5G questions (cold start) --------------------------
        for q in self.COMMON_5G_QUERIES:
            if len(suggestions) >= n:
                break
            if q.lower() not in seen:
                seen.add(q.lower())
                suggestions.append(q)

        return suggestions[:n]

    @staticmethod
    def _alert_to_query(alert: Dict[str, str]) -> str:
        """Convert a recorded alert into a natural-language question."""
        name = alert.get("alertname", "unknown")
        instance = alert.get("instance", "")
        summary = alert.get("summary", "")
        description = alert.get("description", "")

        detail = summary or description
        instance_str = f" on {instance}" if instance and instance != "unknown" else ""
        if detail:
            return (
                f"Alert '{name}'{instance_str}: {detail}. "
                "What could be the root cause and how do I fix it?"
            )
        return (
            f"Alert '{name}'{instance_str} fired. "
            "What are the likely causes and recommended remediation?"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialise state for persistence."""
        return {
            "alert_history": self._alert_history,
            "query_counts": dict(self._query_counter.most_common()),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QuerySuggester":
        """Restore from serialised state."""
        qs = cls()
        qs._alert_history = data.get("alert_history", [])
        qs._query_counter = Counter(data.get("query_counts", {}))
        return qs

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> "QuerySuggester":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


# ---------------------------------------------------------------------------
# 6. FastAPI integration helpers (for api_server.py)
# ---------------------------------------------------------------------------

def register_collaboration_routes(app: Any) -> None:  # noqa: C901
    """Attach collaboration endpoints to an existing FastAPI app.

    Call this from api_server.py:
        from collaboration import register_collaboration_routes
        register_collaboration_routes(app)
    """
    from fastapi import HTTPException
    from fastapi.responses import Response as FastAPIResponse
    from pydantic import BaseModel as PydanticBaseModel

    store = ThreadStore(os.environ.get("THREAD_STORE_DIR", "./threads"))
    suggester = QuerySuggester()

    # Try to load persisted suggester state
    suggester_path = Path(store.store_dir) / "_suggester_state.json"
    if suggester_path.exists():
        try:
            suggester = QuerySuggester.load(str(suggester_path))
        except Exception:
            logger.warning("Failed to load suggester state, starting fresh")

    def _save_suggester() -> None:
        try:
            suggester.save(str(suggester_path))
        except Exception:
            logger.warning("Failed to persist suggester state")

    # -- Thread CRUD -------------------------------------------------------

    class CreateThreadReq(PydanticBaseModel):
        title: str = ""
        tags: List[str] = []

    class AddMessageReq(PydanticBaseModel):
        role: str
        content: str
        tags: List[str] = []

    class AddCommentReq(PydanticBaseModel):
        author: str
        text: str
        rating: Optional[int] = None

    class AddTagReq(PydanticBaseModel):
        tag: str

    @app.post("/threads")
    def create_thread(req: CreateThreadReq) -> Dict[str, str]:
        thread = ConversationThread(title=req.title, tags=req.tags)
        store.save(thread)
        return {"thread_id": thread.thread_id}

    @app.get("/threads")
    def list_threads() -> Dict[str, List[str]]:
        return {"threads": store.list_threads()}

    @app.get("/threads/{thread_id}")
    def get_thread(thread_id: str) -> Dict[str, Any]:
        thread = store.load(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        return asdict(thread)

    @app.delete("/threads/{thread_id}")
    def delete_thread(thread_id: str) -> Dict[str, str]:
        if not store.delete(thread_id):
            raise HTTPException(status_code=404, detail="Thread not found")
        return {"status": "deleted"}

    @app.post("/threads/{thread_id}/messages")
    def add_message(thread_id: str, req: AddMessageReq) -> Dict[str, str]:
        thread = store.load(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        msg = thread.add_message(req.role, req.content, req.tags)
        store.save(thread)
        # Track for suggestions
        if req.role == "user":
            suggester.record_query(req.content)
            _save_suggester()
        return {"message_id": msg.message_id}

    @app.post("/threads/{thread_id}/messages/{message_id}/comments")
    def add_comment(thread_id: str, message_id: str, req: AddCommentReq) -> Dict[str, Any]:
        thread = store.load(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        if req.rating is not None and not (1 <= req.rating <= 5):
            raise HTTPException(status_code=400, detail="Rating must be 1–5")
        comment = thread.add_comment(message_id, req.author, req.text, req.rating)
        if not comment:
            raise HTTPException(status_code=404, detail="Message not found")
        store.save(thread)
        return {"comment_id": comment.comment_id}

    @app.post("/threads/{thread_id}/messages/{message_id}/tags")
    def tag_message(thread_id: str, message_id: str, req: AddTagReq) -> Dict[str, str]:
        thread = store.load(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        if not thread.tag_message(message_id, req.tag):
            raise HTTPException(status_code=404, detail="Message not found")
        store.save(thread)
        return {"status": "tagged"}

    @app.post("/threads/{thread_id}/tags")
    def tag_thread(thread_id: str, req: AddTagReq) -> Dict[str, str]:
        thread = store.load(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        thread.add_tag(req.tag)
        store.save(thread)
        return {"status": "tagged"}

    # -- Export ------------------------------------------------------------

    @app.get("/threads/{thread_id}/export/markdown")
    def export_thread_md(thread_id: str) -> FastAPIResponse:
        thread = store.load(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        md = export_markdown(thread)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", thread_id)
        return FastAPIResponse(
            content=md,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.md"'},
        )

    @app.get("/threads/{thread_id}/export/pdf")
    def export_thread_pdf(thread_id: str) -> FastAPIResponse:
        thread = store.load(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            export_pdf(thread, tmp_path)
            pdf_bytes = Path(tmp_path).read_bytes()
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", thread_id)
        return FastAPIResponse(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'},
        )

    # -- Suggested queries -------------------------------------------------

    @app.get("/suggestions")
    def get_suggestions(n: int = 5) -> Dict[str, List[str]]:
        return {"suggestions": suggester.suggest(min(n, 20))}

    @app.post("/suggestions/alert")
    async def record_alert_for_suggestions(request: Any) -> Dict[str, str]:
        from starlette.requests import Request
        req: Request = request
        data = await req.json()
        alerts = data.get("alerts", [data]) if isinstance(data, dict) else []
        suggester.record_alerts_bulk(alerts)
        _save_suggester()
        return {"status": "recorded", "alert_count": str(len(alerts))}
