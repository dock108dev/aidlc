"""ISSUE-013: in-process doc-gap caching.

``detect_doc_gaps`` skips the rescan when the doc-state hash hasn't changed
since the last call within the same process.

Note: session-dir pruning tests were removed when ``aidlc plan`` (the only
caller of ``_prune_old_session_dirs``) was retired in the core-focus audit.
"""

from __future__ import annotations

import time
from pathlib import Path

from aidlc.doc_gap_detector import clear_doc_gap_cache, detect_doc_gaps


def _config(project_root):
    return {
        "doc_scan_patterns": ["**/*.md"],
        "doc_scan_exclude": [],
        "doc_gap_max_items": 50,
        "_project_root": str(project_root),
    }


def test_doc_gap_cache_skips_rescan_when_unchanged(tmp_path, monkeypatch):
    clear_doc_gap_cache()
    (tmp_path / "doc.md").write_text("# hello\nTBD: figure this out\n")
    cfg = _config(tmp_path)

    call_count = [0]
    real_glob = Path.glob

    def counting_glob(self, pattern):
        if pattern in ("**/*.md", "**/*.txt", "**/*.rst"):
            call_count[0] += 1
        yield from real_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", counting_glob)

    first = detect_doc_gaps(tmp_path, cfg)
    assert first  # has at least one TBD gap
    after_first = call_count[0]

    second = detect_doc_gaps(tmp_path, cfg)
    assert second == first
    assert call_count[0] >= after_first


def test_doc_gap_cache_invalidates_on_doc_change(tmp_path):
    clear_doc_gap_cache()
    (tmp_path / "doc.md").write_text("# hello\nTBD: one\n")
    cfg = _config(tmp_path)

    first = detect_doc_gaps(tmp_path, cfg)
    time.sleep(0.01)
    (tmp_path / "doc.md").write_text("# hello\nTBD: one\nTBD: two\nTBD: three\n")
    second = detect_doc_gaps(tmp_path, cfg)

    assert len(second) > len(first)


def test_doc_gap_cache_separate_per_project(tmp_path):
    clear_doc_gap_cache()
    p1 = tmp_path / "proj1"
    p2 = tmp_path / "proj2"
    p1.mkdir()
    p2.mkdir()
    (p1 / "x.md").write_text("# x\nTBD\n")
    (p2 / "x.md").write_text("# x\nclean\n")

    cfg1 = _config(p1)
    cfg2 = _config(p2)

    g1 = detect_doc_gaps(p1, cfg1)
    g2 = detect_doc_gaps(p2, cfg2)
    assert g1 != g2
    assert detect_doc_gaps(p1, cfg1) == g1
    assert detect_doc_gaps(p2, cfg2) == g2
