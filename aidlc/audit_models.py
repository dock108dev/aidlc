"""Data models for code audit results."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModuleInfo:
    """Information about a source code module/package."""
    name: str
    path: str
    file_count: int = 0
    line_count: int = 0
    role: str = "unknown"  # api, models, services, tests, config, cli, unknown
    key_files: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "file_count": self.file_count,
            "line_count": self.line_count,
            "role": self.role,
            "key_files": self.key_files,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ModuleInfo":
        return cls(
            name=data["name"],
            path=data["path"],
            file_count=data.get("file_count", 0),
            line_count=data.get("line_count", 0),
            role=data.get("role", "unknown"),
            key_files=data.get("key_files", []),
        )


@dataclass
class TechDebtItem:
    """A tech debt indicator found in source code."""
    file: str
    line: int
    type: str  # todo, fixme, deprecated, large_file, hack
    text: str

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "type": self.type,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TechDebtItem":
        return cls(
            file=data["file"],
            line=data.get("line", 0),
            type=data.get("type", "todo"),
            text=data.get("text", ""),
        )


@dataclass
class TestCoverageInfo:
    """Assessment of test coverage in the project."""
    test_files: int = 0
    test_functions: int = 0
    source_files: int = 0
    estimated_coverage: str = "none"  # none, low, moderate, high
    test_framework: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "test_files": self.test_files,
            "test_functions": self.test_functions,
            "source_files": self.source_files,
            "estimated_coverage": self.estimated_coverage,
            "test_framework": self.test_framework,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TestCoverageInfo":
        return cls(
            test_files=data.get("test_files", 0),
            test_functions=data.get("test_functions", 0),
            source_files=data.get("source_files", 0),
            estimated_coverage=data.get("estimated_coverage", "none"),
            test_framework=data.get("test_framework"),
        )


@dataclass
class AuditConflict:
    """A conflict between audit findings and user-provided documentation."""
    doc_path: str
    field: str
    audit_value: str
    user_value: str
    severity: str = "warning"  # warning, error

    def to_dict(self) -> dict:
        return {
            "doc_path": self.doc_path,
            "field": self.field,
            "audit_value": self.audit_value,
            "user_value": self.user_value,
            "severity": self.severity,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AuditConflict":
        return cls(
            doc_path=data["doc_path"],
            field=data["field"],
            audit_value=data.get("audit_value", ""),
            user_value=data.get("user_value", ""),
            severity=data.get("severity", "warning"),
        )


@dataclass
class AuditResult:
    """Complete result of a code audit."""
    depth: str = "quick"  # quick, full
    project_type: str = "unknown"
    frameworks: list = field(default_factory=list)
    entry_points: list = field(default_factory=list)
    modules: list = field(default_factory=list)  # list of ModuleInfo
    directory_tree: str = ""
    source_stats: dict = field(default_factory=dict)

    # Full audit only (None when quick)
    features: Optional[list] = None
    test_coverage: Optional[TestCoverageInfo] = None
    tech_debt: Optional[list] = None  # list of TechDebtItem

    # Conflicts and output tracking
    conflicts: list = field(default_factory=list)  # list of AuditConflict
    generated_docs: list = field(default_factory=list)  # paths of generated docs
    degraded_stats: dict = field(default_factory=dict)  # counters for skipped/failed reads

    def to_dict(self) -> dict:
        return {
            "depth": self.depth,
            "project_type": self.project_type,
            "frameworks": self.frameworks,
            "entry_points": self.entry_points,
            "modules": [m.to_dict() if isinstance(m, ModuleInfo) else m for m in self.modules],
            "directory_tree": self.directory_tree,
            "source_stats": self.source_stats,
            "features": self.features,
            "test_coverage": self.test_coverage.to_dict() if self.test_coverage else None,
            "tech_debt": [t.to_dict() if isinstance(t, TechDebtItem) else t for t in (self.tech_debt or [])],
            "conflicts": [c.to_dict() if isinstance(c, AuditConflict) else c for c in self.conflicts],
            "generated_docs": self.generated_docs,
            "degraded_stats": self.degraded_stats,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AuditResult":
        result = cls(
            depth=data.get("depth", "quick"),
            project_type=data.get("project_type", "unknown"),
            frameworks=data.get("frameworks", []),
            entry_points=data.get("entry_points", []),
            directory_tree=data.get("directory_tree", ""),
            source_stats=data.get("source_stats", {}),
            features=data.get("features"),
            generated_docs=data.get("generated_docs", []),
            degraded_stats=data.get("degraded_stats", {}),
        )
        result.modules = [
            ModuleInfo.from_dict(m) if isinstance(m, dict) else m
            for m in data.get("modules", [])
        ]
        tc = data.get("test_coverage")
        if tc:
            result.test_coverage = TestCoverageInfo.from_dict(tc) if isinstance(tc, dict) else tc
        result.tech_debt = [
            TechDebtItem.from_dict(t) if isinstance(t, dict) else t
            for t in data.get("tech_debt", [])
        ] if data.get("tech_debt") is not None else None
        result.conflicts = [
            AuditConflict.from_dict(c) if isinstance(c, dict) else c
            for c in data.get("conflicts", [])
        ]
        return result
