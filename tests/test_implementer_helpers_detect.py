"""Tests for implementer_helpers.detect_test_command (filesystem-only)."""

from pathlib import Path

from aidlc.implementer_helpers import detect_test_command


def test_detect_pytest_via_pytest_ini(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    assert detect_test_command(tmp_path) == "python -m pytest"


def test_detect_pytest_via_tests_dir(tmp_path: Path):
    (tmp_path / "setup.py").write_text(
        "from setuptools import setup\nsetup(name='x')\n"
    )
    d = tmp_path / "tests"
    d.mkdir()
    (d / "x.py").write_text("def test_x(): pass\n")
    assert detect_test_command(tmp_path) == "python -m pytest"


def test_detect_npm_test_script(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"scripts":{"test":"jest"}}\n')
    assert detect_test_command(tmp_path) == "npm test"


def test_detect_invalid_package_json_skips(tmp_path: Path):
    (tmp_path / "package.json").write_text("{ not json")
    assert detect_test_command(tmp_path) is None


def test_detect_cargo(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n')
    assert detect_test_command(tmp_path) == "cargo test"


def test_detect_go(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module example.com/x\ngo 1.22\n")
    assert detect_test_command(tmp_path) == "go test ./..."


def test_detect_makefile_test_target(tmp_path: Path):
    (tmp_path / "Makefile").write_text("test:\n\t@echo ok\n")
    assert detect_test_command(tmp_path) == "make test"


def test_detect_ruby_bundle(tmp_path: Path):
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n")
    (tmp_path / "spec").mkdir()
    assert detect_test_command(tmp_path) == "bundle exec rspec"


def test_detect_none_when_unrecognized(tmp_path: Path):
    (tmp_path / "README.md").write_text("hi\n")
    assert detect_test_command(tmp_path) is None
