"""Documentation gap detector for AIDLC.

Scans markdown documentation for TBD markers, unfilled placeholders,
and research-needed indicators. Runs before planning to surface
knowledge gaps that may need research actions.
"""

import fnmatch
import logging
import re
from pathlib import Path

from .audit_models import DocGap

# Patterns that indicate knowledge gaps in documentation
_CRITICAL_PATTERNS = re.compile(
    r"\b(DESIGN.TBD|ARCHITECTURE.TBD|ALGORITHM.TBD|FORMULA.NEEDED|"
    r"FORMULA.TBD|NEEDS?.DESIGN|DESIGN.NEEDED)\b",
    re.IGNORECASE,
)

_WARNING_PATTERNS = re.compile(
    r"\b(TBD|TO.BE.DETERMINED|TO.BE.DEFINED|NEEDS?.RESEARCH|"
    r"RESEARCH.NEEDED|PENDING.RESEARCH|TODO.?.RESEARCH|"
    r"TODO.?.FIGURE.OUT|TODO.?.DESIGN|TODO.?.DETERMINE)\b",
    re.IGNORECASE,
)

_INFO_PATTERNS = re.compile(
    r"\{[^}]{3,80}\}|\[TBD\]|\[TODO\]",
)

# Directories to always exclude
_EXCLUDE_DIRS = {
    "node_modules", ".git", "venv", ".venv", "__pycache__",
    ".aidlc", "dist", "build", ".next", "target", "vendor",
}


def detect_doc_gaps(project_root: Path, config: dict) -> list[DocGap]:
    """Scan documentation files for knowledge gaps and placeholders.

    Args:
        project_root: Path to the project directory
        config: AIDLC config dict (uses doc_scan_patterns, doc_scan_exclude)

    Returns:
        List of DocGap items sorted by severity (critical first),
        capped at config["doc_gap_max_items"].
    """
    scan_patterns = config.get("doc_scan_patterns", ["**/*.md"])
    exclude_patterns = config.get("doc_scan_exclude", [])
    max_items = config.get("doc_gap_max_items", 50)

    # Find all doc files
    doc_paths = set()
    for pattern in scan_patterns:
        for path in project_root.glob(pattern):
            if path.is_file():
                rel = str(path.relative_to(project_root))
                if not _is_excluded(rel, exclude_patterns):
                    doc_paths.add(rel)

    # Scan each doc for gaps
    gaps = []
    skipped_docs = 0
    for rel_path in sorted(doc_paths):
        full_path = project_root / rel_path
        try:
            content = full_path.read_text(errors="replace")
        except OSError:
            skipped_docs += 1
            continue

        for line_num, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("```"):
                continue

            # Check patterns in severity order
            match = _CRITICAL_PATTERNS.search(stripped)
            if match:
                gaps.append(DocGap(
                    doc_path=rel_path,
                    line=line_num,
                    pattern=match.group(),
                    text=stripped[:200],
                    severity="critical",
                ))
                continue

            match = _WARNING_PATTERNS.search(stripped)
            if match:
                gaps.append(DocGap(
                    doc_path=rel_path,
                    line=line_num,
                    pattern=match.group(),
                    text=stripped[:200],
                    severity="warning",
                ))
                continue

            match = _INFO_PATTERNS.search(stripped)
            if match:
                # Skip common false positives: JSON examples, code blocks, config refs
                matched_text = match.group()
                if _is_likely_false_positive(matched_text, stripped):
                    continue
                gaps.append(DocGap(
                    doc_path=rel_path,
                    line=line_num,
                    pattern=matched_text,
                    text=stripped[:200],
                    severity="info",
                ))

    # Sort: critical first, then warning, then info
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    gaps.sort(key=lambda g: (severity_order.get(g.severity, 9), g.doc_path, g.line))

    if skipped_docs:
        logging.getLogger("aidlc").warning(
            f"Doc gap detection skipped {skipped_docs} unreadable document(s)."
        )

    return gaps[:max_items]


def _is_excluded(rel_path: str, exclude_patterns: list) -> bool:
    """Check if path matches exclusion patterns."""
    parts = rel_path.split("/")
    for part in parts:
        if part in _EXCLUDE_DIRS:
            return True
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
    return False


def _is_likely_false_positive(matched: str, line: str) -> bool:
    """Filter out {placeholder} matches that are likely code/config examples."""
    # Skip if inside a code fence or backticks
    if f"`{matched}`" in line:
        return True
    # Skip JSON-like patterns: {"key": "value"}
    if '":' in matched or "': " in matched:
        return True
    # Skip common template syntax: {{variable}}, ${variable}
    if matched.startswith("{{") or matched.startswith("${"):
        return True
    # Skip very short matches (likely config references)
    inner = matched.strip("{}")
    if len(inner) <= 2:
        return True
    return False
