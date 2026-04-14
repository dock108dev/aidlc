"""Finalization engine for AIDLC.

Runs configurable audit and cleanup passes after implementation:
- ssot: Dead code removal, enforce single source of truth
- security: Deep security audit with findings report
- abend: Error handling audit, fix dangerous suppressions
- docs: Documentation consolidation and rewrite
- cleanup: Code quality, formatting, consistency

Each pass calls Claude with edit permissions and a focused prompt.
Reports are written to docs/audits/ and .aidlc/reports/.
"""

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .claude_cli import ClaudeCLI
from .finalize_prompts import (
    FUTURES_TEMPLATE,
    PASS_DESCRIPTIONS,
    PASS_ORDER,
    SSOT_PROMPT,
    SECURITY_PROMPT,
    ABEND_PROMPT,
    DOCS_PROMPT,
    CLEANUP_PROMPT,
)
from .models import RunState, RunPhase
from .state_manager import save_state


PASS_PROMPTS = {
    "ssot": SSOT_PROMPT,
    "security": SECURITY_PROMPT,
    "abend": ABEND_PROMPT,
    "docs": DOCS_PROMPT,
    "cleanup": CLEANUP_PROMPT,
}


class Finalizer:
    """Runs post-implementation finalization passes."""

    def __init__(
        self,
        state: RunState,
        run_dir: Path,
        config: dict,
        cli: ClaudeCLI,
        project_context: str,
        logger,
    ):
        self.state = state
        self.run_dir = run_dir
        self.config = config
        self.cli = cli
        self.project_context = project_context
        self.logger = logger
        self.project_root = Path(config["_project_root"])

    def run(self, passes: list[str] | None = None) -> None:
        """Run selected finalization passes."""
        all_passes = [p for p in PASS_ORDER if p in PASS_PROMPTS]
        selected = passes if passes is not None else all_passes

        # Filter to valid pass names
        valid = [p for p in selected if p in PASS_PROMPTS]
        if not valid:
            self.logger.warning(f"No valid finalization passes in: {selected}")
            return

        self.state.phase = RunPhase.FINALIZING
        self.state.finalize_passes_requested = valid
        save_state(self.state, self.run_dir)

        self.logger.info(f"Starting finalization: {', '.join(valid)}")

        # Ensure audit output directory exists
        audit_dir = self.project_root / "docs" / "audits"
        audit_dir.mkdir(parents=True, exist_ok=True)

        for pass_name in valid:
            self._run_pass(pass_name)
            save_state(self.state, self.run_dir)

        # Update config with any newly detected values (codebase may have changed)
        self._refresh_config()

        # Write AIDLC futures note
        self._write_futures_note()

        self.logger.info(
            f"Finalization complete: {len(self.state.finalize_passes_completed)}/{len(valid)} passes"
        )

    def _run_pass(self, pass_name: str) -> None:
        """Execute a single finalization pass."""
        description = PASS_DESCRIPTIONS.get(pass_name, pass_name)
        self.logger.info(f"=== Finalize: {pass_name} — {description} ===")

        # Build the prompt with project context
        prompt_template = PASS_PROMPTS[pass_name]
        diff_summary = self._get_diff_summary() if pass_name in ("ssot", "cleanup") else ""

        prompt = prompt_template.format(
            project_context=self.project_context,
            diff_summary=diff_summary or "(no diff available — working on main branch)",
        )

        # Execute Claude with edit permissions (no hard timeout — warns if long)
        start = time.time()
        finalize_model = self.config.get("claude_model_finalization")
        result = self.cli.execute_prompt(
            prompt=prompt,
            working_dir=self.project_root,
            allow_edits=True,
            model_override=finalize_model,
        )
        self.state.record_claude_result(result, self.config)
        duration = time.time() - start

        self.state.elapsed_seconds += duration

        # Save raw output
        output_text = result.get("output", "")
        if output_text:
            output_dir = self.run_dir / "claude_outputs"
            output_dir.mkdir(exist_ok=True)
            (output_dir / f"finalize_{pass_name}.md").write_text(output_text)

        if result["success"]:
            self.state.finalize_passes_completed.append(pass_name)
            self.logger.info(f"Pass {pass_name} complete ({duration:.0f}s)")
        else:
            self.logger.error(
                f"Pass {pass_name} failed: {result.get('error')} ({duration:.0f}s)"
            )

    def _refresh_config(self):
        """Re-detect project config after finalization (codebase may have changed)."""
        try:
            from .config_detect import detect_config, update_config_file
            detected = detect_config(self.project_root)
            if any(v for k, v in detected.items() if not k.startswith("_")):
                self.logger.info("Refreshing config with post-finalization detection...")
                update_config_file(self.project_root, detected, self.logger)
        except Exception as e:
            self.logger.warning(f"Config refresh failed: {e}")

    def _get_diff_summary(self) -> str:
        """Get git diff summary between main and HEAD."""
        try:
            # Try to get diff against main/master
            for base in ("origin/main", "origin/master", "main", "master"):
                result = subprocess.run(
                    ["git", "diff", "--stat", f"{base}...HEAD"],
                    capture_output=True,
                    text=True,
                    cwd=str(self.project_root),
                    timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    # Also get the full diff (capped)
                    full_diff = subprocess.run(
                        ["git", "diff", f"{base}...HEAD"],
                        capture_output=True,
                        text=True,
                        cwd=str(self.project_root),
                        timeout=30,
                    )
                    diff_text = full_diff.stdout[:30000] if full_diff.returncode == 0 else ""
                    return f"### Diff Stats\n```\n{result.stdout}\n```\n\n### Diff Detail\n```\n{diff_text}\n```"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return ""

    def _write_futures_note(self) -> None:
        """Write AIDLC_FUTURES.md to repo root."""
        # Build audit report links
        audit_dir = self.project_root / "docs" / "audits"
        report_links = []
        if audit_dir.exists():
            for f in sorted(audit_dir.iterdir()):
                if f.is_file() and f.suffix == ".md":
                    report_links.append(f"- [{f.stem}](docs/audits/{f.name})")

        # Get current branch
        branch = "unknown"
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True, text=True,
                cwd=str(self.project_root), timeout=10,
            )
            if result.returncode == 0:
                branch = result.stdout.strip() or "HEAD"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # Detect project type
        project_type = "unknown"
        if "project_type" in self.project_context.lower():
            for line in self.project_context.split("\n"):
                if "project type" in line.lower():
                    project_type = line.split(":")[-1].strip() if ":" in line else "unknown"
                    break

        content = FUTURES_TEMPLATE.format(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            run_id=self.state.run_id,
            total_issues=self.state.total_issues,
            issues_implemented=self.state.issues_implemented,
            issues_verified=self.state.issues_verified,
            issues_failed=self.state.issues_failed,
            passes_completed=", ".join(self.state.finalize_passes_completed) or "none",
            audit_report_links="\n".join(report_links) or "No audit reports generated.",
            branch=branch,
            project_type=project_type,
        )

        futures_path = self.project_root / "AIDLC_FUTURES.md"
        futures_path.write_text(content)
        self.logger.info(f"Wrote {futures_path}")
