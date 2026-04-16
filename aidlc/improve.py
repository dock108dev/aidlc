"""Targeted improvement cycle for AIDLC.

User describes a concern ("economy feels flat", "needs better UI", "tune difficulty"),
AIDLC runs a focused mini-lifecycle:
  1. AUDIT — analyze the relevant area of the codebase
  2. RESEARCH — investigate improvements, patterns, solutions
  3. PLAN — create focused fix/enhancement issues
  4. IMPLEMENT — apply changes
  5. VERIFY — run tests
  6. FINALIZE — cleanup pass on touched files

This is a scoped, targeted version of the full lifecycle.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

from .context_utils import parse_project_type
from .models import Issue, RunPhase, RunState, RunStatus
from .research_output import (
    add_research_output_constraints,
    build_repair_prompt,
    is_permission_chatter,
)
from .schemas import parse_json_output
from .state_manager import generate_run_id, save_state


# ANSI helpers
def _bold(text):
    return f"\033[1m{text}\033[0m" if sys.stdout.isatty() else text


def _dim(text):
    return f"\033[2m{text}\033[0m" if sys.stdout.isatty() else text


def _cyan(text):
    return f"\033[36m{text}\033[0m" if sys.stdout.isatty() else text


def _green(text):
    return f"\033[32m{text}\033[0m" if sys.stdout.isatty() else text


def _yellow(text):
    return f"\033[33m{text}\033[0m" if sys.stdout.isatty() else text


AUDIT_PROMPT = """\
You are auditing a specific area of this codebase based on user feedback.

## User's Concern
{user_concern}

## Project Context
{project_context}

## Instructions

1. Identify ALL files and systems relevant to the user's concern
2. Analyze the current implementation — what works, what's weak, what's missing
3. Research best practices and improvements for this specific area
4. Produce a detailed assessment with concrete improvement recommendations

Output a JSON block:
```json
{{
  "area_summary": "What this area does currently",
  "files_involved": ["list of relevant file paths"],
  "strengths": ["what works well"],
  "weaknesses": ["what needs improvement"],
  "improvements": [
    {{
      "title": "Short improvement title",
      "description": "What to change and why",
      "priority": "high | medium | low",
      "complexity": "small | medium | large",
      "files_to_change": ["paths"]
    }}
  ],
  "research_needed": [
    {{
      "topic": "topic-name",
      "question": "What to research"
    }}
  ]
}}
```
"""

RESEARCH_PROMPT = """\
Research the following topic to improve a software project.

## Topic
{topic}

## Question
{question}

## Context
This is for: {user_concern}
Project type: {project_type}

## Instructions
Provide concrete, implementable recommendations. Include:
- Specific algorithms, patterns, or approaches
- Code examples or pseudocode
- Parameter values and tuning suggestions
- References to established best practices

Output as markdown.
"""


class ImprovementCycle:
    """Runs a targeted improvement cycle on a specific area."""

    def __init__(
        self,
        project_root: Path,
        config: dict,
        cli,
        logger,
        project_context: str,
    ):
        self.project_root = project_root
        self.config = config
        self.cli = cli
        self.logger = logger
        self.project_context = project_context

    def run(self, user_concern: str, auto_implement: bool = True) -> dict:
        """Run the full improvement cycle.

        Args:
            user_concern: What the user wants improved
            auto_implement: If True, implement improvements automatically

        Returns:
            Summary dict with audit findings and actions taken
        """
        print(f"\n  {_bold('Improvement Cycle')}: {user_concern}")
        print()

        # 1. AUDIT — analyze the area
        print(f"  {_cyan('1/6')} Auditing relevant code...")
        audit = self._audit_area(user_concern)
        if not audit:
            print(f"  {_yellow('!')} Could not audit area")
            return {"error": "audit failed"}

        print(f"  {_green('+')} Found {len(audit.get('improvements', []))} potential improvements")
        print(f"  {_green('+')} {len(audit.get('files_involved', []))} files involved")

        # Show findings
        for weakness in audit.get("weaknesses", [])[:5]:
            print(f"    - {weakness}")

        # 2. RESEARCH — investigate improvements
        research_topics = audit.get("research_needed", [])
        if research_topics:
            print(f"\n  {_cyan('2/6')} Researching {len(research_topics)} topic(s)...")
            self._run_research(research_topics, user_concern)
        else:
            print(f"\n  {_cyan('2/6')} No additional research needed")

        # 3. PLAN — create improvement issues
        improvements = audit.get("improvements", [])
        if not improvements:
            print(f"\n  {_green('+')} No improvements needed — area looks good!")
            return {"status": "no_improvements", "audit": audit}

        print(f"\n  {_cyan('3/6')} Creating {len(improvements)} improvement issue(s)...")
        issues = self._create_issues(improvements, user_concern)

        for issue in issues:
            print(f"    {_green('+')} {issue.id}: {issue.title}")

        if not auto_implement:
            print(f"\n  Issues created. Run {_cyan('aidlc run --implement-only')} to apply.")
            return {"status": "planned", "issues": [i.id for i in issues], "audit": audit}

        # 4. IMPLEMENT — apply changes
        print(f"\n  {_cyan('4/6')} Implementing {len(issues)} improvement(s)...")
        implemented = self._implement_issues(issues)
        print(f"  {_green('+')} {implemented}/{len(issues)} implemented")

        # 5. VERIFY — run tests
        print(f"\n  {_cyan('5/6')} Verifying changes...")
        tests_pass = self._verify()
        if tests_pass:
            print(f"  {_green('+')} Tests passing")
        else:
            print(f"  {_yellow('!')} Some tests may need attention")

        # 6. FINALIZE — run relevant passes based on scope of changes
        print(f"\n  {_cyan('6/6')} Running finalization...")
        self._run_finalization(improvements, implemented)

        print(f"\n  {_green('Improvement cycle complete!')}")
        return {
            "status": "complete",
            "improvements": len(improvements),
            "implemented": implemented,
            "tests_passing": tests_pass,
            "audit": audit,
        }

    def _audit_area(self, concern: str) -> dict | None:
        """Audit the specific area of concern."""
        prompt = AUDIT_PROMPT.format(
            user_concern=concern,
            project_context=self.project_context[:40000],
        )

        result = self.cli.execute_prompt(prompt, self.project_root)
        if not result["success"]:
            return None

        try:
            return parse_json_output(result["output"])
        except ValueError:
            self.logger.warning("Could not parse audit output")
            return None

    def _run_research(self, topics: list[dict], concern: str):
        """Research improvement topics."""
        research_dir = self.project_root / "docs" / "research"
        research_dir.mkdir(parents=True, exist_ok=True)

        project_type = parse_project_type(self.project_context)

        for topic in topics[:3]:  # Cap at 3 research topics per cycle
            name = topic.get("topic", "unknown")
            question = topic.get("question", "")

            import re
            sanitized = re.sub(r"[^a-z0-9_-]", "-", name.lower())[:80]
            output_path = research_dir / f"improve-{sanitized}.md"

            if output_path.exists():
                print(f"    {_dim('skip')} {name} (already researched)")
                continue

            print(f"    {_cyan('researching')} {name}...")
            prompt = RESEARCH_PROMPT.format(
                topic=name,
                question=question,
                user_concern=concern,
                project_type=project_type,
            )
            prompt = add_research_output_constraints(prompt)

            res = self.cli.execute_prompt(prompt, self.project_root)
            if res["success"] and res.get("output"):
                output = res["output"]
                if is_permission_chatter(output):
                    self.logger.warning(
                        "Improvement research output requested write permissions; retrying"
                    )
                    retry_prompt = build_repair_prompt(name, question, output)
                    retry_res = self.cli.execute_prompt(retry_prompt, self.project_root)
                    if not retry_res["success"] or not retry_res.get("output"):
                        print(f"    {_yellow('!')} research failed for {name}")
                        continue
                    output = retry_res["output"]
                    if is_permission_chatter(output):
                        print(f"    {_yellow('!')} research returned invalid output for {name}")
                        continue
                output_path.write_text(
                    f"# Research: {name}\n\n"
                    f"*Generated for improvement: {concern}*\n\n"
                    f"---\n\n{output}"
                )
                print(f"    {_green('+')} docs/research/improve-{sanitized}.md")

    def _create_issues(self, improvements: list[dict], concern: str) -> list[Issue]:
        """Create Issue objects from audit improvements."""
        issues = []
        for i, imp in enumerate(improvements, 1):
            issue = Issue(
                id=f"IMP-{i:03d}",
                title=imp.get("title", f"Improvement {i}")[:120],
                description=(
                    f"**Context:** {concern}\n\n"
                    f"{imp.get('description', '')}\n\n"
                    f"**Files to change:** {', '.join(imp.get('files_to_change', []))}"
                ),
                priority=imp.get("priority", "medium"),
                labels=["improvement", "auto-generated"],
                acceptance_criteria=[
                    f"Improvement applied: {imp.get('title', '')}",
                    "No regressions introduced",
                    "Code follows project conventions",
                ],
            )
            issues.append(issue)

            # Write issue file
            issues_dir = self.project_root / ".aidlc" / "issues"
            issues_dir.mkdir(parents=True, exist_ok=True)
            acceptance = "\n".join(f"- [ ] {ac}" for ac in issue.acceptance_criteria)
            (issues_dir / f"{issue.id}.md").write_text(
                f"# {issue.id}: {issue.title}\n\n"
                f"**Priority**: {issue.priority}\n"
                f"**Labels**: {', '.join(issue.labels)}\n\n"
                f"## Description\n\n{issue.description}\n\n"
                f"## Acceptance Criteria\n\n{acceptance}"
            )

        return issues

    def _implement_issues(self, issues: list[Issue]) -> int:
        """Implement improvement issues via Claude."""
        from .implementer import Implementer

        # Create a mini run state
        run_id = generate_run_id("improve")
        runs_dir = self.project_root / ".aidlc" / "runs"
        run_dir = runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "claude_outputs").mkdir()

        state = RunState(run_id=run_id, config_name="improve")
        state.project_root = str(self.project_root)
        state.started_at = datetime.now(timezone.utc).isoformat()
        state.status = RunStatus.RUNNING
        state.phase = RunPhase.IMPLEMENTING

        for issue in issues:
            state.update_issue(issue)
        state.total_issues = len(issues)

        save_state(state, run_dir)

        implementer = Implementer(
            state, run_dir, self.config, self.cli,
            self.project_context, self.logger,
        )

        for issue in issues:
            pending = state.get_issue(issue.id)
            if pending:
                self.logger.info(f"  Implementing: {issue.id} — {issue.title[:60]}")
                implementer._implement_issue(pending)
                save_state(state, run_dir)

        return state.issues_implemented

    def _run_finalization(self, improvements: list[dict], implemented: int):
        """Run finalization passes scoped to what changed.

        Small changes (1-2 files): just cleanup
        Medium changes (3-10 files): cleanup + abend
        Large changes (10+ files or 5+ improvements): all passes
        """
        if implemented == 0:
            print(f"    {_dim('skip')} Nothing implemented, skipping finalization")
            return

        # Count total files touched
        all_files = set()
        for imp in improvements[:implemented]:
            all_files.update(imp.get("files_to_change", []))

        file_count = len(all_files)

        # Determine which passes to run based on scope
        if file_count <= 2 and implemented <= 2:
            passes = ["cleanup"]
            scope = "small"
        elif file_count <= 10 and implemented <= 5:
            passes = ["cleanup", "abend"]
            scope = "medium"
        else:
            passes = ["ssot", "security", "abend", "docs", "cleanup"]
            scope = "large"

        print(f"    Scope: {scope} ({file_count} files, {implemented} improvements)")
        print(f"    Running: {', '.join(passes)}")

        try:
            from .finalizer import Finalizer
            from .state_manager import generate_run_id as _gen_id

            # Create a mini run state for the finalizer
            run_id = _gen_id("improve-finalize")
            run_dir = self.project_root / ".aidlc" / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "claude_outputs").mkdir()

            state = RunState(run_id=run_id, config_name="improve")
            state.project_root = str(self.project_root)
            state.status = RunStatus.RUNNING

            finalizer = Finalizer(
                state, run_dir, self.config, self.cli,
                self.project_context, self.logger,
            )
            finalizer.run(passes=passes)

            for p in state.finalize_passes_completed:
                print(f"    {_green('+')} {p} complete")

        except Exception as e:
            self.logger.warning(f"Finalization failed: {e}")
            print(f"    {_yellow('!')} Finalization encountered errors")

    def _verify(self) -> bool:
        """Run tests to verify improvements."""
        import subprocess
        test_cmd = self.config.get("run_tests_command")
        if not test_cmd:
            return True

        try:
            result = subprocess.run(
                test_cmd, shell=True, cwd=str(self.project_root),
                capture_output=True, timeout=self.config.get("test_timeout_seconds", 300),
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, OSError) as exc:
            self.logger.warning(f"Improvement verification failed to run tests: {exc}")
            return False
