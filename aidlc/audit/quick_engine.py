"""Quick audit engine (no Claude calls)."""

import json
import os
import re
from pathlib import Path

from ..audit_models import AuditResult, CoverageInfo, ModuleInfo, TechDebtItem
from .constants import (
    ENTRY_POINT_NAMES,
    EXCLUDE_DIRS,
    FRAMEWORK_MAP,
    ROLE_MAP,
    TECH_DEBT_PATTERNS,
)


class QuickAuditEngine:
    """Computes deterministic audit outputs from local source."""

    def __init__(self, auditor):
        self.auditor = auditor

    @property
    def project_root(self):
        return self.auditor.project_root

    @property
    def source_extensions(self):
        return self.auditor.source_extensions

    def quick_scan(self) -> AuditResult:
        """Fast, deterministic scan with no model calls."""
        project_type = self.detect_project_type()
        frameworks = self.detect_frameworks()
        entry_points = self.find_entry_points()
        modules = self.list_modules()
        directory_tree = self.scan_directory_tree()
        source_stats = self.count_source_files()
        tech_debt = self.find_tech_debt_markers()
        test_coverage = self.assess_test_coverage_quick(modules, source_stats)

        return AuditResult(
            depth="quick",
            project_type=project_type,
            frameworks=frameworks,
            entry_points=entry_points,
            modules=modules,
            directory_tree=directory_tree,
            source_stats=source_stats,
            tech_debt=tech_debt if tech_debt else None,
            test_coverage=test_coverage,
        )

    def detect_project_type(self) -> str:
        """Detect project type from indicator files."""
        from ..scanner import PROJECT_INDICATORS

        detected = []
        for filename, ptype in PROJECT_INDICATORS.items():
            if (self.project_root / filename).exists():
                detected.append(ptype)
        return ", ".join(sorted(set(detected))) if detected else "unknown"

    def detect_frameworks(self) -> list[str]:
        """Parse dependency files to detect frameworks."""
        frameworks = []

        pyproject = self.project_root / "pyproject.toml"
        if pyproject.exists():
            frameworks.extend(self.parse_pyproject_deps(pyproject))

        requirements = self.project_root / "requirements.txt"
        if requirements.exists():
            frameworks.extend(self.parse_requirements_deps(requirements))

        package_json = self.project_root / "package.json"
        if package_json.exists():
            frameworks.extend(self.parse_package_json_deps(package_json))

        cargo = self.project_root / "Cargo.toml"
        if cargo.exists():
            frameworks.extend(self.parse_cargo_deps(cargo))

        gomod = self.project_root / "go.mod"
        if gomod.exists():
            frameworks.extend(self.parse_gomod_deps(gomod))

        seen = set()
        unique = []
        for framework in frameworks:
            if framework not in seen:
                seen.add(framework)
                unique.append(framework)
        return unique

    def parse_pyproject_deps(self, path: Path) -> list[str]:
        """Extract dependencies from pyproject.toml."""
        frameworks = []
        try:
            content = path.read_text(errors="replace")
            in_deps = False
            for line in content.splitlines():
                stripped = line.strip()
                if stripped in ("[project.dependencies]", "dependencies = ["):
                    in_deps = True
                    continue
                if in_deps:
                    if stripped.startswith("[") and not stripped.startswith('"'):
                        break
                    if stripped == "]":
                        in_deps = False
                        continue
                    match = re.match(r'["\']?([a-zA-Z0-9_-]+)', stripped)
                    if match:
                        pkg = match.group(1).lower()
                        if pkg in FRAMEWORK_MAP:
                            frameworks.append(FRAMEWORK_MAP[pkg])
        except OSError:
            self.auditor._mark_degraded("dependency_parse_errors")
        return frameworks

    def parse_requirements_deps(self, path: Path) -> list[str]:
        """Extract dependencies from requirements.txt."""
        frameworks = []
        try:
            for line in path.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                match = re.match(r"([a-zA-Z0-9_-]+)", line)
                if match:
                    pkg = match.group(1).lower()
                    if pkg in FRAMEWORK_MAP:
                        frameworks.append(FRAMEWORK_MAP[pkg])
        except OSError:
            self.auditor._mark_degraded("dependency_parse_errors")
        return frameworks

    def parse_package_json_deps(self, path: Path) -> list[str]:
        """Extract dependencies from package.json."""
        frameworks = []
        try:
            data = json.loads(path.read_text(errors="replace"))
            all_deps = {}
            all_deps.update(data.get("dependencies", {}))
            all_deps.update(data.get("devDependencies", {}))
            for pkg in all_deps:
                name = pkg.lstrip("@").split("/")[-1] if "/" in pkg else pkg
                if name.lower() in FRAMEWORK_MAP:
                    frameworks.append(FRAMEWORK_MAP[name.lower()])
                elif pkg.lower() in FRAMEWORK_MAP:
                    frameworks.append(FRAMEWORK_MAP[pkg.lower()])
        except (OSError, json.JSONDecodeError):
            self.auditor._mark_degraded("dependency_parse_errors")
        return frameworks

    def parse_cargo_deps(self, path: Path) -> list[str]:
        """Extract dependencies from Cargo.toml."""
        frameworks = []
        try:
            content = path.read_text(errors="replace")
            in_deps = False
            for line in content.splitlines():
                stripped = line.strip()
                if stripped == "[dependencies]":
                    in_deps = True
                    continue
                if stripped.startswith("[") and in_deps:
                    break
                if in_deps:
                    match = re.match(r"([a-zA-Z0-9_-]+)", stripped)
                    if match:
                        pkg = match.group(1).lower()
                        if pkg in FRAMEWORK_MAP:
                            frameworks.append(FRAMEWORK_MAP[pkg])
        except OSError:
            self.auditor._mark_degraded("dependency_parse_errors")
        return frameworks

    def parse_gomod_deps(self, path: Path) -> list[str]:
        """Extract dependencies from go.mod."""
        frameworks = []
        try:
            content = path.read_text(errors="replace")
            for line in content.splitlines():
                for pattern, name in FRAMEWORK_MAP.items():
                    if "/" in pattern and pattern in line:
                        frameworks.append(name)
        except OSError:
            self.auditor._mark_degraded("dependency_parse_errors")
        return frameworks

    def find_entry_points(self) -> list[str]:
        """Find conventional entry point files."""
        entry_points = []
        for name in ENTRY_POINT_NAMES:
            if (self.project_root / name).exists():
                entry_points.append(name)
            if (self.project_root / "src" / name).exists():
                entry_points.append(f"src/{name}")
            if (self.project_root / "cmd" / name).exists():
                entry_points.append(f"cmd/{name}")

        for directory in self.project_root.iterdir():
            if directory.is_dir() and not directory.name.startswith(".") and directory.name not in EXCLUDE_DIRS:
                main_file = directory / "__main__.py"
                if main_file.exists():
                    rel = str(main_file.relative_to(self.project_root))
                    if rel not in entry_points:
                        entry_points.append(rel)

        pyproject = self.project_root / "pyproject.toml"
        if pyproject.exists():
            try:
                content = pyproject.read_text(errors="replace")
                if "[project.scripts]" in content:
                    in_scripts = False
                    for line in content.splitlines():
                        if "[project.scripts]" in line:
                            in_scripts = True
                            continue
                        if in_scripts:
                            if line.strip().startswith("["):
                                break
                            if "=" in line:
                                entry_points.append(f"pyproject.toml:[project.scripts] {line.strip()}")
            except OSError:
                self.auditor._mark_degraded("dependency_parse_errors")

        pkg_json = self.project_root / "package.json"
        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text(errors="replace"))
                main = data.get("main")
                if main:
                    entry_points.append(f"package.json:main → {main}")
                scripts = data.get("scripts", {})
                if "start" in scripts:
                    entry_points.append(f"package.json:scripts.start → {scripts['start']}")
            except (OSError, json.JSONDecodeError):
                self.auditor._mark_degraded("dependency_parse_errors")

        return entry_points

    def list_modules(self) -> list[ModuleInfo]:
        """List top-level source modules with file counts and role guesses."""
        modules = []
        for entry in sorted(self.project_root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith(".") or entry.name in EXCLUDE_DIRS:
                continue

            source_files = []
            total_lines = 0
            for root, dirs, files in os.walk(entry):
                dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
                for filename in files:
                    ext = os.path.splitext(filename)[1]
                    if ext in self.source_extensions:
                        full = os.path.join(root, filename)
                        rel = os.path.relpath(full, self.project_root)
                        source_files.append(rel)
                        try:
                            total_lines += sum(1 for _ in open(full, errors="replace"))
                        except OSError:
                            self.auditor._mark_degraded("line_count_errors")

            if not source_files:
                continue

            role = ROLE_MAP.get(entry.name.lower(), "unknown")
            key_files = source_files[:5]
            modules.append(
                ModuleInfo(
                    name=entry.name,
                    path=str(entry.relative_to(self.project_root)),
                    file_count=len(source_files),
                    line_count=total_lines,
                    role=role,
                    key_files=key_files,
                )
            )
        return modules

    def scan_directory_tree(self, max_depth: int = 3) -> str:
        """Build a depth-limited directory tree string."""
        lines = []
        self._tree_walk(self.project_root, "", 0, max_depth, lines)
        return "\n".join(lines)

    def _tree_walk(self, path: Path, prefix: str, depth: int, max_depth: int, lines: list) -> None:
        if depth > max_depth:
            return

        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name))
        entries = [
            entry
            for entry in entries
            if not (entry.name.startswith(".") and entry.name not in (".github",))
            and entry.name not in EXCLUDE_DIRS
        ]

        for idx, entry in enumerate(entries):
            is_last = idx == len(entries) - 1
            connector = "└── " if is_last else "├── "
            if entry.is_dir():
                file_count = sum(1 for _ in entry.rglob("*") if _.is_file())
                lines.append(f"{prefix}{connector}{entry.name}/ ({file_count} files)")
                extension = "    " if is_last else "│   "
                self._tree_walk(entry, prefix + extension, depth + 1, max_depth, lines)
            else:
                lines.append(f"{prefix}{connector}{entry.name}")

    def count_source_files(self) -> dict:
        """Count source files by extension."""
        by_ext = {}
        total_files = 0
        total_lines = 0

        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
            for filename in files:
                ext = os.path.splitext(filename)[1]
                if ext in self.source_extensions:
                    total_files += 1
                    by_ext[ext] = by_ext.get(ext, 0) + 1
                    try:
                        total_lines += sum(
                            1 for _ in open(os.path.join(root, filename), errors="replace")
                        )
                    except OSError:
                        self.auditor._mark_degraded("line_count_errors")

        return {
            "total_files": total_files,
            "total_lines": total_lines,
            "by_extension": dict(sorted(by_ext.items(), key=lambda x: -x[1])),
        }

    def find_tech_debt_markers(self) -> list[TechDebtItem]:
        """Find TODO, FIXME, HACK, etc. in source files."""
        items = []
        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
            for filename in files:
                ext = os.path.splitext(filename)[1]
                if ext not in self.source_extensions:
                    continue
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, self.project_root)
                try:
                    for line_num, line in enumerate(open(full_path, errors="replace"), 1):
                        match = TECH_DEBT_PATTERNS.search(line)
                        if match:
                            items.append(
                                TechDebtItem(
                                    file=rel_path,
                                    line=line_num,
                                    type=match.group(1).lower(),
                                    text=line.strip()[:200],
                                )
                            )
                except OSError:
                    self.auditor._mark_degraded("source_read_errors")
                    continue

        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
            for filename in files:
                ext = os.path.splitext(filename)[1]
                if ext not in self.source_extensions:
                    continue
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, self.project_root)
                try:
                    line_count = sum(1 for _ in open(full_path, errors="replace"))
                    if line_count > 500:
                        items.append(
                            TechDebtItem(
                                file=rel_path,
                                line=0,
                                type="large_file",
                                text=f"File has {line_count} lines",
                            )
                        )
                except OSError:
                    self.auditor._mark_degraded("line_count_errors")
                    continue

        return items

    def assess_test_coverage_quick(self, modules: list[ModuleInfo], stats: dict) -> CoverageInfo:
        """Quick heuristic test coverage assessment."""
        test_files = 0
        test_functions = 0
        test_framework = None
        source_files = stats.get("total_files", 0)

        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
            for filename in files:
                ext = os.path.splitext(filename)[1]
                if ext not in self.source_extensions:
                    continue
                name_lower = filename.lower()
                if (
                    name_lower.startswith("test_")
                    or name_lower.endswith("_test" + ext)
                    or name_lower.endswith(".test" + ext)
                    or name_lower.endswith(".spec" + ext)
                ):
                    test_files += 1
                    full_path = os.path.join(root, filename)
                    try:
                        content = open(full_path, errors="replace").read()
                        test_functions += len(
                            re.findall(r"(?:def test_|it\(|test\(|describe\(|@Test)", content)
                        )
                    except OSError:
                        self.auditor._mark_degraded("source_read_errors")

        if (self.project_root / "pytest.ini").exists() or (self.project_root / "conftest.py").exists():
            test_framework = "pytest"
        elif (self.project_root / "jest.config.js").exists() or (
            self.project_root / "jest.config.ts"
        ).exists():
            test_framework = "jest"
        elif (self.project_root / ".mocharc.yml").exists():
            test_framework = "mocha"
        elif (self.project_root / "vitest.config.ts").exists():
            test_framework = "vitest"

        if not test_framework:
            pyproject = self.project_root / "pyproject.toml"
            if pyproject.exists():
                try:
                    content = pyproject.read_text(errors="replace")
                    if "[tool.pytest" in content:
                        test_framework = "pytest"
                except OSError:
                    self.auditor._mark_degraded("doc_read_errors")

        if test_files == 0:
            estimated = "none"
        elif source_files > 0:
            ratio = test_files / max(source_files - test_files, 1)
            if ratio >= 0.5:
                estimated = "high"
            elif ratio >= 0.2:
                estimated = "moderate"
            else:
                estimated = "low"
        else:
            estimated = "none"

        return CoverageInfo(
            test_files=test_files,
            test_functions=test_functions,
            source_files=source_files,
            estimated_coverage=estimated,
            test_framework=test_framework,
        )
