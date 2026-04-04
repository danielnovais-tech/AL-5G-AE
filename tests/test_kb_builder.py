#!/usr/bin/env python3
# pyright: reportMissingModuleSource=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownParameterType=false
# pyright: reportUnknownArgumentType=false, reportReturnType=false
# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false
# pyright: reportCallIssue=false
"""
Unit tests for kb_builder.py.

Tests Markdown stripping, log slicing, and PDF extraction (mocked).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.conftest import create_synthetic_logs, create_temp_dir


# ===================================================================
# Markdown stripping
# ===================================================================

class TestStripMarkdown:
    def test_strips_headers(self) -> None:
        from kb_builder import strip_markdown
        assert strip_markdown("# Title\nBody") == "Title\nBody"

    def test_strips_bold_italic(self) -> None:
        from kb_builder import strip_markdown
        result = strip_markdown("**bold** and *italic*")
        assert "bold" in result
        assert "italic" in result

    def test_strips_links(self) -> None:
        from kb_builder import strip_markdown
        result = strip_markdown("[click here](http://example.com)")
        assert "click here" in result

    def test_strips_code_fences(self) -> None:
        from kb_builder import strip_markdown
        result = strip_markdown("```python\nprint('hi')\n```")
        assert "print" in result
        assert "```" not in result

    def test_empty_string(self) -> None:
        from kb_builder import strip_markdown
        assert strip_markdown("") == ""


# ===================================================================
# File processing
# ===================================================================

class TestProcessFile:
    def test_process_markdown(self) -> None:
        from kb_builder import process_file
        tmp = create_temp_dir()
        try:
            src = os.path.join(tmp, "test.md")
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir, exist_ok=True)
            Path(src).write_text("# Title\n\nSome content about 5G.", encoding="utf-8")
            process_file(src, out_dir)
            out_files = list(Path(out_dir).iterdir())
            assert len(out_files) == 1
            content = out_files[0].read_text(encoding="utf-8")
            assert "Title" in content
            assert "Some content" in content
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_process_plain_text(self) -> None:
        from kb_builder import process_file
        tmp = create_temp_dir()
        try:
            src = os.path.join(tmp, "test.txt")
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir, exist_ok=True)
            Path(src).write_text("Plain text about PFCP.", encoding="utf-8")
            process_file(src, out_dir)
            out_files = list(Path(out_dir).iterdir())
            assert len(out_files) == 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_process_log_file(self) -> None:
        from kb_builder import process_file
        tmp = create_temp_dir()
        try:
            src = create_synthetic_logs(os.path.join(tmp, "app.log"), num_lines=30)
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir, exist_ok=True)
            process_file(src, out_dir)
            out_files = list(Path(out_dir).iterdir())
            assert len(out_files) >= 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================
# Log slicing
# ===================================================================

class TestLogSlicing:
    def test_log_lines_limit(self) -> None:
        from kb_builder import process_file
        tmp = create_temp_dir()
        try:
            src = create_synthetic_logs(os.path.join(tmp, "big.log"), num_lines=100)
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir, exist_ok=True)
            process_file(src, out_dir, log_lines=10)
            out_files = list(Path(out_dir).iterdir())
            assert len(out_files) >= 1
            content = out_files[0].read_text(encoding="utf-8")
            # Should have at most ~10 lines (plus possible header)
            assert content.count("\n") <= 15
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================
# PDF extraction (mocked)
# ===================================================================

class TestPDFExtraction:
    def test_extract_pdf_text_when_available(self) -> None:
        try:
            from kb_builder import extract_pdf_text, _ensure_pymupdf
            if not _ensure_pymupdf():
                pytest.skip("PyMuPDF not installed")
        except ImportError:
            pytest.skip("PyMuPDF not installed")

        # We can't easily create a real PDF in a unit test,
        # so we just verify the function exists and handles missing files
        with pytest.raises(Exception):
            extract_pdf_text("/nonexistent/file.pdf")  # type: ignore[possibly-undefined]


# ===================================================================
# Full pipeline
# ===================================================================

class TestFullKBBuild:
    def test_end_to_end(self) -> None:
        from kb_builder import process_file
        tmp = create_temp_dir()
        try:
            in_dir = os.path.join(tmp, "input")
            out_dir = os.path.join(tmp, "output")
            os.makedirs(in_dir)
            os.makedirs(out_dir)

            # Create mixed input files
            Path(os.path.join(in_dir, "spec.md")).write_text(
                "# 3GPP TS 23.501\n\nAMF handles registration.\n",
                encoding="utf-8",
            )
            create_synthetic_logs(os.path.join(in_dir, "amf.log"), num_lines=20)
            Path(os.path.join(in_dir, "notes.txt")).write_text(
                "UPF forwarding rules need updating.\n",
                encoding="utf-8",
            )

            for f in Path(in_dir).iterdir():
                process_file(str(f), out_dir)

            out_files = list(Path(out_dir).iterdir())
            assert len(out_files) == 3
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
