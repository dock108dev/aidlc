"""Planning wizard for AIDLC — brain dump approach.

User writes everything into BRAINDUMP.md, presses Enter, AIDLC processes it.
"""

import sys
from pathlib import Path


def _bold(t): return f"\033[1m{t}\033[0m" if sys.stdout.isatty() else t
def _dim(t): return f"\033[2m{t}\033[0m" if sys.stdout.isatty() else t
def _cyan(t): return f"\033[36m{t}\033[0m" if sys.stdout.isatty() else t
def _green(t): return f"\033[32m{t}\033[0m" if sys.stdout.isatty() else t
def _yellow(t): return f"\033[33m{t}\033[0m" if sys.stdout.isatty() else t


def run_wizard(project_root: Path, auto_detect: bool = True) -> dict:
    """Prompt user to write BRAINDUMP.md, then read it.

    Returns dict with: project_name, tech_stack, brain_dump, and auto-detected info.
    """
    answers = {}

    # Auto-detect what we can
    if auto_detect:
        answers.update(_auto_detect(project_root))

    braindump_path = project_root / "BRAINDUMP.md"

    # Check if BRAINDUMP.md already exists
    has_existing = braindump_path.exists() and braindump_path.read_text(errors="replace").strip()

    if not has_existing:
        # Create a starter template
        starter = _build_starter(project_root, answers)
        braindump_path.write_text(starter)

    print(f"  {_bold('Brain Dump Time')}")
    print()
    if answers.get("tech_stack"):
        print(f"  {_green('+')} Detected: {answers['tech_stack']}")
    if answers.get("has_code"):
        print(f"  {_green('+')} Existing codebase found")
    print()
    if has_existing:
        size = len(braindump_path.read_text(errors="replace"))
        print(f"  {_green('+')} Found existing {_cyan('BRAINDUMP.md')} ({size:,} chars)")
        print("  Review or update it if needed.")
    else:
        print(f"  Created {_cyan('BRAINDUMP.md')} in your project root.")
        print("  Open it and write everything about what you want to build.")
    print()
    print(f"  {_dim('What is it? What should it do? Features, vibes, inspiration,')}")
    print(f"  {_dim('constraints, phases, whatever — dump it all in there.')}")
    print(f"  {_dim('Markdown, bullet points, stream of consciousness, paste a doc.')}")
    print()

    try:
        input(f"  Press {_bold('Enter')} when BRAINDUMP.md is ready...")
    except (EOFError, KeyboardInterrupt):
        print()
        return answers

    # Read the brain dump
    if braindump_path.exists():
        content = braindump_path.read_text(errors="replace").strip()
        # Strip the starter template comments if user didn't replace them
        content = _strip_starter_comments(content)
        if content:
            print(f"  {_green('+')} Read BRAINDUMP.md ({len(content):,} chars)")
            answers["brain_dump"] = content
        else:
            print(f"  {_yellow('!')} BRAINDUMP.md is empty — Claude will work from existing docs")
    else:
        print(f"  {_yellow('!')} BRAINDUMP.md not found — Claude will work from existing docs")

    answers.setdefault("project_name", project_root.name)
    return answers


def _build_starter(project_root: Path, detected: dict) -> str:
    """Build a starter BRAINDUMP.md with hints."""
    lines = [
        f"# {detected.get('project_name', project_root.name)}",
        "",
        "<!-- WRITE YOUR BRAIN DUMP BELOW -->",
        "<!-- Delete these comments and write whatever you want -->",
        "<!-- Markdown, bullet points, stream of consciousness, paste a whole doc -->",
        "",
    ]

    if detected.get("tech_stack"):
        lines.append(f"**Tech stack:** {detected['tech_stack']}")
        lines.append("")

    if detected.get("one_liner"):
        lines.append(f"> {detected['one_liner']}")
        lines.append("")

    lines.extend([
        "## What am I building?",
        "",
        "",
        "## What should it do?",
        "",
        "",
        "## What matters most?",
        "",
        "",
    ])

    return "\n".join(lines)


def _strip_starter_comments(content: str) -> str:
    """Remove the starter template HTML comments."""
    lines = []
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("<!-- ") and stripped.endswith(" -->"):
            continue
        lines.append(line)
    # Also strip empty sections that were never filled
    result = "\n".join(lines).strip()
    # Remove empty ## sections
    import re
    result = re.sub(r"## .+?\n\n(?=## |\Z)", "", result)
    return result.strip()


def _auto_detect(project_root: Path) -> dict:
    """Auto-detect project info from existing repo."""
    defaults = {"project_name": project_root.name}

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
