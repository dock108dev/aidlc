"""Targeted coverage for aidlc.audit.quick_engine.QuickAuditEngine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from aidlc.audit.constants import DEFAULT_SOURCE_EXTENSIONS
from aidlc.audit.quick_engine import QuickAuditEngine


def _engine(root: Path) -> QuickAuditEngine:
    aud = MagicMock()
    aud.project_root = root
    aud.source_extensions = set(DEFAULT_SOURCE_EXTENSIONS)
    aud._mark_degraded = MagicMock()
    return QuickAuditEngine(aud)


def test_quick_scan_exercises_parsers_and_tree(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"react":"1","@scope/pkg":"1"},'
        '"devDependencies":{"jest":"1"},'
        '"main":"src/index.js",'
        '"scripts":{"start":"node src/index.js"}}'
    )
    (tmp_path / "pyproject.toml").write_text(
        "[project.dependencies]\n"
        'dependencies = [\n  "django",\n]\n'
        "[tool.pytest.ini_options]\n"
        'testpaths = ["tests"]\n'
        "[project.scripts]\n"
        'cli = "pkg:run"\n'
    )
    (tmp_path / "requirements.txt").write_text("flask>=2\n-e ./editable\n# c\n")
    (tmp_path / "Cargo.toml").write_text(
        '[dependencies]\naxum = "0.7"\n\n[features]\nx = []\n'
    )
    (tmp_path / "go.mod").write_text(
        "module example.com/m\nrequire gin-gonic/gin v1.9.0\n"
    )
    (tmp_path / "docker-compose.yml").write_text("version: '3'\n")
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "jest.config.js").write_text("module.exports = {}\n")

    (tmp_path / "main.py").write_text("# TODO: finish entry\nprint(1)\n")
    api_dir = tmp_path / "api"
    api_dir.mkdir()
    (api_dir / "svc.py").write_text("x = 1\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_svc.py").write_text("def test_svc():\n    assert 1\n")
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__main__.py").write_text('if __name__ == "__main__":\n    pass\n')

    big = tmp_path / "huge.py"
    big.write_text("\n".join(f"# line {i}" for i in range(520)))

    eng = _engine(tmp_path)
    result = eng.quick_scan()
    assert result.depth == "quick"
    assert "python" in result.project_type or "docker" in result.project_type
    assert result.frameworks
    assert result.entry_points
    assert result.modules
    assert result.directory_tree
    assert result.source_stats["total_files"] >= 1
    assert result.tech_debt
    assert result.test_coverage is not None


def test_detect_frameworks_duplicate_dedup(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project.dependencies]\ndependencies = [\n  "requests",\n  "requests",\n]\n'
    )
    eng = _engine(tmp_path)
    fw = eng.detect_frameworks()
    assert fw.count("HTTP client") == 1


def test_assess_test_coverage_pyproject_pytest_marker(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (tmp_path / "lib.py").write_text("x=1\n")
    eng = _engine(tmp_path)
    cov = eng.assess_test_coverage_quick([], {"total_files": 1})
    assert cov.test_framework == "pytest"


def test_assess_test_coverage_mocha_vitest_jest(tmp_path: Path):
    (tmp_path / "a.py").write_text("pass\n")
    eng = _engine(tmp_path)
    (tmp_path / ".mocharc.yml").write_text("x: 1\n")
    assert (
        eng.assess_test_coverage_quick([], {"total_files": 1}).test_framework == "mocha"
    )
    (tmp_path / ".mocharc.yml").unlink()
    (tmp_path / "vitest.config.ts").write_text("export default {}\n")
    assert (
        eng.assess_test_coverage_quick([], {"total_files": 1}).test_framework
        == "vitest"
    )
    (tmp_path / "vitest.config.ts").unlink()
    (tmp_path / "jest.config.ts").write_text("export default {}\n")
    assert (
        eng.assess_test_coverage_quick([], {"total_files": 1}).test_framework == "jest"
    )


def test_assess_test_coverage_ratios(tmp_path: Path):
    (tmp_path / "s.py").write_text("x=1\n")
    for i in range(3):
        (tmp_path / f"test_{i}.py").write_text("def test_x():\n  pass\n")
    eng = _engine(tmp_path)
    cov = eng.assess_test_coverage_quick([], {"total_files": 10})
    assert cov.estimated_coverage in ("high", "moderate", "low", "none")


def test_parse_package_json_invalid_marks_degraded(tmp_path: Path):
    (tmp_path / "package.json").write_text("{ not json")
    eng = _engine(tmp_path)
    assert eng.parse_package_json_deps(tmp_path / "package.json") == []
    eng.auditor._mark_degraded.assert_called()


def test_count_source_files_open_error(tmp_path: Path, monkeypatch):
    (tmp_path / "x.py").write_text("a\nb\n")
    eng = _engine(tmp_path)
    real_open = open

    def boom(path, *a, **kw):
        if str(path).endswith("x.py"):
            raise OSError("denied")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", boom)
    stats = eng.count_source_files()
    assert stats["total_lines"] == 0
    eng.auditor._mark_degraded.assert_called()


def test_find_tech_debt_read_error(tmp_path: Path, monkeypatch):
    (tmp_path / "b.py").write_text("# TODO x\n")
    eng = _engine(tmp_path)
    real_open = open

    def boom(path, *a, **kw):
        if str(path).endswith("b.py"):
            raise OSError("denied")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", boom)
    assert eng.find_tech_debt_markers() == []
    eng.auditor._mark_degraded.assert_called()


def test_assess_test_coverage_pyproject_read_error(tmp_path: Path, monkeypatch):
    p = tmp_path / "pyproject.toml"
    p.write_text("[tool.pytest.ini_options]\n")
    eng = _engine(tmp_path)
    real_read = Path.read_text

    def read_text(self, *args, **kwargs):
        if self.resolve() == p.resolve():
            raise OSError("nope")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    cov = eng.assess_test_coverage_quick([], {"total_files": 1})
    assert cov.test_framework is None
    eng.auditor._mark_degraded.assert_called()


def test_tree_max_depth(tmp_path: Path):
    d = tmp_path / "a" / "b" / "c"
    d.mkdir(parents=True)
    (d / "f.py").write_text("1\n")
    eng = _engine(tmp_path)
    tree = eng.scan_directory_tree(max_depth=1)
    assert "a/" in tree
