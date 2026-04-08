"""Tests for aidlc.auditor module."""

import json
import pytest
from pathlib import Path

from aidlc.auditor import CodeAuditor
from aidlc.audit_models import AuditResult, ModuleInfo, TechDebtItem, TestCoverageInfo, AuditConflict


@pytest.fixture
def config():
    return {
        "audit_source_extensions": [".py", ".js", ".ts"],
        "audit_exclude_patterns": [],
        "audit_max_claude_calls": 10,
        "audit_max_source_chars_per_module": 15000,
    }


@pytest.fixture
def python_project(tmp_path):
    """Create a Python project structure for testing."""
    # Project config
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\n\n'
        "[project.dependencies]\n"
        '"fastapi>=0.100"\n'
        '"sqlalchemy>=2.0"\n'
    )
    (tmp_path / "README.md").write_text("# My App\nA web application.")

    # Source modules
    app = tmp_path / "app"
    app.mkdir()
    (app / "__init__.py").write_text("")
    (app / "__main__.py").write_text('if __name__ == "__main__":\n    print("hello")')
    (app / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")

    api = tmp_path / "api"
    api.mkdir()
    (api / "__init__.py").write_text("")
    (api / "routes.py").write_text("# API routes\n# TODO: add auth\ndef get_users(): pass\n")
    (api / "models.py").write_text("class User:\n    pass\n")

    models = tmp_path / "models"
    models.mkdir()
    (models / "__init__.py").write_text("")
    (models / "user.py").write_text("class UserModel:\n    pass\n")

    # Tests
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text("def test_hello():\n    assert True\n")
    (tests / "conftest.py").write_text("import pytest\n")

    # Config files
    (tmp_path / "conftest.py").write_text("# root conftest")

    # .aidlc dir
    (tmp_path / ".aidlc").mkdir()

    return tmp_path


@pytest.fixture
def js_project(tmp_path):
    """Create a JavaScript project structure."""
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "myapp",
        "main": "src/index.js",
        "scripts": {"start": "node src/index.js"},
        "dependencies": {"express": "^4.18.0", "mongoose": "^7.0.0"},
        "devDependencies": {"jest": "^29.0.0"},
    }))
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.js").write_text("const express = require('express');\n")
    (src / "app.js").write_text("// FIXME: handle errors\nmodule.exports = {};\n")

    (tmp_path / ".aidlc").mkdir()
    return tmp_path


class TestCodeAuditorQuickScan:
    def test_detect_project_type(self, python_project, config):
        auditor = CodeAuditor(python_project, config)
        result = auditor.run(depth="quick")
        assert "python" in result.project_type

    def test_detect_frameworks(self, python_project, config):
        auditor = CodeAuditor(python_project, config)
        result = auditor.run(depth="quick")
        fw_names = [f.lower() for f in result.frameworks]
        assert any("fastapi" in f for f in fw_names)
        assert any("sqlalchemy" in f for f in fw_names)

    def test_detect_js_frameworks(self, js_project, config):
        auditor = CodeAuditor(js_project, config)
        result = auditor.run(depth="quick")
        fw_names = [f.lower() for f in result.frameworks]
        assert any("express" in f for f in fw_names)

    def test_find_entry_points(self, python_project, config):
        auditor = CodeAuditor(python_project, config)
        result = auditor.run(depth="quick")
        # Should find app/__main__.py
        assert any("__main__" in ep for ep in result.entry_points)

    def test_find_js_entry_points(self, js_project, config):
        auditor = CodeAuditor(js_project, config)
        result = auditor.run(depth="quick")
        assert any("index.js" in ep for ep in result.entry_points)

    def test_list_modules(self, python_project, config):
        auditor = CodeAuditor(python_project, config)
        result = auditor.run(depth="quick")
        module_names = [m.name for m in result.modules]
        assert "app" in module_names
        assert "api" in module_names
        assert "models" in module_names

    def test_module_roles(self, python_project, config):
        auditor = CodeAuditor(python_project, config)
        result = auditor.run(depth="quick")
        roles = {m.name: m.role for m in result.modules}
        assert roles.get("api") == "api"
        assert roles.get("models") == "models"
        assert roles.get("tests") == "tests"

    def test_source_stats(self, python_project, config):
        auditor = CodeAuditor(python_project, config)
        result = auditor.run(depth="quick")
        assert result.source_stats["total_files"] > 0
        assert result.source_stats["total_lines"] > 0
        assert ".py" in result.source_stats["by_extension"]

    def test_directory_tree(self, python_project, config):
        auditor = CodeAuditor(python_project, config)
        result = auditor.run(depth="quick")
        assert "app/" in result.directory_tree
        assert "api/" in result.directory_tree

    def test_tech_debt_detection(self, python_project, config):
        auditor = CodeAuditor(python_project, config)
        result = auditor.run(depth="quick")
        assert result.tech_debt is not None
        # Should find the TODO in api/routes.py
        todo_items = [t for t in result.tech_debt if t.type == "todo"]
        assert len(todo_items) > 0

    def test_test_coverage_assessment(self, python_project, config):
        auditor = CodeAuditor(python_project, config)
        result = auditor.run(depth="quick")
        assert result.test_coverage is not None
        assert result.test_coverage.test_files > 0
        assert result.test_coverage.test_framework == "pytest"

    def test_generates_status_md(self, python_project, config):
        auditor = CodeAuditor(python_project, config)
        result = auditor.run(depth="quick")
        assert "STATUS.md" in result.generated_docs
        status_path = python_project / "STATUS.md"
        assert status_path.exists()
        content = status_path.read_text()
        assert "Project Status" in content
        assert "python" in content.lower()

    def test_generates_architecture_md_when_missing(self, python_project, config):
        auditor = CodeAuditor(python_project, config)
        result = auditor.run(depth="quick")
        assert "ARCHITECTURE.md" in result.generated_docs
        arch_path = python_project / "ARCHITECTURE.md"
        assert arch_path.exists()

    def test_skips_architecture_md_when_exists(self, python_project, config):
        (python_project / "ARCHITECTURE.md").write_text("# My Architecture\nCustom content.")
        auditor = CodeAuditor(python_project, config)
        result = auditor.run(depth="quick")
        assert "ARCHITECTURE.md" not in result.generated_docs
        # Should not overwrite
        content = (python_project / "ARCHITECTURE.md").read_text()
        assert "Custom content" in content

    def test_saves_audit_json(self, python_project, config):
        auditor = CodeAuditor(python_project, config)
        auditor.run(depth="quick")
        json_path = python_project / ".aidlc" / "audit_result.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["depth"] == "quick"
        assert data["project_type"] != "unknown"
        assert "degraded_stats" in data

    def test_empty_project(self, tmp_path, config):
        (tmp_path / ".aidlc").mkdir()
        auditor = CodeAuditor(tmp_path, config)
        result = auditor.run(depth="quick")
        assert result.project_type == "unknown"
        assert result.modules == []
        assert result.source_stats["total_files"] == 0


class TestConflictDetection:
    def test_no_conflicts_when_no_user_docs(self, python_project, config):
        auditor = CodeAuditor(python_project, config)
        result = auditor.run(depth="quick")
        # No pre-existing ARCHITECTURE.md, so no conflicts from that
        error_conflicts = [c for c in result.conflicts if c.severity == "error"]
        assert len(error_conflicts) == 0

    def test_conflict_on_project_type_mismatch(self, python_project, config):
        # Write an ARCHITECTURE.md that says it's a Java project
        (python_project / "ARCHITECTURE.md").write_text(
            "# Architecture\nThis is a Java application using Spring Boot."
        )
        auditor = CodeAuditor(python_project, config)
        result = auditor.run(depth="quick")
        type_conflicts = [c for c in result.conflicts if c.field == "project_type"]
        assert len(type_conflicts) > 0

    def test_conflict_on_missing_module(self, python_project, config):
        (python_project / "ARCHITECTURE.md").write_text(
            "# Architecture\nThe `billing/` module handles payments."
        )
        auditor = CodeAuditor(python_project, config)
        result = auditor.run(depth="quick")
        missing = [c for c in result.conflicts if c.field == "missing_module"]
        assert len(missing) > 0
        assert any("billing" in c.audit_value for c in missing)

    def test_conflicts_file_written(self, python_project, config):
        (python_project / "ARCHITECTURE.md").write_text(
            "# Architecture\nThis is a Java project."
        )
        auditor = CodeAuditor(python_project, config)
        auditor.run(depth="quick")
        conflicts_path = python_project / ".aidlc" / "CONFLICTS.md"
        assert conflicts_path.exists()
        content = conflicts_path.read_text()
        assert "Audit Conflicts" in content


class TestAuditModels:
    def test_audit_result_serialization(self):
        result = AuditResult(
            depth="quick",
            project_type="python",
            frameworks=["FastAPI"],
            entry_points=["main.py"],
            modules=[ModuleInfo(name="app", path="app", file_count=5, line_count=200, role="services")],
            directory_tree="app/\n  main.py",
            source_stats={"total_files": 5, "total_lines": 200},
            tech_debt=[TechDebtItem(file="app/main.py", line=10, type="todo", text="TODO: fix this")],
            test_coverage=TestCoverageInfo(test_files=2, test_functions=5, source_files=5, estimated_coverage="moderate"),
            conflicts=[AuditConflict(doc_path="ARCH.md", field="type", audit_value="python", user_value="java")],
        )
        d = result.to_dict()
        restored = AuditResult.from_dict(d)
        assert restored.depth == "quick"
        assert restored.project_type == "python"
        assert len(restored.modules) == 1
        assert restored.modules[0].name == "app"
        assert len(restored.tech_debt) == 1
        assert restored.test_coverage.estimated_coverage == "moderate"
        assert len(restored.conflicts) == 1

    def test_module_info_serialization(self):
        m = ModuleInfo(name="api", path="src/api", file_count=3, line_count=100, role="api", key_files=["routes.py"])
        d = m.to_dict()
        restored = ModuleInfo.from_dict(d)
        assert restored.name == "api"
        assert restored.key_files == ["routes.py"]

    def test_tech_debt_item_serialization(self):
        t = TechDebtItem(file="main.py", line=42, type="fixme", text="FIXME: broken")
        d = t.to_dict()
        restored = TechDebtItem.from_dict(d)
        assert restored.line == 42
        assert restored.type == "fixme"
