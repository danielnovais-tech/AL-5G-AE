#!/usr/bin/env python3
# pyright: reportMissingModuleSource=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownParameterType=false
# pyright: reportUnknownArgumentType=false, reportReturnType=false
# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false
# pyright: reportUnusedImport=false, reportAttributeAccessIssue=false
"""
Unit tests for collaboration.py.

Tests conversation thread model, export, commenting, tagging, and suggestions.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.conftest import create_temp_dir, create_alertmanager_payload
from collaboration import (
    Message,
    Comment,
    ConversationThread,
    ThreadStore,
    export_markdown,
    QuerySuggester,
)


# ===================================================================
# Message model
# ===================================================================

class TestMessage:
    def test_auto_timestamp(self) -> None:
        msg = Message(role="user", content="Hello")
        assert msg.timestamp != ""
        assert msg.message_id.startswith("msg_")

    def test_custom_fields(self) -> None:
        msg = Message(role="assistant", content="Reply", timestamp="2026-01-01T00:00:00Z", message_id="m1")
        assert msg.timestamp == "2026-01-01T00:00:00Z"
        assert msg.message_id == "m1"


# ===================================================================
# Comment model
# ===================================================================

class TestComment:
    def test_auto_fields(self) -> None:
        c = Comment(author="alice", text="Good answer")
        assert c.timestamp != ""
        assert c.comment_id.startswith("cmt_")

    def test_rating(self) -> None:
        c = Comment(author="bob", text="Helpful", rating=5)
        assert c.rating == 5


# ===================================================================
# ConversationThread
# ===================================================================

class TestConversationThread:
    def test_add_message(self) -> None:
        t = ConversationThread(title="Test")
        t.messages.append(Message(role="user", content="Q1"))
        t.messages.append(Message(role="assistant", content="A1"))
        assert len(t.messages) == 2

    def test_tags(self) -> None:
        t = ConversationThread(title="Test", tags=["pfcp", "troubleshooting"])
        assert "pfcp" in t.tags


# ===================================================================
# ThreadStore
# ===================================================================

class TestThreadStore:
    def test_crud(self) -> None:
        tmp = create_temp_dir()
        try:
            store = ThreadStore(store_dir=tmp)
            thread = store.create_thread(title="Test Thread")
            assert thread.thread_id != ""

            # Add message
            store.add_message(thread.thread_id, "user", "What is PFCP?")
            store.add_message(thread.thread_id, "assistant", "PFCP is...")

            # Retrieve
            loaded = store.get_thread(thread.thread_id)
            assert loaded is not None
            assert len(loaded.messages) == 2

            # List
            all_threads = store.list_threads()
            assert len(all_threads) == 1

            # Delete
            store.delete_thread(thread.thread_id)
            assert store.get_thread(thread.thread_id) is None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_path_traversal_blocked(self) -> None:
        tmp = create_temp_dir()
        try:
            store = ThreadStore(store_dir=tmp)
            # A malicious thread_id with path traversal should be sanitized
            result = store.get_thread("../../../etc/passwd")
            assert result is None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_add_comment(self) -> None:
        tmp = create_temp_dir()
        try:
            store = ThreadStore(store_dir=tmp)
            thread = store.create_thread(title="Comment Test")
            store.add_message(thread.thread_id, "user", "Q")
            loaded = store.get_thread(thread.thread_id)
            assert loaded is not None
            msg_id = loaded.messages[0].message_id
            store.add_comment(thread.thread_id, msg_id, "reviewer", "Nice question!", rating=4)

            loaded2 = store.get_thread(thread.thread_id)
            assert loaded2 is not None
            assert len(loaded2.messages[0].comments) == 1
            assert loaded2.messages[0].comments[0].rating == 4
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_add_tag(self) -> None:
        tmp = create_temp_dir()
        try:
            store = ThreadStore(store_dir=tmp)
            thread = store.create_thread(title="Tag Test")
            store.add_tag(thread.thread_id, "pfcp")
            loaded = store.get_thread(thread.thread_id)
            assert loaded is not None
            assert "pfcp" in loaded.tags
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================
# Markdown export
# ===================================================================

class TestExportMarkdown:
    def test_basic_export(self) -> None:
        t = ConversationThread(title="Export Test")
        t.messages.append(Message(role="user", content="What is 5G?"))
        t.messages.append(Message(role="assistant", content="5G is the fifth generation."))
        md = export_markdown(t)
        assert "Export Test" in md
        assert "What is 5G?" in md
        assert "5G is the fifth generation" in md

    def test_export_with_comments(self) -> None:
        t = ConversationThread(title="Commented")
        msg = Message(role="assistant", content="Answer here.")
        msg.comments.append(Comment(author="alice", text="Good", rating=5))
        t.messages.append(msg)
        md = export_markdown(t)
        assert "alice" in md
        assert "Good" in md

    def test_export_file(self) -> None:
        from collaboration import export_markdown_file
        tmp = create_temp_dir()
        try:
            t = ConversationThread(title="File Export")
            t.messages.append(Message(role="user", content="Test"))
            path = os.path.join(tmp, "export.md")
            export_markdown_file(t, path)
            assert Path(path).exists()
            content = Path(path).read_text(encoding="utf-8")
            assert "File Export" in content
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================
# PDF export
# ===================================================================

class TestExportPDF:
    def test_pdf_export(self) -> None:
        try:
            from collaboration import export_pdf, _ensure_fpdf
            if not _ensure_fpdf():
                pytest.skip("fpdf2 not installed")
        except ImportError:
            pytest.skip("fpdf2 not installed")

        tmp = create_temp_dir()
        try:
            t = ConversationThread(title="PDF Test")
            t.messages.append(Message(role="user", content="Hello"))
            t.messages.append(Message(role="assistant", content="World"))
            path = os.path.join(tmp, "export.pdf")
            export_pdf(t, path)  # type: ignore[possibly-undefined]
            assert Path(path).exists()
            assert Path(path).stat().st_size > 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================
# Query suggester
# ===================================================================

class TestQuerySuggester:
    def test_common_suggestions(self) -> None:
        qs = QuerySuggester()
        suggestions = qs.suggest(5)
        assert len(suggestions) == 5
        # Should include some common 5G questions
        combined = " ".join(suggestions).lower()
        assert "5g" in combined or "pfcp" in combined or "registration" in combined

    def test_record_alert(self) -> None:
        qs = QuerySuggester()
        payload = create_alertmanager_payload()
        qs.record_alerts(payload["alerts"])
        suggestions = qs.suggest(3)
        # The alert-derived question should appear first
        assert any("PFCP" in s or "pfcp" in s.lower() for s in suggestions)

    def test_record_query(self) -> None:
        qs = QuerySuggester()
        for _ in range(5):
            qs.record_query("How to debug PFCP association?")
        for _ in range(2):
            qs.record_query("What is AMF?")
        suggestions = qs.suggest(5)
        assert any("PFCP" in s for s in suggestions)

    def test_save_load(self) -> None:
        tmp = create_temp_dir()
        try:
            path = os.path.join(tmp, "suggestions.json")
            qs = QuerySuggester()
            qs.record_query("Test query")
            qs.save(path)

            qs2 = QuerySuggester()
            qs2.load(path)
            assert "Test query" in qs2._query_counts
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
