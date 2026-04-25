"""Full audit engine (Claude-assisted analysis)."""

import os

from ..audit_models import AuditResult, ModuleInfo
from ..audit_schemas import (
    AUDIT_FEATURE_INVENTORY_PROMPT,
    AUDIT_MODULE_ANALYSIS_PROMPT,
    parse_audit_feature_output,
    parse_audit_module_output,
)
from .constants import EXCLUDE_DIRS


class FullAuditEngine:
    """Enhances quick audit results using Claude analysis."""

    def __init__(self, auditor):
        self.auditor = auditor

    @property
    def project_root(self):
        return self.auditor.project_root

    @property
    def source_extensions(self):
        return self.auditor.source_extensions

    @property
    def max_claude_calls(self):
        return self.auditor.max_claude_calls

    @property
    def max_source_chars(self):
        return self.auditor.max_source_chars

    @property
    def logger(self):
        return self.auditor.logger

    @property
    def cli(self):
        return self.auditor.cli

    def full_audit(self, result: AuditResult) -> AuditResult:
        """Enhance quick scan results with Claude-powered semantic analysis."""
        if not self.cli:
            self.logger.warning("No Claude CLI available for full audit, skipping.")
            return result

        claude_calls = 0
        module_analyses = {}
        for module in result.modules:
            if claude_calls >= self.max_claude_calls:
                self.logger.info(
                    f"Reached max Claude calls ({self.max_claude_calls}), skipping remaining modules"
                )
                break
            if module.role == "tests":
                continue

            analysis = self.analyze_module_with_claude(module)
            if analysis:
                module_analyses[module.name] = analysis
                claude_calls += 1

        if claude_calls < self.max_claude_calls and module_analyses:
            features = self.inventory_features_with_claude(result, module_analyses)
            if features:
                result.features = features
        return result

    def analyze_module_with_claude(self, module: ModuleInfo) -> dict | None:
        """Send module source to Claude for semantic analysis."""
        source_content = self.read_module_source(module)
        if not source_content:
            return None

        prompt = AUDIT_MODULE_ANALYSIS_PROMPT.format(
            module_name=module.name,
            module_path=module.path,
            source_content=source_content,
        )
        cli_result = self.cli.execute_prompt(
            prompt=prompt,
            working_dir=self.project_root,
            allow_edits=False,
        )
        if cli_result["success"] and cli_result["output"]:
            try:
                return parse_audit_module_output(cli_result["output"])
            except ValueError as err:
                self.logger.warning(
                    f"Failed to parse module analysis for {module.name}: {err}"
                )
        return None

    def read_module_source(self, module: ModuleInfo) -> str:
        """Read source files from a module, truncated to max_source_chars."""
        parts = []
        total_chars = 0
        module_path = self.project_root / module.path

        for root, dirs, files in os.walk(module_path):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for filename in sorted(files):
                ext = os.path.splitext(filename)[1]
                if ext not in self.source_extensions:
                    continue
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, self.project_root)
                try:
                    content = open(full_path, errors="replace").read()
                    if total_chars + len(content) > self.max_source_chars:
                        remaining = self.max_source_chars - total_chars
                        if remaining > 500:
                            parts.append(
                                f"\n--- {rel_path} (truncated) ---\n{content[:remaining]}"
                            )
                        break
                    parts.append(f"\n--- {rel_path} ---\n{content}")
                    total_chars += len(content)
                except OSError:
                    self.auditor._mark_degraded("source_read_errors")
                    continue
        return "\n".join(parts)

    def inventory_features_with_claude(
        self, result: AuditResult, module_analyses: dict
    ) -> list[str] | None:
        """Ask Claude to inventory features based on module analyses."""
        summaries = []
        for name, analysis in module_analyses.items():
            desc = analysis.get("description", "")
            caps = analysis.get("capabilities", [])
            summaries.append(f"- **{name}**: {desc}\n  Capabilities: {', '.join(caps)}")

        prompt = AUDIT_FEATURE_INVENTORY_PROMPT.format(
            project_type=result.project_type,
            frameworks=", ".join(result.frameworks) or "none detected",
            module_summaries="\n".join(summaries),
        )
        cli_result = self.cli.execute_prompt(
            prompt=prompt,
            working_dir=self.project_root,
            allow_edits=False,
        )
        if cli_result["success"] and cli_result["output"]:
            try:
                data = parse_audit_feature_output(cli_result["output"])
                features = data.get("features", [])
                return [
                    f"{feature.get('name', '?')} ({feature.get('status', '?')}): "
                    f"{feature.get('description', '')}"
                    for feature in features
                ]
            except ValueError as err:
                self.logger.warning(f"Failed to parse feature inventory: {err}")
        return None
