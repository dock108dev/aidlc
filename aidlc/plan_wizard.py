"""Interactive wizard for AIDLC planning sessions.

Keeps it simple: get the project name, auto-detect what we can,
then let the user brain dump everything else in one shot.
"""

import sys
from pathlib import Path


# ANSI helpers
def _bold(t): return f"\033[1m{t}\033[0m" if sys.stdout.isatty() else t
def _dim(t): return f"\033[2m{t}\033[0m" if sys.stdout.isatty() else t
def _cyan(t): return f"\033[36m{t}\033[0m" if sys.stdout.isatty() else t
def _green(t): return f"\033[32m{t}\033[0m" if sys.stdout.isatty() else t


def run_wizard(project_root: Path, auto_detect: bool = True) -> dict:
    """Run the planning wizard. Quick basics then brain dump.

    Returns dict with: project_name, tech_stack, brain_dump, and any auto-detected info.
    """
    answers = {}

    # Auto-detect what we can
    defaults = _auto_detect(project_root) if auto_detect else {}
    answers.update(defaults)

    print(f"  {_bold('Project Planning')}")
    print()

    # 1. Project name (auto-detect from dir)
    default_name = defaults.get("project_name", project_root.name)
    print(f"  Project name? {_dim(f'[{default_name}]')}")
    try:
        name = input("  > ").strip()
    except (EOFError, KeyboardInterrupt):
        name = ""
    answers["project_name"] = name or default_name

    # 2. Show what was auto-detected
    if defaults.get("tech_stack"):
        print(f"\n  {_green('+')} Detected: {defaults['tech_stack']}")
    if defaults.get("has_code"):
        print(f"  {_green('+')} Existing codebase found")

    # 3. Brain dump — the main event
    print(f"\n  {_bold('Tell me everything.')}")
    print(f"  {_dim('What are you building? What should it do? What matters?')}")
    print(f"  {_dim('Paste a doc, write bullet points, stream of consciousness — all good.')}")
    print(f"  {_dim('Blank line + Enter when done.')}")
    print()

    lines = []
    empty_count = 0
    while True:
        try:
            line = input("  ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.strip() == "":
            empty_count += 1
            if empty_count >= 2:
                break
            lines.append("")
        else:
            empty_count = 0
            lines.append(line)

    answers["brain_dump"] = "\n".join(lines).strip()

    # If brain dump is empty, that's fine — Claude will work from existing docs
    if not answers["brain_dump"]:
        print(f"  {_dim('No input — Claude will work from existing project docs.')}")

    return answers


def _auto_detect(project_root: Path) -> dict:
    """Auto-detect project info from existing repo."""
    defaults = {"project_name": project_root.name}

    # Tech stack
    indicators = {
        "package.json": "JavaScript/TypeScript, Node.js",
        "pyproject.toml": "Python",
        "Cargo.toml": "Rust",
        "go.mod": "Go",
        "Gemfile": "Ruby",
        "project.godot": "Godot, GDScript",
        "pom.xml": "Java",
        "CMakeLists.txt": "C/C++",
        "pubspec.yaml": "Dart/Flutter",
        "Package.swift": "Swift",
    }
    detected_stack = []
    for filename, stack in indicators.items():
        if (project_root / filename).exists():
            detected_stack.append(stack)
    if detected_stack:
        defaults["tech_stack"] = ", ".join(detected_stack)

    # Check if there's existing code
    source_exts = {".py", ".js", ".ts", ".gd", ".rs", ".go", ".java", ".rb", ".swift", ".cpp"}
    for entry in project_root.iterdir():
        if entry.is_dir() and not entry.name.startswith(".") and entry.name not in {
            "node_modules", "venv", ".venv", "dist", "build", "__pycache__"
        }:
            for f in entry.rglob("*"):
                if f.is_file() and f.suffix in source_exts:
                    defaults["has_code"] = True
                    break
            if defaults.get("has_code"):
                break

    # One-liner from README
    readme = project_root / "README.md"
    if readme.exists():
        try:
            for line in readme.read_text(errors="replace").split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and len(line) > 20:
                    defaults["one_liner"] = line[:200]
                    break
        except OSError:
            pass

    return defaults
