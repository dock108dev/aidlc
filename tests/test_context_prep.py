"""Tests for aidlc.context_prep."""

from pathlib import Path
from unittest.mock import MagicMock

from aidlc import context_prep


def test_build_doc_manifest_empty():
    assert "## Document Manifest" in context_prep.build_doc_manifest([])


def test_build_doc_manifest_one_doc():
    docs = [{"path": "README.md", "size": 100, "content": "First real line of content here"}]
    out = context_prep.build_doc_manifest(docs, max_summary_len=50)
    assert "README.md" in out
    assert "100" in out
    assert "First real line" in out


def test_build_doc_manifest_truncates_summary():
    long_line = "x" * 200
    docs = [{"path": "a.md", "size": 10, "content": long_line}]
    out = context_prep.build_doc_manifest(docs, max_summary_len=40)
    assert out.endswith("...")


def test_extract_summary_skips_headers_and_short():
    assert (
        context_prep._extract_summary("# only\n\n---\n\n", 80) == "(empty or header-only document)"
    )
    assert context_prep._extract_summary("hi\n\n", 80) == "(empty or header-only document)"


def test_extract_summary_first_meaningful_line():
    text = "# Title\n\nShort\n\nThis is the meaningful content line for tests."
    assert "meaningful content" in context_prep._extract_summary(text, 200)


def test_build_project_brief_no_cli():
    assert context_prep.build_project_brief([], None, Path("."), MagicMock()) is None


def test_build_project_brief_single_batch_success():
    cli = MagicMock()
    cli.execute_prompt.return_value = {"success": True, "output": "brief " * 100}
    docs = [{"path": "a.md", "size": 5, "content": "hello world here"}]
    root = Path("/tmp")
    logger = MagicMock()
    out = context_prep.build_project_brief(docs, cli, root, logger, max_brief_chars=500)
    assert out is not None
    assert "brief" in out
    cli.execute_prompt.assert_called_once()


def test_build_project_brief_single_batch_truncates():
    cli = MagicMock()
    cli.execute_prompt.return_value = {"success": True, "output": "x" * 2000}
    docs = [{"path": "a.md", "size": 3, "content": "abc"}]
    out = context_prep.build_project_brief(docs, cli, Path("."), MagicMock(), max_brief_chars=100)
    assert out is not None
    assert "truncated" in out


def test_build_project_brief_single_batch_failure():
    cli = MagicMock()
    cli.execute_prompt.return_value = {"success": False, "error": "nope"}
    docs = [{"path": "a.md", "size": 3, "content": "abc"}]
    logger = MagicMock()
    assert context_prep.build_project_brief(docs, cli, Path("."), logger) is None
    logger.warning.assert_called()


def test_build_project_brief_multi_batch():
    """Force multiple batches by exceeding max_per_batch."""
    cli = MagicMock()
    cli.execute_prompt.side_effect = [
        {"success": True, "output": "summary batch 1"},
        {"success": True, "output": "summary batch 2"},
        {"success": True, "output": "final brief"},
    ]
    huge = "chunk\n" * 60000
    docs = [
        {"path": "a.md", "size": len(huge), "content": huge},
        {"path": "b.md", "size": len(huge), "content": huge},
    ]
    out = context_prep.build_project_brief(docs, cli, Path("."), MagicMock())
    assert out == "final brief"
    assert cli.execute_prompt.call_count == 3


def test_build_project_brief_multi_batch_all_summaries_fail():
    cli = MagicMock()
    cli.execute_prompt.return_value = {"success": False}
    huge = "x" * 60000
    docs = [{"path": "a.md", "size": len(huge), "content": huge}] * 2
    assert context_prep.build_project_brief(docs, cli, Path("."), MagicMock()) is None


def test_summarize_batch_failure():
    cli = MagicMock()
    cli.execute_prompt.return_value = {"success": False}
    logger = MagicMock()
    assert context_prep._summarize_batch("c", 1, 2, cli, Path("."), logger) is None
    logger.warning.assert_called()
