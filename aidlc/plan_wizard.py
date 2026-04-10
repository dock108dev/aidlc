"""Interactive wizard for AIDLC planning sessions.

Asks structured questions to capture project identity, scope, and vision.
Supports auto-detection from existing repos.
"""

import sys
from pathlib import Path


# ANSI helpers
def _bold(t): return f"\033[1m{t}\033[0m" if sys.stdout.isatty() else t
def _dim(t): return f"\033[2m{t}\033[0m" if sys.stdout.isatty() else t
def _cyan(t): return f"\033[36m{t}\033[0m" if sys.stdout.isatty() else t
def _green(t): return f"\033[32m{t}\033[0m" if sys.stdout.isatty() else t


WIZARD_QUESTIONS = [
    {
        "key": "project_name",
        "question": "What is your project called?",
        "required": True,
    },
    {
        "key": "one_liner",
        "question": "Describe your project in one sentence:",
        "required": True,
    },
    {
        "key": "project_type",
        "question": "What type of project is this?",
        "choices": [
            "web app", "mobile app", "game", "CLI tool",
            "API/backend", "library/package", "data pipeline", "other",
        ],
    },
    {
        "key": "tech_stack",
        "question": "What's the tech stack? (languages, frameworks, platforms)",
    },
    {
        "key": "core_features",
        "question": "List the core features (one per line, blank line to finish):",
        "multiline": True,
    },
    {
        "key": "target_audience",
        "question": "Who is this for?",
    },
    {
        "key": "mvp_definition",
        "question": "What does the minimum viable version look like?",
    },
    {
        "key": "phases",
        "question": "Describe your development phases (one per line, blank to finish):",
        "multiline": True,
    },
    {
        "key": "constraints",
        "question": "Any constraints? (timeline, dependencies, must-haves, things to avoid)",
    },
    {
        "key": "inspiration",
        "question": "Any references or inspiration? (existing projects, vibes, parody targets)",
        "hint": "For parody/spoof projects, describe what you're riffing on so we can research original alternatives.",
    },
    {
        "key": "research_needs",
        "question": "Anything you need researched? (free APIs, design patterns, tech solutions, existing repos)\nOne per line, blank to finish:",
        "multiline": True,
        "hint": "Examples: 'free weather API', 'how to build a card game engine', 'pixel art style references'",
    },
]


def run_wizard(project_root: Path, auto_detect: bool = True) -> dict:
    """Run the interactive wizard, return answers dict.

    If auto_detect is True, pre-fills answers from existing repo state.
    """
    answers = {}

    # Auto-detect defaults from existing repo
    defaults = {}
    if auto_detect:
        defaults = _auto_detect(project_root)

    print(f"  {_bold('Project Planning Wizard')}")
    print(f"  {_dim('Answer each question. Press Enter to accept defaults [in brackets].')}")
    print()

    for q in WIZARD_QUESTIONS:
        key = q["key"]
        question = q["question"]
        default = defaults.get(key, "")
        required = q.get("required", False)
        choices = q.get("choices")
        multiline = q.get("multiline", False)
        hint = q.get("hint")

        if hint:
            print(f"  {_dim(hint)}")

        if choices:
            answer = _ask_choice(question, choices, default)
        elif multiline:
            answer = _ask_multiline(question, default)
        else:
            answer = _ask_single(question, default, required)

        answers[key] = answer
        print()

    return answers


def _ask_single(question: str, default: str = "", required: bool = False) -> str:
    """Ask a single-line question."""
    if default:
        prompt = f"  {question} {_dim(f'[{default}]')}\n  > "
    else:
        prompt = f"  {question}\n  > "

    while True:
        try:
            answer = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return default or ""
        if not answer and default:
            return default
        if not answer and required:
            print(f"  {_dim('(required)')}")
            continue
        return answer or default


def _ask_choice(question: str, choices: list[str], default: str = "") -> str:
    """Ask a multiple-choice question."""
    print(f"  {question}")
    for i, choice in enumerate(choices, 1):
        marker = f" {_green('*')}" if choice == default else ""
        print(f"    {i}. {choice}{marker}")

    prompt = f"  > {_dim('(number or text) ')}"
    try:
        answer = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default or choices[0]

    if not answer and default:
        return default

    # Try as number
    try:
        idx = int(answer) - 1
        if 0 <= idx < len(choices):
            return choices[idx]
    except ValueError:
        pass

    # Accept raw text
    return answer if answer else (default or choices[0])


def _ask_multiline(question: str, default: str | list = "") -> list[str]:
    """Ask a multi-line question. Returns list of lines."""
    print(f"  {question}")
    if default:
        if isinstance(default, list):
            for d in default:
                print(f"  {_dim(f'  [{d}]')}")
        else:
            print(f"  {_dim(f'  [{default}]')}")

    lines = []
    while True:
        try:
            line = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            break
        lines.append(line)

    if not lines and default:
        return default if isinstance(default, list) else [default]
    return lines


def _auto_detect(project_root: Path) -> dict:
    """Auto-detect defaults from existing repo."""
    defaults = {}

    # Project name from directory
    defaults["project_name"] = project_root.name

    # Tech stack from indicator files
    indicators = {
        "package.json": "JavaScript/TypeScript, Node.js",
        "pyproject.toml": "Python",
        "Cargo.toml": "Rust",
        "go.mod": "Go",
        "Gemfile": "Ruby",
        "project.godot": "Godot, GDScript",
        "pom.xml": "Java",
        "CMakeLists.txt": "C/C++",
    }
    detected_stack = []
    for filename, stack in indicators.items():
        if (project_root / filename).exists():
            detected_stack.append(stack)
    if detected_stack:
        defaults["tech_stack"] = ", ".join(detected_stack)

    # One-liner from README
    readme = project_root / "README.md"
    if readme.exists():
        try:
            content = readme.read_text(errors="replace")
            for line in content.split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and len(line) > 20:
                    defaults["one_liner"] = line[:200]
                    break
        except OSError:
            pass

    # Existing ROADMAP phases
    roadmap = project_root / "ROADMAP.md"
    if roadmap.exists():
        try:
            content = roadmap.read_text(errors="replace")
            import re
            phases = re.findall(r"^##\s+(.+)", content, re.MULTILINE)
            if phases:
                defaults["phases"] = phases
        except OSError:
            pass

    return defaults
