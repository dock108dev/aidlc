"""Helper functions for Implementer internals."""

import json
import subprocess
from pathlib import Path

from .schemas import IMPLEMENTATION_SCHEMA_DESCRIPTION


def implementation_instructions(test_command: str | None) -> str:
    """Return implementation instruction block."""
    test_instruction = ""
    if test_command:
        test_instruction = f"""
- Run tests with: `{test_command}`
- All tests must pass before marking as complete
- If tests fail, fix the issues"""

    return f"""## Instructions — Implementation

You are implementing this issue. Your goal is to write production-ready code that will
survive automated quality audits after implementation completes.

**Requirements:**
- Implement exactly what the issue describes — no more, no less
- Follow the project's existing code style and patterns
- Write clean, well-structured code
- Add appropriate error handling
- Create or update tests for the changes{test_instruction}
- Do NOT modify files unrelated to this issue
- Do NOT introduce breaking changes to existing functionality

**Code quality standards (enforced by post-implementation audits):**
- **File size**: Keep files under 500 lines. Split into modules if needed.
- **Single responsibility**: Each file/class/function does one thing well.
- **No dead code**: Don't leave commented-out code, unused imports, or stale experiments.
- **No duplicate logic**: Extract shared utilities. Don't copy-paste.
- **Explicit error handling**: No bare excepts, no silent failures, no swallowed errors.
  Log errors with context. Fail loudly on unexpected states.
- **Test coverage**: Write tests alongside implementation. Test the happy path AND
  edge cases. If a test framework exists, use it.
- **No hardcoded secrets**: Use config/environment for API keys, credentials, URLs.
- **Input validation**: Validate at system boundaries. Don't trust external input.
- **Consistent naming**: Follow the project's naming conventions. No abbreviations
  without precedent in the codebase.
- **Documentation**: Add comments only where intent isn't obvious (why, not what).
  Update docstrings for public APIs.

**Acceptance criteria must ALL be met.** Check each one.

After implementation, output the structured JSON result.
If you cannot fully implement the issue, set success to false and explain why in notes."""


def build_implementation_prompt(impl, issue) -> str:
    """Build prompt for implementing an issue."""
    issue_file = Path(impl.config["_issues_dir"]) / f"{issue.id}.md"
    issue_content = issue_file.read_text() if issue_file.exists() else ""

    completed = [
        data
        for data in impl.state.issues
        if data.get("status") in ("implemented", "verified")
    ]

    sections = [
        "# Implementation Task\n",
        f"You are implementing issue **{issue.id}** for this project.",
        "",
        "## Project Context\n",
        impl.project_context[: impl.max_impl_context_chars],
        "",
        f"## Issue: {issue.id} — {issue.title}\n",
        f"**Priority**: {issue.priority}",
        f"**Labels**: {', '.join(issue.labels) if issue.labels else 'none'}",
        f"**Dependencies**: {', '.join(issue.dependencies) if issue.dependencies else 'none'}",
        "",
    ]

    if issue_content:
        sections.extend(["### Full Issue Specification\n", issue_content])
    else:
        sections.extend(["### Description\n", issue.description, "\n### Acceptance Criteria\n"])
        for criterion in issue.acceptance_criteria:
            sections.append(f"- {criterion}")

    if issue.attempt_count > 1:
        sections.extend(
            [
                "\n### Previous Attempt Notes\n",
                issue.implementation_notes,
                "\n**Fix the issues from the previous attempt.**",
            ]
        )

    if completed:
        sections.append(f"\n## Already Implemented ({len(completed)} issues)\n")
        for data in completed[:20]:
            sections.append(f"- {data['id']}: {data['title']}")

    sections.append(implementation_instructions(impl.test_command))
    sections.append(IMPLEMENTATION_SCHEMA_DESCRIPTION)
    return "\n\n".join(sections)


def detect_test_command(project_root: Path) -> str | None:
    """Auto-detect test command for project root."""
    if (project_root / "pyproject.toml").exists() or (project_root / "setup.py").exists():
        if (project_root / "pytest.ini").exists() or (project_root / "conftest.py").exists():
            return "python -m pytest"
        if (project_root / "tests").is_dir() or (project_root / "test").is_dir():
            return "python -m pytest"

    pkg_json = project_root / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text())
            scripts = pkg.get("scripts", {})
            if "test" in scripts:
                return "npm test"
        except (json.JSONDecodeError, OSError):
            pass

    if (project_root / "Cargo.toml").exists():
        return "cargo test"
    if (project_root / "go.mod").exists():
        return "go test ./..."
    if (project_root / "Gemfile").exists() and (project_root / "spec").is_dir():
        return "bundle exec rspec"

    makefile = project_root / "Makefile"
    if makefile.exists():
        try:
            if "test:" in makefile.read_text():
                return "make test"
        except OSError:
            pass
    return None


def ensure_test_deps(project_root: Path, test_command: str | None, logger) -> None:
    """Install or verify test dependencies when safe and possible."""
    install_commands = {
        "pytest": "pip install pytest",
        "jest": "npm install --save-dev jest",
        "vitest": "npm install --save-dev vitest",
        "playwright": "npx playwright install",
        "cypress": "npx cypress install",
        "rspec": "bundle install",
        "cargo test": None,
        "go test": None,
    }
    dep_install = {
        "package.json": "npm install",
        "package-lock.json": "npm ci",
        "yarn.lock": "yarn install",
        "pnpm-lock.yaml": "pnpm install",
        "requirements.txt": "pip install -r requirements.txt",
        "Gemfile": "bundle install",
        "Cargo.toml": None,
        "go.mod": "go mod download",
    }

    for dep_file, command in dep_install.items():
        if command and (project_root / dep_file).exists():
            if dep_file in (
                "package.json",
                "package-lock.json",
                "yarn.lock",
                "pnpm-lock.yaml",
            ) and (project_root / "node_modules").exists():
                continue
            if dep_file in ("requirements.txt", "Gemfile"):
                continue

            logger.info(f"Installing project dependencies: {command}")
            try:
                subprocess.run(
                    command,
                    shell=True,
                    cwd=str(project_root),
                    capture_output=True,
                    timeout=120,
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                logger.warning(f"Dependency install failed: {exc}")
            break

    for tool_name, install_cmd in install_commands.items():
        if install_cmd and tool_name in (test_command or ""):
            check_cmd = f"which {tool_name.split()[0]} || npx {tool_name.split()[0]} --version"
            try:
                result = subprocess.run(
                    check_cmd,
                    shell=True,
                    capture_output=True,
                    cwd=str(project_root),
                    timeout=10,
                )
                if result.returncode != 0:
                    logger.info(f"Installing test tool: {install_cmd}")
                    subprocess.run(
                        install_cmd,
                        shell=True,
                        cwd=str(project_root),
                        capture_output=True,
                        timeout=120,
                    )
            except (subprocess.TimeoutExpired, OSError):
                logger.warning(
                    f"Unable to verify/install test tool '{tool_name}' automatically."
                )
            break


def fix_failing_tests(impl, issue) -> bool:
    """Run test-fix prompt and re-run tests."""
    impl.logger.info(f"Attempting to fix failing tests for {issue.id}")
    test_output = impl._run_tests(capture_output=True)
    fix_prompt = f"""# Fix Failing Tests

Tests are failing after implementing issue {issue.id}: {issue.title}

## Test Output

```
{test_output[:5000]}
```

## Instructions

Fix the failing tests. The implementation should match the acceptance criteria:
{chr(10).join(f'- {ac}' for ac in issue.acceptance_criteria)}

Fix the code or tests so everything passes. Do not remove or skip tests.

{IMPLEMENTATION_SCHEMA_DESCRIPTION}
"""
    result = impl.cli.execute_prompt(fix_prompt, impl.project_root, allow_edits=True)
    if result["success"]:
        impl.state.elapsed_seconds += result.get("duration_seconds", 0)
        return impl._run_tests()
    return False
