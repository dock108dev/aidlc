"""Finalization engine for AIDLC.

Runs configurable cleanup/audit passes after implementation. Each pass calls
the provider with edit permissions and a focused prompt; reports land in
``docs/audits/`` and the raw provider output in ``run_dir/claude_outputs``.

Two cadences:

- **Periodic** (every ``cleanup_passes_every_cycles`` impl cycles, default 10)
  runs the safe subset ``cleanup_passes_periodic`` (default ``["abend",
  "cleanup"]``). Driven from the implementer loop.
- **End-of-run** runs ``finalize_passes`` (``None`` = all passes in
  ``PASS_ORDER``). Driven from the runner.

Every pass enforces an *Actionability Contract*: each finding must either be
fixed in-place or documented as an intentional non-fix with concrete rationale
in both the pass report and at the code site. Bare "TODO" outputs are rejected.
"""

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .finalize_prompts import (
    ABEND_PROMPT,
    CLEANUP_PROMPT,
    DOCS_PROMPT,
    FUTURES_TEMPLATE,
    PASS_DESCRIPTIONS,
    PASS_ORDER,
    SECURITY_PROMPT,
    SSOT_PROMPT,
)
from .models import RunPhase, RunState
from .state_manager import save_state
from .timing import add_console_time

PASS_PROMPTS = {
    "ssot": SSOT_PROMPT,
    "security": SECURITY_PROMPT,
    "abend": ABEND_PROMPT,
    "cleanup": CLEANUP_PROMPT,
    "docs": DOCS_PROMPT,
}

# Passes whose prompts include a {diff_summary} placeholder (need git diff
# injection). Docs is intentionally diff-blind — it audits current state.
DIFF_AWARE_PASSES = {"ssot", "security", "abend", "cleanup"}


class Finalizer:
    """Runs post-implementation finalization passes."""

    def __init__(
        self,
        state: RunState,
        run_dir: Path,
        config: dict,
        cli,
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

    def _project_context_for_finalize(self) -> str:
        """Cap project_context for finalize prompts (avoids CLI 'Prompt is too long')."""
        raw = self.project_context
        max_c = max(
            4000, int(self.config.get("finalize_project_context_max_chars", 22000))
        )
        if len(raw) <= max_c:
            return raw
        head = int(max_c * 0.65)
        tail = max(0, max_c - head - 200)
        sep = (
            "\n\n... [truncated for finalize prompt — read README.md, ARCHITECTURE.md, "
            "DESIGN.md, and repo files in full] ...\n\n"
        )
        return raw[:head] + sep + raw[-tail:]

    def run(self, passes: list[str] | None = None) -> None:
        """Run selected finalization passes."""
        all_passes = [p for p in PASS_ORDER if p in PASS_PROMPTS]
        selected = passes if passes is not None else all_passes

        # Filter to valid pass names
        valid = [p for p in selected if p in PASS_PROMPTS]
        if not valid:
            self.logger.warning(f"No valid finalization passes in: {selected}")
            return

        # Each run is a full batch (e.g. pre-autosync + end-of-run); do not accumulate duplicates.
        self.state.finalize_passes_completed = []
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

        # Build the prompt with project context. Diff-aware passes (ssot,
        # security, abend, cleanup) get the branch diff; docs is current-state
        # only.
        prompt_template = PASS_PROMPTS[pass_name]
        if pass_name in DIFF_AWARE_PASSES:
            diff_summary = self._get_diff_summary() or (
                "(no diff available — working on main branch or first commit)"
            )
            prompt = prompt_template.format(
                project_context=self._project_context_for_finalize(),
                diff_summary=diff_summary,
            )
        else:
            prompt = prompt_template.format(
                project_context=self._project_context_for_finalize(),
            )

        # Execute Claude with edit permissions (no hard timeout — warns if long)
        start = time.time()
        result = self.cli.execute_prompt(
            prompt=prompt,
            working_dir=self.project_root,
            allow_edits=True,
        )
        self.state.record_provider_result(result, self.config, phase="finalization")
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
                self.logger.info(
                    "Refreshing config with post-finalization detection..."
                )
                update_config_file(self.project_root, detected, self.logger)
        except Exception as e:
            self.logger.warning(f"Config refresh failed: {e}")

    def _get_diff_summary(self) -> str:
        """Get git diff summary between main and HEAD."""
        try:
            # Try to get diff against main/master
            for base in ("origin/main", "origin/master", "main", "master"):
                t0 = time.time()
                try:
                    result = subprocess.run(
                        ["git", "diff", "--stat", f"{base}...HEAD"],
                        capture_output=True,
                        text=True,
                        cwd=str(self.project_root),
                        timeout=30,
                    )
                finally:
                    add_console_time(self.state, t0)
                if result.returncode == 0 and result.stdout.strip():
                    # Also get the full diff (capped)
                    t1 = time.time()
                    try:
                        full_diff = subprocess.run(
                            ["git", "diff", f"{base}...HEAD"],
                            capture_output=True,
                            text=True,
                            cwd=str(self.project_root),
                            timeout=30,
                        )
                    finally:
                        add_console_time(self.state, t1)
                    diff_text = (
                        full_diff.stdout[:30000] if full_diff.returncode == 0 else ""
                    )
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
            t0 = time.time()
            try:
                result = subprocess.run(
                    ["git", "branch", "--show-current"],
                    capture_output=True,
                    text=True,
                    cwd=str(self.project_root),
                    timeout=10,
                )
            finally:
                add_console_time(self.state, t0)
            if result.returncode == 0:
                branch = result.stdout.strip() or "HEAD"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # Detect project type
        project_type = "unknown"
        if "project_type" in self.project_context.lower():
            for line in self.project_context.split("\n"):
                if "project type" in line.lower():
                    project_type = (
                        line.split(":")[-1].strip() if ":" in line else "unknown"
                    )
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
