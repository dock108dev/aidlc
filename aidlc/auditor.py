"""Code auditor for existing repositories.

Analyzes existing codebases to generate 'where we are' documentation
before the planning phase. Supports two depths:

- quick: Pure Python analysis (no Claude calls). Directory tree, framework
  detection, entry points, module listing, source stats.
- full: Uses Claude for semantic analysis. Module dependencies, feature
  inventory, test coverage assessment, tech debt detection.
"""

import fnmatch
import json
import logging
import os
import re
from pathlib import Path

from .audit_models import (
    AuditConflict,
    AuditResult,
    ModuleInfo,
    TechDebtItem,
    TestCoverageInfo,
)
from .audit_schemas import (
    AUDIT_FEATURE_INVENTORY_PROMPT,
    AUDIT_MODULE_ANALYSIS_PROMPT,
    parse_audit_feature_output,
    parse_audit_module_output,
)

# Map directory names to likely roles
ROLE_MAP = {
    "api": "api",
    "routes": "api",
    "endpoints": "api",
    "handlers": "api",
    "views": "api",
    "controllers": "api",
    "models": "models",
    "schemas": "models",
    "entities": "models",
    "services": "services",
    "core": "services",
    "lib": "services",
    "utils": "services",
    "helpers": "services",
    "common": "services",
    "shared": "services",
    "tests": "tests",
    "test": "tests",
    "spec": "tests",
    "__tests__": "tests",
    "config": "config",
    "configs": "config",
    "settings": "config",
    "cli": "cli",
    "cmd": "cli",
    "commands": "cli",
    "scripts": "cli",
    "migrations": "config",
    "db": "models",
    "database": "models",
    "middleware": "services",
    "auth": "services",
    "static": "config",
    "templates": "config",
    "public": "config",
    "assets": "config",
}

# Map known packages to framework descriptions
FRAMEWORK_MAP = {
    # Python
    "fastapi": "FastAPI web framework",
    "flask": "Flask web framework",
    "django": "Django web framework",
    "starlette": "Starlette ASGI framework",
    "tornado": "Tornado async framework",
    "aiohttp": "aiohttp async HTTP",
    "sqlalchemy": "SQLAlchemy ORM",
    "alembic": "Alembic migrations",
    "pydantic": "Pydantic data validation",
    "celery": "Celery task queue",
    "redis": "Redis client",
    "pymongo": "MongoDB client",
    "psycopg2": "PostgreSQL client",
    "boto3": "AWS SDK",
    "requests": "HTTP client",
    "httpx": "Async HTTP client",
    "pytest": "pytest testing",
    "numpy": "NumPy numerical computing",
    "pandas": "pandas data analysis",
    "scikit-learn": "scikit-learn ML",
    "tensorflow": "TensorFlow ML",
    "torch": "PyTorch ML",
    "click": "Click CLI framework",
    "typer": "Typer CLI framework",
    # JavaScript/TypeScript
    "react": "React frontend",
    "next": "Next.js framework",
    "vue": "Vue.js frontend",
    "angular": "Angular frontend",
    "express": "Express.js server",
    "fastify": "Fastify server",
    "nestjs": "NestJS framework",
    "prisma": "Prisma ORM",
    "sequelize": "Sequelize ORM",
    "typeorm": "TypeORM",
    "mongoose": "Mongoose MongoDB ODM",
    "jest": "Jest testing",
    "mocha": "Mocha testing",
    "vitest": "Vitest testing",
    "tailwindcss": "Tailwind CSS",
    "webpack": "Webpack bundler",
    "vite": "Vite build tool",
    "eslint": "ESLint linter",
    # Rust
    "actix-web": "Actix-web framework",
    "axum": "Axum web framework",
    "tokio": "Tokio async runtime",
    "serde": "Serde serialization",
    "diesel": "Diesel ORM",
    "sqlx": "SQLx async database",
    # Go (module paths)
    "gin-gonic/gin": "Gin web framework",
    "gorilla/mux": "Gorilla Mux router",
    "gorm.io/gorm": "GORM ORM",
}

# Source file extensions to scan
DEFAULT_SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
    ".java", ".rb", ".swift", ".kt", ".scala", ".c",
    ".cpp", ".h", ".hpp", ".cs", ".php",
}

# Directories to always exclude from scanning
EXCLUDE_DIRS = {
    "node_modules", ".git", "venv", ".venv", "__pycache__",
    ".aidlc", "dist", "build", ".next", ".nuxt", "target",
    "vendor", ".tox", ".mypy_cache", ".pytest_cache",
    "coverage", ".coverage", "htmlcov", "egg-info",
}

# Entry point file patterns
ENTRY_POINT_NAMES = {
    "main.py", "app.py", "__main__.py", "manage.py", "wsgi.py", "asgi.py",
    "index.js", "index.ts", "app.js", "app.ts", "server.js", "server.ts",
    "main.go", "main.rs", "Main.java", "Program.cs",
}

# Patterns that indicate tech debt
TECH_DEBT_PATTERNS = re.compile(
    r"\b(TODO|FIXME|HACK|XXX|DEPRECATED|NOQA|WORKAROUND)\b",
    re.IGNORECASE,
)


class CodeAuditor:
    """Analyzes existing codebases to generate documentation."""

    def __init__(
        self,
        project_root: Path,
        config: dict,
        cli=None,
        logger: logging.Logger | None = None,
    ):
        self.project_root = project_root
        self.config = config
        self.cli = cli  # ClaudeCLI instance, None for quick scan
        self.logger = logger or logging.getLogger(__name__)
        self.source_extensions = set(
            config.get("audit_source_extensions", DEFAULT_SOURCE_EXTENSIONS)
        )
        self.exclude_patterns = config.get("audit_exclude_patterns", [])
        self.max_claude_calls = config.get("audit_max_claude_calls", 10)
        self.max_source_chars = config.get("audit_max_source_chars_per_module", 15000)
        self.degraded_stats = {
            "dependency_parse_errors": 0,
            "source_read_errors": 0,
            "doc_read_errors": 0,
            "line_count_errors": 0,
        }

    def _mark_degraded(self, key: str) -> None:
        self.degraded_stats[key] = self.degraded_stats.get(key, 0) + 1

    def run(self, depth: str = "quick") -> AuditResult:
        """Run the code audit. depth is 'quick' or 'full'."""
        self.logger.info(f"Starting {depth} code audit...")
        result = self._quick_scan()
        result.depth = depth

        if depth == "full" and self.cli:
            self.logger.info("Running full audit with Claude analysis...")
            result = self._full_audit(result)

        self._generate_docs(result)
        result.conflicts = self._detect_conflicts(result)
        result.degraded_stats = dict(self.degraded_stats)

        if result.conflicts:
            self._write_conflicts_file(result.conflicts)

        self._save_audit_json(result)

        self.logger.info(
            f"Audit complete: {len(result.modules)} modules, "
            f"{len(result.frameworks)} frameworks, "
            f"{len(result.entry_points)} entry points"
        )
        degraded_total = sum(result.degraded_stats.values())
        if degraded_total:
            self.logger.warning(
                f"Audit completed with degraded reads: {degraded_total} "
                f"({result.degraded_stats})"
            )
        if result.conflicts:
            self.logger.warning(f"Found {len(result.conflicts)} conflict(s) with existing docs")

        return result

    # --- Quick scan (pure Python, no Claude) ---

    def _quick_scan(self) -> AuditResult:
        """Fast, deterministic scan — no Claude calls."""
        project_type = self._detect_project_type()
        frameworks = self._detect_frameworks()
        entry_points = self._find_entry_points()
        modules = self._list_modules()
        directory_tree = self._scan_directory_tree()
        source_stats = self._count_source_files()
        tech_debt = self._find_tech_debt_markers()
        test_coverage = self._assess_test_coverage_quick(modules, source_stats)

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

    def _detect_project_type(self) -> str:
        """Detect project type from indicator files."""
        from .scanner import PROJECT_INDICATORS
        detected = []
        for filename, ptype in PROJECT_INDICATORS.items():
            if (self.project_root / filename).exists():
                detected.append(ptype)
        return ", ".join(sorted(set(detected))) if detected else "unknown"

    def _detect_frameworks(self) -> list[str]:
        """Parse dependency files to detect frameworks."""
        frameworks = []

        # Python: pyproject.toml
        pyproject = self.project_root / "pyproject.toml"
        if pyproject.exists():
            frameworks.extend(self._parse_pyproject_deps(pyproject))

        # Python: requirements.txt
        requirements = self.project_root / "requirements.txt"
        if requirements.exists():
            frameworks.extend(self._parse_requirements_deps(requirements))

        # JavaScript: package.json
        package_json = self.project_root / "package.json"
        if package_json.exists():
            frameworks.extend(self._parse_package_json_deps(package_json))

        # Rust: Cargo.toml
        cargo = self.project_root / "Cargo.toml"
        if cargo.exists():
            frameworks.extend(self._parse_cargo_deps(cargo))

        # Go: go.mod
        gomod = self.project_root / "go.mod"
        if gomod.exists():
            frameworks.extend(self._parse_gomod_deps(gomod))

        # Deduplicate
        seen = set()
        unique = []
        for f in frameworks:
            if f not in seen:
                seen.add(f)
                unique.append(f)
        return unique

    def _parse_pyproject_deps(self, path: Path) -> list[str]:
        """Extract dependencies from pyproject.toml."""
        frameworks = []
        try:
            content = path.read_text(errors="replace")
            # Simple regex-based parsing to avoid tomllib dependency issues
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
                    # Extract package name from "package>=version" or "package"
                    match = re.match(r'["\']?([a-zA-Z0-9_-]+)', stripped)
                    if match:
                        pkg = match.group(1).lower()
                        if pkg in FRAMEWORK_MAP:
                            frameworks.append(FRAMEWORK_MAP[pkg])
        except OSError:
            self._mark_degraded("dependency_parse_errors")
        return frameworks

    def _parse_requirements_deps(self, path: Path) -> list[str]:
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
            self._mark_degraded("dependency_parse_errors")
        return frameworks

    def _parse_package_json_deps(self, path: Path) -> list[str]:
        """Extract dependencies from package.json."""
        frameworks = []
        try:
            data = json.loads(path.read_text(errors="replace"))
            all_deps = {}
            all_deps.update(data.get("dependencies", {}))
            all_deps.update(data.get("devDependencies", {}))
            for pkg in all_deps:
                # Strip scope prefix
                name = pkg.lstrip("@").split("/")[-1] if "/" in pkg else pkg
                if name.lower() in FRAMEWORK_MAP:
                    frameworks.append(FRAMEWORK_MAP[name.lower()])
                elif pkg.lower() in FRAMEWORK_MAP:
                    frameworks.append(FRAMEWORK_MAP[pkg.lower()])
        except (OSError, json.JSONDecodeError):
            self._mark_degraded("dependency_parse_errors")
        return frameworks

    def _parse_cargo_deps(self, path: Path) -> list[str]:
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
            self._mark_degraded("dependency_parse_errors")
        return frameworks

    def _parse_gomod_deps(self, path: Path) -> list[str]:
        """Extract dependencies from go.mod."""
        frameworks = []
        try:
            content = path.read_text(errors="replace")
            for line in content.splitlines():
                for pattern, name in FRAMEWORK_MAP.items():
                    if "/" in pattern and pattern in line:
                        frameworks.append(name)
        except OSError:
            self._mark_degraded("dependency_parse_errors")
        return frameworks

    def _find_entry_points(self) -> list[str]:
        """Find conventional entry point files."""
        entry_points = []

        # Check well-known entry point names
        for name in ENTRY_POINT_NAMES:
            # Check root
            if (self.project_root / name).exists():
                entry_points.append(name)
            # Check src/
            if (self.project_root / "src" / name).exists():
                entry_points.append(f"src/{name}")
            # Check cmd/ (Go convention)
            if (self.project_root / "cmd" / name).exists():
                entry_points.append(f"cmd/{name}")

        # Check for __main__.py in packages
        for d in self.project_root.iterdir():
            if d.is_dir() and not d.name.startswith(".") and d.name not in EXCLUDE_DIRS:
                main_file = d / "__main__.py"
                if main_file.exists():
                    rel = str(main_file.relative_to(self.project_root))
                    if rel not in entry_points:
                        entry_points.append(rel)

        # Check pyproject.toml for script entry points
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
                self._mark_degraded("dependency_parse_errors")

        # Check package.json for scripts
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
                self._mark_degraded("dependency_parse_errors")

        return entry_points

    def _list_modules(self) -> list[ModuleInfo]:
        """List top-level source modules with file counts and role guesses."""
        modules = []

        # Find source directories
        for entry in sorted(self.project_root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith(".") or entry.name in EXCLUDE_DIRS:
                continue

            # Check if directory contains source files
            source_files = []
            total_lines = 0
            for root, dirs, files in os.walk(entry):
                # Prune excluded dirs
                dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
                for f in files:
                    ext = os.path.splitext(f)[1]
                    if ext in self.source_extensions:
                        full = os.path.join(root, f)
                        rel = os.path.relpath(full, self.project_root)
                        source_files.append(rel)
                        try:
                            total_lines += sum(1 for _ in open(full, errors="replace"))
                        except OSError:
                            self._mark_degraded("line_count_errors")

            if not source_files:
                continue

            # Guess role from directory name
            role = ROLE_MAP.get(entry.name.lower(), "unknown")

            # Find key files (largest or most important)
            key_files = source_files[:5]  # First 5 as representative

            modules.append(ModuleInfo(
                name=entry.name,
                path=str(entry.relative_to(self.project_root)),
                file_count=len(source_files),
                line_count=total_lines,
                role=role,
                key_files=key_files,
            ))

        return modules

    def _scan_directory_tree(self, max_depth: int = 3) -> str:
        """Build a depth-limited directory tree string."""
        lines = []
        self._tree_walk(self.project_root, "", 0, max_depth, lines)
        return "\n".join(lines)

    def _tree_walk(self, path: Path, prefix: str, depth: int, max_depth: int, lines: list):
        """Recursive tree walker."""
        if depth > max_depth:
            return

        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name))
        # Filter out excluded
        entries = [
            e for e in entries
            if not (e.name.startswith(".") and e.name not in (".github",))
            and e.name not in EXCLUDE_DIRS
        ]

        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            if entry.is_dir():
                file_count = sum(1 for _ in entry.rglob("*") if _.is_file())
                lines.append(f"{prefix}{connector}{entry.name}/ ({file_count} files)")
                extension = "    " if is_last else "│   "
                self._tree_walk(entry, prefix + extension, depth + 1, max_depth, lines)
            else:
                lines.append(f"{prefix}{connector}{entry.name}")

    def _count_source_files(self) -> dict:
        """Count source files by extension."""
        by_ext = {}
        total_files = 0
        total_lines = 0

        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
            for f in files:
                ext = os.path.splitext(f)[1]
                if ext in self.source_extensions:
                    total_files += 1
                    by_ext[ext] = by_ext.get(ext, 0) + 1
                    try:
                        total_lines += sum(1 for _ in open(os.path.join(root, f), errors="replace"))
                    except OSError:
                        self._mark_degraded("line_count_errors")

        return {
            "total_files": total_files,
            "total_lines": total_lines,
            "by_extension": dict(sorted(by_ext.items(), key=lambda x: -x[1])),
        }

    def _find_tech_debt_markers(self) -> list[TechDebtItem]:
        """Find TODO, FIXME, HACK, etc. in source files."""
        items = []
        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
            for f in files:
                ext = os.path.splitext(f)[1]
                if ext not in self.source_extensions:
                    continue
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, self.project_root)
                try:
                    for line_num, line in enumerate(open(full_path, errors="replace"), 1):
                        match = TECH_DEBT_PATTERNS.search(line)
                        if match:
                            items.append(TechDebtItem(
                                file=rel_path,
                                line=line_num,
                                type=match.group(1).lower(),
                                text=line.strip()[:200],
                            ))
                except OSError:
                    self._mark_degraded("source_read_errors")
                    continue

        # Also flag large files (>500 lines)
        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
            for f in files:
                ext = os.path.splitext(f)[1]
                if ext not in self.source_extensions:
                    continue
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, self.project_root)
                try:
                    line_count = sum(1 for _ in open(full_path, errors="replace"))
                    if line_count > 500:
                        items.append(TechDebtItem(
                            file=rel_path,
                            line=0,
                            type="large_file",
                            text=f"File has {line_count} lines",
                        ))
                except OSError:
                    self._mark_degraded("line_count_errors")
                    continue

        return items

    def _assess_test_coverage_quick(self, modules: list[ModuleInfo], stats: dict) -> TestCoverageInfo:
        """Quick heuristic test coverage assessment."""
        test_files = 0
        test_functions = 0
        test_framework = None
        source_files = stats.get("total_files", 0)

        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
            for f in files:
                ext = os.path.splitext(f)[1]
                if ext not in self.source_extensions:
                    continue
                name_lower = f.lower()
                if name_lower.startswith("test_") or name_lower.endswith("_test" + ext) or \
                   name_lower.endswith(".test" + ext) or name_lower.endswith(".spec" + ext):
                    test_files += 1
                    full_path = os.path.join(root, f)
                    try:
                        content = open(full_path, errors="replace").read()
                        # Count test functions
                        test_functions += len(re.findall(
                            r"(?:def test_|it\(|test\(|describe\(|@Test)", content
                        ))
                    except OSError:
                        self._mark_degraded("source_read_errors")

        # Detect test framework
        if (self.project_root / "pytest.ini").exists() or \
           (self.project_root / "conftest.py").exists():
            test_framework = "pytest"
        elif (self.project_root / "jest.config.js").exists() or \
             (self.project_root / "jest.config.ts").exists():
            test_framework = "jest"
        elif (self.project_root / ".mocharc.yml").exists():
            test_framework = "mocha"
        elif (self.project_root / "vitest.config.ts").exists():
            test_framework = "vitest"

        # Also check pyproject.toml for pytest
        if not test_framework:
            pyproject = self.project_root / "pyproject.toml"
            if pyproject.exists():
                try:
                    content = pyproject.read_text(errors="replace")
                    if "[tool.pytest" in content:
                        test_framework = "pytest"
                except OSError:
                    self._mark_degraded("doc_read_errors")

        # Estimate coverage level
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

        return TestCoverageInfo(
            test_files=test_files,
            test_functions=test_functions,
            source_files=source_files,
            estimated_coverage=estimated,
            test_framework=test_framework,
        )

    # --- Full audit (uses Claude) ---

    def _full_audit(self, result: AuditResult) -> AuditResult:
        """Enhance quick scan results with Claude-powered semantic analysis."""
        if not self.cli:
            self.logger.warning("No Claude CLI available for full audit, skipping.")
            return result

        claude_calls = 0

        # Analyze modules with Claude
        module_analyses = {}
        for module in result.modules:
            if claude_calls >= self.max_claude_calls:
                self.logger.info(f"Reached max Claude calls ({self.max_claude_calls}), skipping remaining modules")
                break
            if module.role == "tests":
                continue  # Skip test modules for analysis

            analysis = self._analyze_module_with_claude(module)
            if analysis:
                module_analyses[module.name] = analysis
                claude_calls += 1

        # Feature inventory
        if claude_calls < self.max_claude_calls and module_analyses:
            features = self._inventory_features_with_claude(result, module_analyses)
            if features:
                result.features = features
                claude_calls += 1

        return result

    def _analyze_module_with_claude(self, module: ModuleInfo) -> dict | None:
        """Send module source to Claude for semantic analysis."""
        source_content = self._read_module_source(module)
        if not source_content:
            return None

        prompt = AUDIT_MODULE_ANALYSIS_PROMPT.format(
            module_name=module.name,
            module_path=module.path,
            source_content=source_content,
        )

        cli_result = self.cli.execute_prompt(
            prompt=prompt,
            working_dir=self.project_root,
            allow_edits=False,
        )

        if cli_result["success"] and cli_result["output"]:
            try:
                return parse_audit_module_output(cli_result["output"])
            except ValueError as e:
                self.logger.warning(f"Failed to parse module analysis for {module.name}: {e}")
        return None

    def _read_module_source(self, module: ModuleInfo) -> str:
        """Read source files from a module, truncated to max_source_chars."""
        parts = []
        total_chars = 0
        module_path = self.project_root / module.path

        for root, dirs, files in os.walk(module_path):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for f in sorted(files):
                ext = os.path.splitext(f)[1]
                if ext not in self.source_extensions:
                    continue
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, self.project_root)
                try:
                    content = open(full_path, errors="replace").read()
                    if total_chars + len(content) > self.max_source_chars:
                        remaining = self.max_source_chars - total_chars
                        if remaining > 500:
                            parts.append(f"\n--- {rel_path} (truncated) ---\n{content[:remaining]}")
                        break
                    parts.append(f"\n--- {rel_path} ---\n{content}")
                    total_chars += len(content)
                except OSError:
                    self._mark_degraded("source_read_errors")
                    continue

        return "\n".join(parts)

    def _inventory_features_with_claude(self, result: AuditResult, module_analyses: dict) -> list[str] | None:
        """Ask Claude to inventory features based on module analyses."""
        summaries = []
        for name, analysis in module_analyses.items():
            desc = analysis.get("description", "")
            caps = analysis.get("capabilities", [])
            summaries.append(f"- **{name}**: {desc}\n  Capabilities: {', '.join(caps)}")

        prompt = AUDIT_FEATURE_INVENTORY_PROMPT.format(
            project_type=result.project_type,
            frameworks=", ".join(result.frameworks) or "none detected",
            module_summaries="\n".join(summaries),
        )

        cli_result = self.cli.execute_prompt(
            prompt=prompt,
            working_dir=self.project_root,
            allow_edits=False,
        )

        if cli_result["success"] and cli_result["output"]:
            try:
                data = parse_audit_feature_output(cli_result["output"])
                features = data.get("features", [])
                return [
                    f"{f.get('name', '?')} ({f.get('status', '?')}): {f.get('description', '')}"
                    for f in features
                ]
            except ValueError as e:
                self.logger.warning(f"Failed to parse feature inventory: {e}")
        return None

    # --- Output ---

    def _generate_docs(self, result: AuditResult):
        """Write STATUS.md and optionally ARCHITECTURE.md to project root."""
        # Always generate STATUS.md
        status_path = self.project_root / "STATUS.md"
        status_content = self._render_status_doc(result)
        status_path.write_text(status_content)
        result.generated_docs.append("STATUS.md")
        self.logger.info(f"Generated {status_path}")

        # Only generate ARCHITECTURE.md if it doesn't exist
        arch_path = self.project_root / "ARCHITECTURE.md"
        if not arch_path.exists():
            arch_content = self._render_architecture_doc(result)
            arch_path.write_text(arch_content)
            result.generated_docs.append("ARCHITECTURE.md")
            self.logger.info(f"Generated {arch_path}")

    def _render_status_doc(self, result: AuditResult) -> str:
        """Render STATUS.md from audit results."""
        lines = [
            "# Project Status",
            "",
            f"*Auto-generated by AIDLC code audit ({result.depth} scan)*",
            "",
            "## Current State",
            "",
            f"- **Project type**: {result.project_type}",
            f"- **Source files**: {result.source_stats.get('total_files', 0)}",
            f"- **Total lines**: {result.source_stats.get('total_lines', 0):,}",
            "",
        ]

        # Frameworks
        if result.frameworks:
            lines.append("## Frameworks & Dependencies")
            lines.append("")
            for fw in result.frameworks:
                lines.append(f"- {fw}")
            lines.append("")

        # Modules
        if result.modules:
            lines.append("## Modules")
            lines.append("")
            lines.append("| Module | Path | Files | Lines | Role |")
            lines.append("|--------|------|-------|-------|------|")
            for m in result.modules:
                mod = m if isinstance(m, ModuleInfo) else ModuleInfo.from_dict(m)
                lines.append(f"| {mod.name} | {mod.path} | {mod.file_count} | {mod.line_count:,} | {mod.role} |")
            lines.append("")

        # Entry points
        if result.entry_points:
            lines.append("## Entry Points")
            lines.append("")
            for ep in result.entry_points:
                lines.append(f"- `{ep}`")
            lines.append("")

        # Test coverage
        if result.test_coverage:
            tc = result.test_coverage if isinstance(result.test_coverage, TestCoverageInfo) \
                else TestCoverageInfo.from_dict(result.test_coverage)
            lines.append("## Test Coverage")
            lines.append("")
            lines.append(f"- **Test files**: {tc.test_files}")
            lines.append(f"- **Test functions**: {tc.test_functions}")
            lines.append(f"- **Estimated coverage**: {tc.estimated_coverage}")
            if tc.test_framework:
                lines.append(f"- **Framework**: {tc.test_framework}")
            lines.append("")

        # Features (full audit only)
        if result.features:
            lines.append("## Features")
            lines.append("")
            for feat in result.features:
                lines.append(f"- {feat}")
            lines.append("")

        # Tech debt
        if result.tech_debt:
            lines.append("## Known Tech Debt")
            lines.append("")
            debt_by_type = {}
            for item in result.tech_debt:
                td = item if isinstance(item, TechDebtItem) else TechDebtItem.from_dict(item)
                debt_by_type.setdefault(td.type, []).append(td)
            for dtype, items in sorted(debt_by_type.items()):
                lines.append(f"### {dtype.upper()} ({len(items)})")
                for td in items[:10]:  # Cap at 10 per type
                    if td.type == "large_file":
                        lines.append(f"- `{td.file}`: {td.text}")
                    else:
                        lines.append(f"- `{td.file}:{td.line}`: {td.text}")
                if len(items) > 10:
                    lines.append(f"- ... and {len(items) - 10} more")
                lines.append("")

        # Source stats
        by_ext = result.source_stats.get("by_extension", {})
        if by_ext:
            lines.append("## Source File Breakdown")
            lines.append("")
            for ext, count in by_ext.items():
                lines.append(f"- `{ext}`: {count} files")
            lines.append("")

        if result.degraded_stats:
            degraded_total = sum(result.degraded_stats.values())
            if degraded_total:
                lines.append("## Degraded Audit Reads")
                lines.append("")
                lines.append(
                    f"- The audit skipped or degraded {degraded_total} read/parse operation(s)."
                )
                for key, count in sorted(result.degraded_stats.items()):
                    if count:
                        lines.append(f"- `{key}`: {count}")
                lines.append("")

        return "\n".join(lines)

    def _render_architecture_doc(self, result: AuditResult) -> str:
        """Render a skeleton ARCHITECTURE.md from audit results."""
        lines = [
            "# Architecture",
            "",
            f"*Auto-generated by AIDLC code audit ({result.depth} scan)*",
            "",
            "## Overview",
            "",
            f"This is a {result.project_type} project",
        ]
        if result.frameworks:
            lines[-1] += f" using {', '.join(result.frameworks[:5])}"
        lines[-1] += "."
        lines.append("")

        # Directory structure
        if result.directory_tree:
            lines.append("## Directory Structure")
            lines.append("")
            lines.append("```")
            # Limit tree output
            tree_lines = result.directory_tree.split("\n")
            for tl in tree_lines[:50]:
                lines.append(tl)
            if len(tree_lines) > 50:
                lines.append(f"... ({len(tree_lines) - 50} more entries)")
            lines.append("```")
            lines.append("")

        # Components
        if result.modules:
            lines.append("## Components")
            lines.append("")
            for m in result.modules:
                mod = m if isinstance(m, ModuleInfo) else ModuleInfo.from_dict(m)
                lines.append(f"### {mod.name}")
                lines.append(f"- **Path**: `{mod.path}/`")
                lines.append(f"- **Role**: {mod.role}")
                lines.append(f"- **Files**: {mod.file_count} ({mod.line_count:,} lines)")
                if mod.key_files:
                    lines.append(f"- **Key files**: {', '.join(f'`{f}`' for f in mod.key_files[:3])}")
                lines.append("")

        return "\n".join(lines)

    def _detect_conflicts(self, result: AuditResult) -> list[AuditConflict]:
        """Compare audit findings against existing user-provided docs."""
        conflicts = []

        # Check ARCHITECTURE.md for project type mismatches
        arch_path = self.project_root / "ARCHITECTURE.md"
        if arch_path.exists() and "ARCHITECTURE.md" not in result.generated_docs:
            try:
                content = arch_path.read_text(errors="replace").lower()
                # Check if architecture doc mentions a different project type
                audit_types = set(result.project_type.lower().replace(",", " ").split())
                type_keywords = {
                    "python", "javascript", "typescript", "rust", "go", "java",
                    "ruby", "swift", "kotlin", "c++", "c#", "php",
                }
                mentioned_types = type_keywords & set(content.split())
                if mentioned_types and not (mentioned_types & audit_types) and result.project_type != "unknown":
                    conflicts.append(AuditConflict(
                        doc_path="ARCHITECTURE.md",
                        field="project_type",
                        audit_value=result.project_type,
                        user_value=", ".join(sorted(mentioned_types)),
                        severity="error",
                    ))

                # Check for module references that don't exist
                for mod_name in re.findall(r"`(\w+)/`", content):
                    mod_path = self.project_root / mod_name
                    if not mod_path.exists() and mod_name not in EXCLUDE_DIRS:
                        conflicts.append(AuditConflict(
                            doc_path="ARCHITECTURE.md",
                            field="missing_module",
                            audit_value=f"Directory '{mod_name}/' not found in repo",
                            user_value=f"Referenced in ARCHITECTURE.md",
                            severity="warning",
                        ))
            except OSError:
                self._mark_degraded("doc_read_errors")

        # Check for major modules not mentioned in any user doc
        if result.modules:
            user_docs_content = self._read_all_user_docs()
            if user_docs_content:
                for m in result.modules:
                    mod = m if isinstance(m, ModuleInfo) else ModuleInfo.from_dict(m)
                    if mod.file_count >= 5 and mod.role != "tests":
                        if mod.name.lower() not in user_docs_content.lower():
                            conflicts.append(AuditConflict(
                                doc_path="(no doc)",
                                field="undocumented_module",
                                audit_value=f"Module '{mod.name}/' ({mod.file_count} files, role: {mod.role}) exists but is not mentioned in any documentation",
                                user_value="Not referenced",
                                severity="warning",
                            ))

        return conflicts

    def _read_all_user_docs(self) -> str:
        """Read all user-provided markdown docs into a single string for conflict checking."""
        parts = []
        for doc_name in ("README.md", "ARCHITECTURE.md", "ROADMAP.md", "DESIGN.md"):
            doc_path = self.project_root / doc_name
            if doc_path.exists() and doc_name not in getattr(self, '_generated_docs', []):
                try:
                    parts.append(doc_path.read_text(errors="replace"))
                except OSError:
                    self._mark_degraded("doc_read_errors")
        return "\n".join(parts)

    def _write_conflicts_file(self, conflicts: list[AuditConflict]):
        """Write conflicts to .aidlc/CONFLICTS.md for user review."""
        aidlc_dir = self.project_root / ".aidlc"
        aidlc_dir.mkdir(exist_ok=True)
        path = aidlc_dir / "CONFLICTS.md"

        lines = [
            "# Audit Conflicts",
            "",
            "The code audit found the following conflicts with your existing documentation.",
            "Please review and resolve these before planning proceeds.",
            "",
            "After resolving, run `aidlc run --resume` to continue.",
            "",
        ]

        for i, conflict in enumerate(conflicts, 1):
            c = conflict if isinstance(conflict, AuditConflict) else AuditConflict.from_dict(conflict)
            severity_label = "ERROR" if c.severity == "error" else "WARNING"
            lines.append(f"## {i}. [{severity_label}] {c.field}")
            lines.append(f"- **Document**: `{c.doc_path}`")
            lines.append(f"- **Audit found**: {c.audit_value}")
            lines.append(f"- **Doc says**: {c.user_value}")
            lines.append("")

        path.write_text("\n".join(lines))
        self.logger.info(f"Wrote conflicts to {path}")

    def _save_audit_json(self, result: AuditResult):
        """Save machine-readable audit result to .aidlc/audit_result.json."""
        aidlc_dir = self.project_root / ".aidlc"
        aidlc_dir.mkdir(exist_ok=True)
        path = aidlc_dir / "audit_result.json"

        with open(path, "w") as f:
            json.dump(result.to_dict(), f, indent=2)

        self.logger.info(f"Saved audit result to {path}")
