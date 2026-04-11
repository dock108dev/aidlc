"""Interactive planning session engine for AIDLC.

Three-phase flow:
1. Guided wizard — structured questions
2. Doc generation + research — Claude generates docs, researches unknowns
3. Claude refinement — interactive session for the user to refine docs
"""

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .claude_cli import ClaudeCLI
from .plan_templates import (
    ARCHITECTURE_GENERATION_PROMPT,
    CLAUDE_MD_GENERATION_PROMPT,
    DESIGN_GENERATION_PROMPT,
    REFINEMENT_SYSTEM_PROMPT,
    RESEARCH_TRIGGER_PROMPT,
    ROADMAP_GENERATION_PROMPT,
)
from .plan_wizard import run_wizard


# ANSI helpers
def _bold(t): return f"\033[1m{t}\033[0m" if sys.stdout.isatty() else t
def _dim(t): return f"\033[2m{t}\033[0m" if sys.stdout.isatty() else t
def _cyan(t): return f"\033[36m{t}\033[0m" if sys.stdout.isatty() else t
def _green(t): return f"\033[32m{t}\033[0m" if sys.stdout.isatty() else t
def _yellow(t): return f"\033[33m{t}\033[0m" if sys.stdout.isatty() else t


class PlanSession:
    """Orchestrates an interactive planning session."""

    def __init__(
        self,
        project_root: Path,
        config: dict,
        cli: ClaudeCLI,
        logger,
    ):
        self.project_root = project_root
        self.config = config
        self.cli = cli
        self.logger = logger
        self.session_dir = project_root / ".aidlc" / "session"

    def run(
        self,
        skip_wizard: bool = False,
        wizard_only: bool = False,
        review_only: bool = False,
    ):
        """Run the full planning session."""
        if review_only:
            self._run_review()
            return

        # Phase 1: Wizard
        if skip_wizard:
            answers = self._load_session_answers()
            if not answers:
                print(f"  {_yellow('!')} No previous session found. Running wizard.")
                answers = run_wizard(self.project_root)
        else:
            answers = run_wizard(self.project_root)

        self._save_session_answers(answers)

        # Phase 2: Research + Doc generation
        print(f"\n  {_bold('Generating documentation...')}")
        print()

        # Identify research needs
        research_topics = self._identify_research(answers)
        if research_topics:
            self._run_research(research_topics)

        # Generate docs
        drafts = self._generate_docs(answers)
        self._save_drafts(drafts)

        print()
        for path in sorted(drafts.keys()):
            print(f"  {_green('+')} {path}")
        print()
        print(f"  Backups saved to {_dim(str(self.session_dir))}")

        if wizard_only:
            print()
            print(f"  Next: run {_cyan('aidlc plan')} to launch Claude refinement session")
            print(f"        or {_cyan('aidlc run')} to start planning and implementation")
            return

        # Phase 3: Claude refinement
        print()
        print(f"  {_bold('Launching Claude for interactive refinement...')}")
        print()
        print(f"  {_dim('Talk through your design with Claude. Claude can edit your docs directly.')}")
        hint = 'Try: "Expand Phase 2" or "What am I missing for MVP?"'
        print(f"  {_dim(hint)}")
        print(f"  {_dim('Exit Claude (Ctrl+C or /exit) when done.')}")
        print()

        self._launch_refinement(answers)

        print()
        print(f"  {_green('Planning session complete!')}")
        print()
        print(f"  Next steps:")
        print(f"    {_cyan('aidlc precheck')}       Verify readiness")
        print(f"    {_cyan('aidlc run')}            Start planning and implementation")
        print(f"    {_cyan('aidlc plan --review')}   Get an audit of your docs")

    def _identify_research(self, answers: dict) -> list[dict]:
        """Identify research topics from wizard answers."""
        brain_dump = answers.get("brain_dump", "")

        # Skip if no brain dump
        if not brain_dump:
            return []

        prompt = RESEARCH_TRIGGER_PROMPT.format(
            project_name=answers.get("project_name", ""),
            one_liner=answers.get("one_liner", ""),
            project_type=answers.get("tech_stack", "unknown"),
            tech_stack=answers.get("tech_stack", ""),
            inspiration="(see brain dump)",
            core_features=brain_dump,
            research_needs="(extract from brain dump above)",
        )

        result = self.cli.execute_prompt(prompt, self.project_root)
        if not result["success"]:
            self.logger.warning("Could not identify research topics")
            return []

        # Parse JSON from response
        try:
            from .schemas import parse_json_output
            output = result.get("output", "")
            # Try to extract JSON array
            import re
            match = re.search(r"\[.*\]", output, re.DOTALL)
            if match:
                topics = json.loads(match.group())
                return topics if isinstance(topics, list) else []
        except (ValueError, json.JSONDecodeError):
            self.logger.warning("Could not parse research topics")
        return []

    def _run_research(self, topics: list[dict]):
        """Run research for identified topics."""
        research_dir = self.project_root / "docs" / "research"
        research_dir.mkdir(parents=True, exist_ok=True)

        high_priority = [t for t in topics if t.get("priority") == "high"]
        other = [t for t in topics if t.get("priority") != "high"]
        ordered = high_priority + other

        for topic in ordered:
            name = topic.get("topic", "unknown")
            question = topic.get("question", "")
            category = topic.get("category", "")

            # Skip if already researched
            sanitized = name.lower().replace(" ", "-")[:80]
            output_path = research_dir / f"{sanitized}.md"
            if output_path.exists():
                print(f"  {_dim('skip')} {name} (already researched)")
                continue

            print(f"  {_cyan('researching')} {name} ({category})...")

            prompt = (
                f"# Research: {name}\n\n"
                f"## Question\n{question}\n\n"
                f"## Instructions\n"
                f"Write a thorough, concrete research document.\n\n"
                f"If this involves content creation, create the ACTUAL content "
                f"(names, stats, descriptions), not just guidelines.\n\n"
                f"If this involves finding APIs or tools, list specific options "
                f"with URLs, pricing, rate limits, and code examples.\n\n"
                f"If this involves parody/spoof content, research the source "
                f"material to understand what works, then design ORIGINAL "
                f"alternatives. Never use real brand names or copyrighted content.\n\n"
                f"Output as markdown."
            )

            research_result = self.cli.execute_prompt(prompt, self.project_root)
            if research_result["success"] and research_result.get("output"):
                content = (
                    f"# Research: {name}\n\n"
                    f"*Auto-generated by AIDLC planning session*\n\n"
                    f"**Question:** {question}\n\n---\n\n"
                    f"{research_result['output']}"
                )
                output_path.write_text(content)
                print(f"  {_green('+')} docs/research/{sanitized}.md")
            else:
                print(f"  {_yellow('!')} research failed for {name}")

    def _generate_docs(self, answers: dict) -> dict[str, str]:
        """Generate doc drafts from wizard answers."""
        # Build existing context from audit/scan data if available
        existing_context = self._get_existing_context()

        brain_dump = answers.get("brain_dump", "")
        template_vars = {
            "project_name": answers.get("project_name", ""),
            "one_liner": answers.get("one_liner", brain_dump[:200] if brain_dump else ""),
            "project_type": answers.get("tech_stack", "unknown"),
            "tech_stack": answers.get("tech_stack", ""),
            "target_audience": "(extract from brain dump)",
            "mvp_definition": "(extract from brain dump)",
            "constraints": "(extract from brain dump)",
            "inspiration": "(extract from brain dump)",
            "core_features": brain_dump or "(no description provided — work from existing docs)",
            "phases": "(derive from brain dump and project scope)",
            "existing_context": existing_context,
        }

        docs_to_generate = {
            "ROADMAP.md": ROADMAP_GENERATION_PROMPT,
            "ARCHITECTURE.md": ARCHITECTURE_GENERATION_PROMPT,
            "DESIGN.md": DESIGN_GENERATION_PROMPT,
            "CLAUDE.md": CLAUDE_MD_GENERATION_PROMPT,
        }

        drafts = {}
        for doc_name, prompt_template in docs_to_generate.items():
            # Skip if doc already exists and has substantial content
            existing = self.project_root / doc_name
            if existing.exists():
                content = existing.read_text(errors="replace")
                if len(content) > 500 and "{" not in content[:200]:
                    # Has real content (not just template placeholders)
                    print(f"  {_dim('skip')} {doc_name} (already exists with content)")
                    continue

            print(f"  {_cyan('generating')} {doc_name}...")
            prompt = prompt_template.format(**template_vars)
            result = self.cli.execute_prompt(prompt, self.project_root)

            if result["success"] and result.get("output"):
                drafts[doc_name] = result["output"].strip()
            else:
                self.logger.warning(f"Could not generate {doc_name}")

        return drafts

    def _save_drafts(self, drafts: dict[str, str]):
        """Write drafts to repo root with backups."""
        # Create backup directory
        self.session_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_dir = self.session_dir / timestamp
        backup_dir.mkdir(exist_ok=True)

        for doc_name, content in drafts.items():
            doc_path = self.project_root / doc_name

            # Backup existing file if present
            if doc_path.exists():
                shutil.copy2(doc_path, backup_dir / doc_name)

            # Write new content
            doc_path.write_text(content)

            # Also save to backup
            (backup_dir / f"{doc_name}.generated").write_text(content)

    def _launch_refinement(self, answers: dict):
        """Launch Claude CLI interactively for doc refinement."""
        system_prompt = REFINEMENT_SYSTEM_PROMPT.format(
            project_name=answers.get("project_name", "Project"),
        )

        # Write system prompt to temp file for --append-system-prompt
        prompt_file = self.session_dir / "refinement_prompt.txt"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(system_prompt)

        # Build initial message summarizing what was generated
        initial_msg = (
            f"I just generated project documentation for {answers.get('project_name', 'your project')}. "
            f"The following docs are ready for your review and refinement:\n\n"
        )
        for doc in ["ROADMAP.md", "ARCHITECTURE.md", "DESIGN.md", "CLAUDE.md"]:
            if (self.project_root / doc).exists():
                initial_msg += f"- {doc}\n"

        research_dir = self.project_root / "docs" / "research"
        if research_dir.exists():
            research_files = list(research_dir.glob("*.md"))
            if research_files:
                initial_msg += f"\nResearch docs ({len(research_files)} files) in docs/research/\n"

        initial_msg += (
            "\nWhat would you like to refine? You can ask me to:\n"
            "- Expand specific phases or features\n"
            "- Add missing systems or components\n"
            "- Review the docs for gaps\n"
            "- Design specific content (items, levels, characters)\n"
            "- Research technical approaches\n"
        )

        # Launch claude interactively
        cmd = [
            self.config.get("claude_cli_command", "claude"),
            "--append-system-prompt", system_prompt,
            "--dangerously-skip-permissions",
            initial_msg,
        ]

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.project_root),
                stdin=sys.stdin,
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
        except (KeyboardInterrupt, FileNotFoundError):
            pass

    def _get_existing_context(self) -> str:
        """Get context from existing repo (audit data, scan results)."""
        parts = []

        # Check for audit result
        audit_path = self.project_root / ".aidlc" / "audit_result.json"
        if audit_path.exists():
            try:
                data = json.loads(audit_path.read_text())
                parts.append("## Existing Codebase Analysis")
                parts.append(f"- Project type: {data.get('project_type', 'unknown')}")
                parts.append(f"- Source files: {data.get('source_stats', {}).get('total_files', 0)}")
                parts.append(f"- Total lines: {data.get('source_stats', {}).get('total_lines', 0):,}")

                frameworks = data.get("frameworks", [])
                if frameworks:
                    parts.append(f"- Frameworks: {', '.join(frameworks)}")

                modules = data.get("modules", [])
                if modules:
                    parts.append("\nModules:")
                    for m in modules:
                        parts.append(f"  - {m.get('name', '?')} ({m.get('role', '?')}, {m.get('file_count', 0)} files)")
            except (OSError, json.JSONDecodeError):
                pass

        # Check for research docs
        research_dir = self.project_root / "docs" / "research"
        if research_dir.exists():
            research_files = list(research_dir.glob("*.md"))
            if research_files:
                parts.append(f"\n## Research Available ({len(research_files)} docs)")
                for rf in research_files:
                    parts.append(f"- docs/research/{rf.name}")

        return "\n".join(parts) if parts else ""

    def _save_session_answers(self, answers: dict):
        """Save wizard answers for resume."""
        self.session_dir.mkdir(parents=True, exist_ok=True)
        path = self.session_dir / "wizard_answers.json"
        with open(path, "w") as f:
            json.dump(answers, f, indent=2)

    def _load_session_answers(self) -> dict | None:
        """Load wizard answers from previous session."""
        path = self.session_dir / "wizard_answers.json"
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                pass
        return None

    def _run_review(self):
        """Review existing docs and suggest improvements."""
        print(f"  {_bold('Reviewing project documentation...')}")
        print()

        # Collect all existing docs
        doc_content = []
        for doc_name in ["ROADMAP.md", "ARCHITECTURE.md", "DESIGN.md", "CLAUDE.md", "README.md"]:
            path = self.project_root / doc_name
            if path.exists():
                content = path.read_text(errors="replace")
                doc_content.append(f"### {doc_name}\n```\n{content[:5000]}\n```")
                print(f"  {_green('v')} {doc_name} ({len(content):,} chars)")
            else:
                print(f"  {_yellow('-')} {doc_name} (missing)")

        if not doc_content:
            print(f"\n  No docs found. Run {_cyan('aidlc plan')} to create them.")
            return

        prompt = (
            "Review these project documents and provide a structured assessment:\n\n"
            + "\n\n".join(doc_content) +
            "\n\n## Review Criteria\n"
            "1. **Completeness** — are all necessary sections filled in?\n"
            "2. **Specificity** — are items concrete enough to create issues from?\n"
            "3. **Gaps** — what's missing that should be there?\n"
            "4. **Consistency** — do the docs agree with each other?\n"
            "5. **Research needs** — what topics need deeper investigation?\n\n"
            "Provide specific, actionable suggestions. Format as markdown."
        )

        print(f"\n  {_cyan('Analyzing...')}")
        result = self.cli.execute_prompt(prompt, self.project_root)

        if result["success"] and result.get("output"):
            review_path = self.project_root / "docs" / "audits" / "doc-review.md"
            review_path.parent.mkdir(parents=True, exist_ok=True)
            review_path.write_text(
                f"# Documentation Review\n\n"
                f"*Auto-generated by AIDLC on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}*\n\n"
                f"{result['output']}"
            )
            print(f"\n  {_green('+')} Review saved to docs/audits/doc-review.md")
            print(f"\n{result['output'][:2000]}")
            if len(result['output']) > 2000:
                print(f"\n  {_dim('... (full review in docs/audits/doc-review.md)')}")
        else:
            print(f"  {_yellow('!')} Review failed: {result.get('error')}")
