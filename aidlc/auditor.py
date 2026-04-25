"""Code auditor orchestrator for existing repositories."""

import logging
from pathlib import Path

from .audit.constants import DEFAULT_SOURCE_EXTENSIONS
from .audit.full_engine import FullAuditEngine
from .audit.output_engine import AuditOutputEngine
from .audit.quick_engine import QuickAuditEngine
from .audit.runtime_engine import RuntimeAuditEngine
from .audit_models import AuditResult


class CodeAuditor:
    """Analyzes existing codebases and generates status documentation."""

    def __init__(
        self,
        project_root: Path,
        config: dict,
        cli=None,
        logger: logging.Logger | None = None,
    ):
        self.project_root = project_root
        self.config = config
        self.cli = cli
        self.logger = logger or logging.getLogger(__name__)
        self.source_extensions = set(
            config.get("audit_source_extensions", DEFAULT_SOURCE_EXTENSIONS)
        )
        self.exclude_patterns = config.get("audit_exclude_patterns", [])
        self.max_claude_calls = config.get("audit_max_claude_calls", 10)
        self.max_source_chars = config.get("audit_max_source_chars_per_module", 15000)
        self.degraded_stats = {
            "dependency_parse_errors": 0,
            "source_read_errors": 0,
            "doc_read_errors": 0,
            "line_count_errors": 0,
        }

        # Engines keep implementation details out of this facade.
        self._quick = QuickAuditEngine(self)
        self._full = FullAuditEngine(self)
        self._runtime = RuntimeAuditEngine(self)
        self._output = AuditOutputEngine(self)

    def _mark_degraded(self, key: str) -> None:
        self.degraded_stats[key] = self.degraded_stats.get(key, 0) + 1

    def run(self, depth: str = "quick") -> AuditResult:
        """Run the code audit. depth is 'quick' or 'full'."""
        self.logger.info(f"Starting {depth} code audit...")
        result = self._quick.quick_scan()
        result.depth = depth

        if depth == "full" and self.cli:
            self.logger.info("Running full audit with Claude analysis...")
            result = self._full.full_audit(result)

        if depth == "full" and self.config.get("audit_runtime_enabled", True):
            self.logger.info("Running runtime audit checks (build/test/e2e)...")
            result.runtime_checks = self._runtime.run_runtime_checks(
                result.project_type
            )

        self._output.generate_docs(result)
        result.conflicts = self._output.detect_conflicts(result)
        result.degraded_stats = dict(self.degraded_stats)

        if result.conflicts:
            self._output.write_conflicts_file(result.conflicts)

        self._output.save_audit_json(result)

        self.logger.info(
            f"Audit complete: {len(result.modules)} modules, "
            f"{len(result.frameworks)} frameworks, "
            f"{len(result.entry_points)} entry points"
        )
        degraded_total = sum(result.degraded_stats.values())
        if degraded_total:
            self.logger.warning(
                f"Audit completed with degraded reads: {degraded_total} ({result.degraded_stats})"
            )
        if result.conflicts:
            self.logger.warning(
                f"Found {len(result.conflicts)} conflict(s) with existing docs"
            )

        return result
