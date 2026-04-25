"""Pre-flight readiness check for AIDLC.

The contract is intentionally narrow:

  - ``BRAINDUMP.md`` at the project root (the owner's intent for this cycle).
  - ``.aidlc/`` with a config (auto-created if missing).

Everything else — ARCHITECTURE / DESIGN / CLAUDE / ROADMAP / STATUS / specs /
planning / design — is out. The repo itself is the source of truth for "what
is", and BRAINDUMP is the source of truth for "what next". Scoring readiness
on a 4/11-doc checklist trained users to scaffold ceremony docs the planner
then ignored; the new model says: no BRAINDUMP, no run.
"""

from pathlib import Path

REQUIRED_DOCS = {
    "BRAINDUMP.md": {
        "purpose": "Owner's intent for this cycle — what to verify, enhance, add, refactor, or cut.",
        "suggestion": (
            "Run 'aidlc init' to scaffold one, then write what you want this cycle "
            "to deliver.\n  AIDLC will not run without it."
        ),
    },
}


class PrecheckResult:
    """Result of a precheck run."""

    def __init__(self):
        self.config_created = False
        self.config_existed = True
        self.required_missing: list[str] = []
        self.required_found: list[str] = []
        self.project_type: str = "unknown"
        self.has_source_code: bool = False

    @property
    def ready(self) -> bool:
        """True if all required docs are present."""
        return len(self.required_missing) == 0


def run_precheck(project_root: Path, auto_init: bool = True) -> PrecheckResult:
    """Run pre-flight checks on a project.

    Args:
        project_root: Path to the project directory
        auto_init: If True, create .aidlc/ with defaults when missing

    Returns:
        PrecheckResult with findings
    """
    result = PrecheckResult()
    aidlc_dir = project_root / ".aidlc"

    # Auto-create .aidlc/ if missing
    if not aidlc_dir.exists() and auto_init:
        _auto_init_aidlc(project_root)
        result.config_created = True
        result.config_existed = False
    elif not (aidlc_dir / "config.json").exists() and auto_init:
        _auto_init_aidlc(project_root)
        result.config_created = True
        result.config_existed = False

    # Detect project type
    from .scanner import detect_project_type

    result.project_type = detect_project_type(project_root)

    # Check for source code
    source_extensions = {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".rb",
    }
    for entry in project_root.iterdir():
        if (
            entry.is_dir()
            and entry.name
            not in {
                "node_modules",
                ".git",
                "venv",
                ".venv",
                "__pycache__",
                ".aidlc",
                "dist",
                "build",
            }
            and not entry.name.startswith(".")
        ):
            for f in entry.rglob("*"):
                if f.is_file() and f.suffix in source_extensions:
                    result.has_source_code = True
                    break
            if result.has_source_code:
                break

    # Check required docs
    for doc_name in REQUIRED_DOCS:
        if (project_root / doc_name).exists():
            result.required_found.append(doc_name)
        else:
            result.required_missing.append(doc_name)

    return result


def _auto_init_aidlc(project_root: Path):
    """Create .aidlc/ directory with default config."""
    from .config import write_default_config

    write_default_config(project_root / ".aidlc")
