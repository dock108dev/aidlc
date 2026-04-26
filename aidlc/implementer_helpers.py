"""Helper functions for Implementer internals."""

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .models import Issue, IssueStatus
from .schemas import (
    IMPLEMENTATION_SCHEMA_DESCRIPTION,
    TEST_FIX_OUTCOME_SCHEMA_DESCRIPTION,
    parse_test_fix_outcome,
)
from .timing import add_console_time

if TYPE_CHECKING:
    from .models import RunState


@dataclass
class FixTestsOutcome:
    """Result of a test-fix attempt after an implementation."""

    tests_now_passing: bool = False
    accepted_pre_existing_debt: bool = False
    follow_up_documentation: str = ""


def _looks_like_pre_existing_unrelated_debt(text: str) -> bool:
    """True when prose (no JSON) describes unrelated / pre-existing suite blockers."""
    low = (text or "").lower()
    if len(low) < 24:
        return False
    if "pre-existing" in low and "unrelated" in low:
        return True
    if "unrelated suite" in low:
        return True
    if "gate" in low and "blocked" in low and ("unrelated" in low or "pre-existing" in low):
        return True
    if "broader" in low and "gate" in low and "blocked" in low:
        return True
    if "focused" in low and "passes" in low and "blocked" in low:
        return True
    return False


def _resolve_follow_up_documentation(
    parsed: dict | None,
    raw_output: str,
    raw_error: str,
    min_chars: int,
    allow_prose_heuristic: bool,
) -> str:
    """Prefer JSON follow_up_documentation; else accept matching prose (models often skip JSON)."""
    doc = ""
    if isinstance(parsed, dict) and parsed.get("failures_are_pre_existing_unrelated") is True:
        doc = str(parsed.get("follow_up_documentation") or "").strip()
    if len(doc) >= min_chars:
        return doc
    if not allow_prose_heuristic:
        return ""
    combined = ((raw_output or "") + "\n" + str(raw_error or "")).strip()
    if len(combined) >= min_chars and _looks_like_pre_existing_unrelated_debt(combined):
        return combined
    return ""


def implementation_instructions(test_command: str | None) -> str:
    """Return implementation instruction block (dense; same rules, fewer tokens)."""
    test_line = f"\n- Tests: `{test_command}` — must pass." if test_command else ""

    return f"""## Instructions — Implementation (v4)

Ship production-ready code; post-run audits apply.

**Must:** Match issue scope; follow repo style; handle errors; add/update tests.{test_line}
**Must not:** Touch unrelated files; break behavior; leave dead code; bare `except`; hardcode secrets.

**Preserve, don't rewrite (ISSUE-007):** If a file/system exists with tests or callers, modify in place and preserve the public surface. Rewriting is a last resort and requires updating every caller in the same change. Before editing a system, list its existing tests in your output. Breaking an out-of-scope test is a regression, not progress — fix or revert.

**Quality:** Files <500 lines; single responsibility; DRY; validate external input; docstrings on public APIs; comments only for non-obvious *why*.

Meet **all** acceptance criteria. End with **only** the JSON block below. If blocked: `success: false` + short `notes`. When editing a system with callers, populate `existing_callers_checked` with `<file:line>` refs you inspected."""


def build_implementation_prompt(impl, issue) -> str:
    """Build prompt: static instructions + schema first (cache-friendly), then volatile context."""
    issue_file = Path(impl.config["_issues_dir"]) / f"{issue.id}.md"
    issue_content = issue_file.read_text() if issue_file.exists() else ""
    previous_notes = issue.implementation_notes or ""

    completed = [
        data for data in impl.state.issues if data.get("status") in ("implemented", "verified")
    ]
    cap_done = max(1, int(impl.config.get("implementation_completed_issues_max", 12)))

    static_sections = [
        implementation_instructions(impl.test_command),
        IMPLEMENTATION_SCHEMA_DESCRIPTION,
    ]

    context_cap = max(1, int(impl.max_impl_context_chars or 12000))
    project_context = impl.project_context
    if len(project_context) > context_cap:
        head = int(context_cap * 0.7)
        tail = max(0, context_cap - head - 140)
        tail_text = project_context[-tail:] if tail else ""
        project_context = "".join(
            [
                project_context[:head],
                "\n\n... [context truncated; read repository files directly when needed] ...\n\n",
                tail_text,
            ]
        )

    volatile_sections = [
        "# Implementation Task\n",
        f"Issue **{issue.id}** — read full spec in `.aidlc/issues/{issue.id}.md` when present.",
        "",
        "## Project Context\n",
        project_context,
        "",
        f"## Issue header: {issue.id} — {issue.title}\n",
        f"- priority: {issue.priority} | labels: {', '.join(issue.labels) if issue.labels else 'none'}",
        f"- dependencies: {', '.join(issue.dependencies) if issue.dependencies else 'none'}",
        "",
    ]

    if issue_content:
        volatile_sections.extend(["### Issue file content\n", issue_content])
    else:
        volatile_sections.extend(
            ["### Description\n", issue.description, "\n### Acceptance Criteria\n"]
        )
        for criterion in issue.acceptance_criteria:
            volatile_sections.append(f"- {criterion}")

    if issue.attempt_count > 1 and not issue_content:
        volatile_sections.extend(
            [
                "\n### Previous attempt notes\n",
                previous_notes,
                "\nAddress failures above.",
            ]
        )

    if completed:
        tail = completed[-cap_done:]
        volatile_sections.append(
            f"\n## Recently completed (last {len(tail)}/{len(completed)}; others on disk)\n"
        )
        for data in tail:
            volatile_sections.append(f"- {data['id']}: {data['title']}")

    # Research awareness: the planner may have written `docs/research/<topic>.md`
    # files during planning. List them by filename so the agent knows to read
    # the relevant ones before designing a change rather than re-deriving content.
    project_root = impl.config.get("_project_root")
    research_dir = Path(project_root) / "docs" / "research" if project_root else None
    if research_dir and research_dir.is_dir():
        research_files = sorted(p.name for p in research_dir.glob("*.md"))
        if research_files:
            cap_research = max(5, int(impl.config.get("implementation_research_index_max", 30)))
            shown = research_files[:cap_research]
            volatile_sections.append(
                f"\n## Available Research ({len(research_files)} file(s) in `docs/research/`)\n"
            )
            volatile_sections.append(
                "If any of these are relevant to this issue's topic, **read them first** — "
                "they contain concrete content/specs the planner researched so the implementer "
                "doesn't re-derive them. Reference filenames in your `notes` if you used them."
            )
            for name in shown:
                volatile_sections.append(f"- `docs/research/{name}`")
            if len(research_files) > cap_research:
                volatile_sections.append(
                    f"- ... and {len(research_files) - cap_research} more (list in `docs/research/`)"
                )

    return "\n\n".join(static_sections + volatile_sections)


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


def ensure_test_deps(
    project_root: Path,
    test_command: str | None,
    logger,
    state: "RunState | None" = None,
) -> None:
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
            if (
                dep_file
                in (
                    "package.json",
                    "package-lock.json",
                    "yarn.lock",
                    "pnpm-lock.yaml",
                )
                and (project_root / "node_modules").exists()
            ):
                continue
            if dep_file in ("requirements.txt", "Gemfile"):
                continue

            logger.info(f"Installing project dependencies: {command}")
            t0 = time.time()
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
            finally:
                if state is not None:
                    add_console_time(state, t0)
            break

    for tool_name, install_cmd in install_commands.items():
        if install_cmd and tool_name in (test_command or ""):
            check_cmd = f"which {tool_name.split()[0]} || npx {tool_name.split()[0]} --version"
            try:
                t0 = time.time()
                try:
                    result = subprocess.run(
                        check_cmd,
                        shell=True,
                        capture_output=True,
                        cwd=str(project_root),
                        timeout=10,
                    )
                finally:
                    if state is not None:
                        add_console_time(state, t0)
                if result.returncode != 0:
                    logger.info(f"Installing test tool: {install_cmd}")
                    t1 = time.time()
                    try:
                        subprocess.run(
                            install_cmd,
                            shell=True,
                            cwd=str(project_root),
                            capture_output=True,
                            timeout=120,
                        )
                    finally:
                        if state is not None:
                            add_console_time(state, t1)
            except (subprocess.TimeoutExpired, OSError):
                logger.warning(f"Unable to verify/install test tool '{tool_name}' automatically.")
            break


def fix_failing_tests(
    impl,
    issue,
    model_override: str | None = None,
    *,
    files_changed: list[str] | None = None,
) -> FixTestsOutcome:
    """Run test-fix prompt, re-run tests, optionally accept documented pre-existing debt."""
    impl.logger.info(f"Attempting to fix failing tests for {issue.id}")
    test_output = impl._run_tests(capture_output=True, files_changed=files_changed)
    fix_prompt = f"""# Fix Failing Tests

Tests are failing after implementing issue {issue.id}: {issue.title}

## Test Output

```
{test_output[:5000]}
```

## Instructions

Prefer fixing the failure so the configured test command passes. Stay within this issue's scope;
only broaden edits when obviously required by the failing output.

If the **only** blockers are **pre-existing, unrelated** suite failures (other files' parse errors,
unrelated integration tests, global gates broken before this issue), do **not** rewrite half the repo —
document them for follow-up instead.

Do not delete or weaken tests to get green unless the test is objectively wrong for this change.

{TEST_FIX_OUTCOME_SCHEMA_DESCRIPTION}
"""
    result = impl.cli.execute_prompt(
        fix_prompt,
        impl.project_root,
        allow_edits=True,
        model_override=model_override,
    )
    impl.state.record_provider_result(result, impl.config, phase="fix_tests")
    if not result.get("success"):
        return FixTestsOutcome()

    impl.state.elapsed_seconds += float(result.get("duration_seconds") or 0)

    if impl._run_tests(files_changed=files_changed):
        return FixTestsOutcome(tests_now_passing=True)

    out = parse_test_fix_outcome(result.get("output") or "")
    cfg = impl.config if isinstance(impl.config, dict) else {}
    if not cfg.get("implementation_accept_pre_existing_suite_failures", True):
        return FixTestsOutcome()

    min_chars = max(10, int(cfg.get("implementation_pre_existing_debt_min_chars", 40) or 40))
    prose_ok = bool(cfg.get("implementation_pre_existing_prose_heuristic", True))
    doc = _resolve_follow_up_documentation(
        out,
        result.get("output") or "",
        str(result.get("error") or ""),
        min_chars,
        prose_ok,
    )
    if doc:
        impl.logger.info(
            f"{issue.id}: fix attempt documents pre-existing/unrelated suite failures; "
            "implementation may be accepted with follow-up notes."
        )
        return FixTestsOutcome(
            tests_now_passing=False,
            accepted_pre_existing_debt=True,
            follow_up_documentation=doc,
        )
    return FixTestsOutcome()


def reopen_transient_failures(state, logger, sync_issue_markdown, force_all: bool = False) -> int:
    """ISSUE-012: reopen failed issues whose cause was transient.

    Auto-runs at the start of each implementation cycle. Reopens issues with
    ``failure_cause`` in ``TRANSIENT_FAILURE_CAUSES`` (token_exhausted, unknown)
    to ``pending`` so they get another shot. Issues with cause ``dependency``
    or ``test_regression`` stay failed for manual review.

    With ``force_all=True`` (the ``--retry-failed`` flag), all failed issues
    are reopened regardless of cause.

    Returns the number of issues reopened.
    """
    from .issue_model import TRANSIENT_FAILURE_CAUSES

    reopened = 0
    for d in list(state.issues):
        if d.get("status") != IssueStatus.FAILED.value:
            continue
        cause = d.get("failure_cause")
        if not force_all and cause not in TRANSIENT_FAILURE_CAUSES:
            continue
        issue = Issue.from_dict(d)
        logger.info(
            f"Reopening {issue.id} (cause={cause or 'none'}) — "
            f"{'forced via --retry-failed' if force_all else 'transient cause auto-reopen'}"
        )
        issue.status = IssueStatus.PENDING
        issue.failure_cause = None
        # attempt_count is preserved so max_attempts still bounds retries.
        state.update_issue(issue)
        sync_issue_markdown(issue)
        reopened += 1
    return reopened


def reopen_stale_verified_issues(
    state,
    logger,
    sync_issue_markdown,
    *,
    enabled: bool,
) -> bool:
    """Re-open verified issues that have no Verification Result body.

    Without this, hydrated ``Status: verified`` rows make ``all_issues_resolved()``
    true and the implementation loop is skipped entirely while work was never
    done. Returns True iff any issue was reopened.
    """
    if not enabled:
        return False
    non_skip = [d for d in state.issues if d.get("status") != IssueStatus.SKIPPED.value]
    if not non_skip:
        return False
    # Only run when *every* non-skipped issue is currently verified — otherwise
    # the planner already has work to do and we shouldn't reshuffle.
    for d in non_skip:
        if d.get("status") != IssueStatus.VERIFIED.value:
            return False
    stale: list[Issue] = []
    for d in non_skip:
        issue = Issue.from_dict(d)
        if not (issue.verification_result or "").strip():
            stale.append(issue)
    if not stale:
        return False
    logger.warning(
        f"{len(stale)} issue(s) are verified but have no Verification Result text; "
        "re-opening as pending so implementation runs "
        "(disable via implementation_reopen_verified_without_result=false)."
    )
    for issue in stale:
        issue.status = IssueStatus.PENDING
        state.update_issue(issue)
        sync_issue_markdown(issue)
    return True


def log_provider_result_for_issue(logger, issue, result: dict) -> None:
    """Log which provider/model handled an implementation call + token counts."""
    provider = str(result.get("provider_id") or "unknown")
    model = str(result.get("model_used") or "unknown")
    routing = result.get("routing_decision") or {}
    requested_model = str(routing.get("model") or model)
    if model != requested_model and model != "unknown":
        logger.info(
            f"{issue.id}: model {provider}/{model} (requested {provider}/{requested_model})"
        )
    else:
        logger.info(f"{issue.id}: model {provider}/{requested_model}")

    usage = result.get("usage")
    if isinstance(usage, dict):
        inp = int(usage.get("input_tokens", 0) or 0)
        out = int(usage.get("output_tokens", 0) or 0)
        cc = int(usage.get("cache_creation_input_tokens", 0) or 0)
        cr = int(usage.get("cache_read_input_tokens", 0) or 0)
        if inp or out or cc or cr:
            logger.info(
                f"  {issue.id}: tokens (this call) in={inp:,} out={out:,} "
                f"cache_write={cc:,} cache_read={cr:,}"
            )
