"""Document scanner for AIDLC.

Scans any repository for markdown docs, README files, architecture docs,
design docs, etc. to build project context for planning.
"""

import fnmatch
import os
from pathlib import Path

# Default max chars per doc (overridden by config["max_doc_chars"])
DEFAULT_MAX_DOC_CHARS = 10000

# Priority patterns — docs matching these get loaded first
PRIORITY_PATTERNS = [
    "README.md",
    "README.*",
    "ARCHITECTURE.md",
    "ROADMAP.md",
    "DESIGN.md",
    "CONTRIBUTING.md",
    "CLAUDE.md",
    "docs/README.md",
    "docs/**/*.md",
    "planning/**/*.md",
    "design/**/*.md",
    "specs/**/*.md",
    "requirements/**/*.md",
    "rfcs/**/*.md",
]

# Files that indicate project type
PROJECT_INDICATORS = {
    "package.json": "javascript/typescript",
    "tsconfig.json": "typescript",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "pyproject.toml": "python",
    "setup.py": "python",
    "requirements.txt": "python",
    "Gemfile": "ruby",
    "pom.xml": "java",
    "build.gradle": "java/kotlin",
    "Package.swift": "swift",
    "Makefile": "make",
    "CMakeLists.txt": "c/c++",
    "docker-compose.yml": "docker",
    "Dockerfile": "docker",
}


class ProjectScanner:
    """Scans a repository to understand its structure and planning docs."""

    def __init__(self, project_root: Path, config: dict):
        self.project_root = project_root
        self.config = config
        self.scan_patterns = config.get("doc_scan_patterns", ["**/*.md"])
        self.exclude_patterns = config.get("doc_scan_exclude", [
            "node_modules/**", ".git/**", "venv/**", ".venv/**",
            "__pycache__/**", ".aidlc/**", "dist/**", "build/**",
        ])
        self.max_doc_chars = config.get("max_doc_chars", DEFAULT_MAX_DOC_CHARS)
        self.max_context_chars = config.get("max_context_chars", 80000)

    def scan(self) -> dict:
        """Full project scan. Returns structured context.

        Returns:
            dict with:
                - project_type: detected language/framework
                - doc_files: list of {path, content, priority} dicts
                - structure_summary: text summary of project layout
                - existing_issues: any pre-existing issue/task files
                - total_docs: count
        """
        project_type = self._detect_project_type()
        doc_files = self._scan_docs()
        structure = self._scan_structure()
        existing_issues = self._find_existing_issues()

        audit_result = self._load_audit_result()

        return {
            "project_type": project_type,
            "doc_files": doc_files,
            "structure_summary": structure,
            "existing_issues": existing_issues,
            "total_docs": len(doc_files),
            "audit_result": audit_result,
            "scan_warnings": {
                "skipped_docs": getattr(self, "_skipped_docs_count", 0),
                "skipped_issue_reads": getattr(self, "_skipped_issue_reads", 0),
                "audit_result_load_errors": getattr(self, "_audit_load_errors", 0),
            },
        }

    def _detect_project_type(self) -> str:
        """Detect project type from indicator files."""
        detected = []
        for filename, ptype in PROJECT_INDICATORS.items():
            if (self.project_root / filename).exists():
                detected.append(ptype)
        return ", ".join(set(detected)) if detected else "unknown"

    def _scan_docs(self) -> list[dict]:
        """Find and read all documentation files."""
        all_docs = set()
        self._skipped_docs_count = 0

        for pattern in self.scan_patterns:
            for path in self.project_root.rglob(pattern.lstrip("*").lstrip("/")):
                if path.is_file():
                    rel = str(path.relative_to(self.project_root))
                    if not self._is_excluded(rel):
                        all_docs.add(rel)

        # Also do glob-based scan for each pattern
        for pattern in self.scan_patterns:
            for path in self.project_root.glob(pattern):
                if path.is_file():
                    rel = str(path.relative_to(self.project_root))
                    if not self._is_excluded(rel):
                        all_docs.add(rel)

        # Read and prioritize
        docs = []
        for rel_path in sorted(all_docs):
            full_path = self.project_root / rel_path
            try:
                content = full_path.read_text(errors="replace")
                if len(content) > self.max_doc_chars:
                    content = content[:self.max_doc_chars] + "\n\n... (truncated)"
                priority = self._doc_priority(rel_path)
                docs.append({
                    "path": rel_path,
                    "content": content,
                    "priority": priority,
                    "size": len(content),
                })
            except (OSError, UnicodeDecodeError):
                self._skipped_docs_count += 1
                continue

        # Sort by priority (lower = higher priority), then path
        docs.sort(key=lambda d: (d["priority"], d["path"]))
        return docs

    def _doc_priority(self, rel_path: str) -> int:
        """Assign priority score. Lower = more important."""
        lower = rel_path.lower()

        # Top priority: root-level project docs
        if lower in ("readme.md", "architecture.md", "roadmap.md", "design.md", "claude.md"):
            return 0

        # High: planning/design directories
        for prefix in ("planning/", "design/", "specs/", "rfcs/", "docs/"):
            if lower.startswith(prefix):
                return 1

        # Medium: other docs
        if lower.startswith("docs/"):
            return 2

        # Lower: everything else
        return 3

    def _is_excluded(self, rel_path: str) -> bool:
        """Check if path matches any exclusion pattern."""
        for pattern in self.exclude_patterns:
            if fnmatch.fnmatch(rel_path, pattern):
                return True
            # Also check directory components
            parts = rel_path.split("/")
            for part in parts:
                if fnmatch.fnmatch(part, pattern.rstrip("/**").rstrip("/*")):
                    return True
        return False

    def _scan_structure(self) -> str:
        """Build a summary of the project directory structure."""
        lines = ["## Project Structure\n"]
        top_entries = sorted(self.project_root.iterdir())

        for entry in top_entries:
            name = entry.name
            if name.startswith(".") and name not in (".github",):
                continue
            if name in ("node_modules", "__pycache__", "venv", ".venv", "dist", "build"):
                continue

            if entry.is_dir():
                sub_count = sum(1 for _ in entry.rglob("*") if _.is_file())
                lines.append(f"- {name}/ ({sub_count} files)")
            else:
                lines.append(f"- {name}")

        return "\n".join(lines)

    def _find_existing_issues(self) -> list[dict]:
        """Find any pre-existing issue/task markdown files in the repo."""
        issue_patterns = [
            ".aidlc/issues/*.md",
            "issues/*.md",
            "tasks/*.md",
            "planning/issues/*.md",
            "docs/issues/*.md",
        ]
        issues = []
        self._skipped_issue_reads = 0
        for pattern in issue_patterns:
            for path in self.project_root.glob(pattern):
                if path.is_file():
                    try:
                        content = path.read_text(errors="replace")
                        rel = str(path.relative_to(self.project_root))
                        issues.append({"path": rel, "content": content})
                    except (OSError, UnicodeDecodeError):
                        self._skipped_issue_reads += 1
                        continue
        return issues

    def _load_audit_result(self) -> dict | None:
        """Load audit results from .aidlc/audit_result.json if present."""
        import json
        audit_path = self.project_root / ".aidlc" / "audit_result.json"
        self._audit_load_errors = 0
        if audit_path.exists():
            try:
                with open(audit_path) as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                self._audit_load_errors += 1
        return None

    def build_context_prompt(self, scan_result: dict) -> str:
        """Build a project context string from scan results for use in prompts."""
        sections = []

        # Project type
        sections.append(f"**Project type**: {scan_result['project_type']}")
        sections.append(f"**Total documentation files**: {scan_result['total_docs']}")
        warnings = scan_result.get("scan_warnings", {})
        skipped_docs = warnings.get("skipped_docs", 0)
        skipped_issue_reads = warnings.get("skipped_issue_reads", 0)
        audit_load_errors = warnings.get("audit_result_load_errors", 0)
        if skipped_docs or skipped_issue_reads or audit_load_errors:
            sections.append(
                "**Scanner degraded reads**: "
                f"{skipped_docs} docs skipped, "
                f"{skipped_issue_reads} issue files skipped, "
                f"{audit_load_errors} audit result load errors"
            )
        sections.append("")

        # Structure
        sections.append(scan_result["structure_summary"])
        sections.append("")

        # Key docs (top priority first, capped)
        sections.append("## Key Documentation\n")
        included = 0
        total_chars = 0
        max_context_chars = self.max_context_chars

        for doc in scan_result["doc_files"]:
            if total_chars + doc["size"] > max_context_chars:
                sections.append(f"\n... ({scan_result['total_docs'] - included} more docs not shown)")
                break
            sections.append(f"### {doc['path']}\n")
            sections.append(doc["content"])
            sections.append("")
            included += 1
            total_chars += doc["size"]

        # Audit findings
        audit = scan_result.get("audit_result")
        if audit:
            sections.append("\n## Code Audit Findings\n")
            sections.append(f"**Audit depth**: {audit.get('depth', 'quick')}")
            sections.append(f"**Detected frameworks**: {', '.join(audit.get('frameworks', [])) or 'none'}")
            sections.append(f"**Entry points**: {', '.join(audit.get('entry_points', [])) or 'none'}")

            modules = audit.get("modules", [])
            if modules:
                sections.append("\n**Modules:**")
                for m in modules:
                    name = m.get("name", "?")
                    role = m.get("role", "unknown")
                    files = m.get("file_count", 0)
                    lines = m.get("line_count", 0)
                    sections.append(f"- `{name}/` — {role} ({files} files, {lines:,} lines)")

            stats = audit.get("source_stats", {})
            if stats:
                sections.append(f"\n**Source stats**: {stats.get('total_files', 0)} files, {stats.get('total_lines', 0):,} lines")

            tc = audit.get("test_coverage")
            if tc:
                sections.append(f"**Test coverage**: {tc.get('estimated_coverage', 'unknown')} ({tc.get('test_files', 0)} test files, {tc.get('test_functions', 0)} test functions)")

            features = audit.get("features")
            if features:
                sections.append("\n**Features:**")
                for feat in features:
                    sections.append(f"- {feat}")

            tech_debt = audit.get("tech_debt")
            if tech_debt:
                sections.append(f"\n**Tech debt markers**: {len(tech_debt)} found")
                for td in tech_debt[:10]:
                    sections.append(f"- `{td.get('file', '?')}:{td.get('line', 0)}` [{td.get('type', '?')}] {td.get('text', '')[:100]}")
                if len(tech_debt) > 10:
                    sections.append(f"- ... and {len(tech_debt) - 10} more")

            sections.append("")

        # Existing issues
        if scan_result["existing_issues"]:
            sections.append(f"\n## Existing Issues ({len(scan_result['existing_issues'])} found)\n")
            for issue in scan_result["existing_issues"]:
                sections.append(f"### {issue['path']}\n")
                sections.append(issue["content"])
                sections.append("")

        return "\n".join(sections)
