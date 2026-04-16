"""Terminal display utilities for CLI output."""

import sys
from pathlib import Path

_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


# Color formatters
def bold(text: str) -> str:
    """Return bold text (or plain if no color support)."""
    return f"\033[1m{text}\033[0m" if _USE_COLOR else text


def green(text: str) -> str:
    """Return green text."""
    return f"\033[32m{text}\033[0m" if _USE_COLOR else text


def yellow(text: str) -> str:
    """Return yellow text."""
    return f"\033[33m{text}\033[0m" if _USE_COLOR else text


def red(text: str) -> str:
    """Return red text."""
    return f"\033[31m{text}\033[0m" if _USE_COLOR else text


def dim(text: str) -> str:
    """Return dimmed text."""
    return f"\033[2m{text}\033[0m" if _USE_COLOR else text


def cyan(text: str) -> str:
    """Return cyan text."""
    return f"\033[36m{text}\033[0m" if _USE_COLOR else text


# Display functions
def print_banner(version: str):
    """Print AIDLC banner with version."""
    print(bold("AIDLC") + dim(f" v{version}") + " — AI Development Life Cycle")
    print()


def get_template_dir() -> Path:
    """Return bundled project_template directory path."""
    pkg_template = Path(__file__).parent.parent / "project_template"
    if pkg_template.exists():
        return pkg_template
    repo_template = Path(__file__).parent.parent.parent / "project_template"
    if repo_template.exists():
        return repo_template
    raise FileNotFoundError("project_template directory not found")


def print_precheck(result, project_root: Path, verbose: bool = False) -> None:
    """Print precheck results to console."""
    from aidlc.precheck import OPTIONAL_DOCS, RECOMMENDED_DOCS, REQUIRED_DOCS

    if result.config_created:
        print(f"  {green('+')} Auto-created {cyan('.aidlc/')} with default config")
        print(f"    Config: {dim(str(project_root / '.aidlc' / 'config.json'))}")
        print("    Edit to set plan_budget_hours, run_tests_command, etc.")
        print()

    if result.has_source_code:
        print(f"  {bold('Project:')} {result.project_type} {dim('(source code detected)')}")
        if "STATUS.md" not in [
            *result.optional_found,
            *result.recommended_found,
            *result.required_found,
        ]:
            print(
                f"    Tip: run {cyan('aidlc audit')} to auto-generate STATUS.md + ARCHITECTURE.md"
            )
    else:
        print(f"  {bold('Project:')} {dim('no source code detected (new project?)')}")
    print()

    print(f"  {bold('Required')}")
    for doc in REQUIRED_DOCS:
        if doc in result.required_found:
            print(f"    {green('v')} {doc}")
        else:
            info = REQUIRED_DOCS[doc]
            print(f"    {red('x')} {doc} — {info['purpose']}")
            for line in info["suggestion"].split("\n"):
                print(f"      {dim(line)}")
    print()

    print(f"  {bold('Recommended')}")
    for doc in RECOMMENDED_DOCS:
        if doc in result.recommended_found:
            print(f"    {green('v')} {doc}")
        else:
            info = RECOMMENDED_DOCS[doc]
            print(f"    {yellow('-')} {doc} — {info['purpose']}")
            if verbose:
                for line in info["suggestion"].split("\n"):
                    print(f"      {dim(line)}")
    print()

    print(f"  {bold('Optional')}")
    for doc in OPTIONAL_DOCS:
        if doc in result.optional_found:
            print(f"    {green('v')} {doc}")
        else:
            info = OPTIONAL_DOCS[doc]
            print(f"    {dim('-')} {doc} — {info['purpose']}")
    print()

    found = sum(
        [len(result.required_found), len(result.recommended_found), len(result.optional_found)]
    )
    total = len(REQUIRED_DOCS) + len(RECOMMENDED_DOCS) + len(OPTIONAL_DOCS)
    score = result.score

    if score == "not ready":
        print(f"  {bold('Readiness:')} {red('NOT READY')} — missing required doc(s)")
        print(
            f"    Create the required files above, then run {cyan('aidlc precheck')} again."
        )
    elif score == "excellent":
        print(f"  {bold('Readiness:')} {green('EXCELLENT')} ({found}/{total} docs) — ready to run")
    elif score == "good":
        print(f"  {bold('Readiness:')} {green('GOOD')} ({found}/{total} docs) — ready to run")
    else:
        print(
            f"  {bold('Readiness:')} {yellow('MINIMAL')} ({found}/{total} docs) — can run, but more docs = better plans"
        )
