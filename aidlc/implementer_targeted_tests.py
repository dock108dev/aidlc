"""Narrow implementation test commands when the full suite is known project-wide unstable."""

from __future__ import annotations

import re
from pathlib import Path


def _normalize_res_path(project_root: Path, f: str) -> str:
    s = (f or "").replace("\\", "/").strip()
    if not s:
        return ""
    if s.startswith("res://"):
        return s
    rel = s.lstrip("/")
    try:
        (project_root / rel).resolve()
    except OSError:
        pass
    return "res://" + rel


def collect_gut_paths_from_changes(project_root: Path, files_changed: list[str]) -> list[str]:
    """Pick Godot/GUT-style test paths from files touched in this issue."""
    seen: dict[str, None] = {}
    for raw in files_changed or []:
        s = (raw or "").replace("\\", "/").strip()
        if not s.endswith(".gd"):
            continue
        low = s.lower()
        if "test_" not in low and "/gut/" not in low and "/tests/" not in low:
            continue
        rp = _normalize_res_path(project_root, s)
        if rp:
            seen[rp] = None
    return list(seen.keys())


_DEFAULT_SIBLING_EXPANSION_CAP = 8


def expand_same_directory_gut_tests(
    project_root: Path,
    gut_paths: list[str],
    *,
    cap: int = _DEFAULT_SIBLING_EXPANSION_CAP,
) -> list[str]:
    """Add sibling ``test_*.gd`` in the same directory as any listed path.

    The intent is "lightweight deps" — if a test changes, also run other
    tests in the same directory that may share fixtures. In flat test
    directories (e.g. ``tests/gut/`` with 20+ files) this expansion blows
    the gtest list into the entire suite, which then times out and burns
    money. When the expansion would exceed ``cap`` total paths we fall
    back to the explicitly-changed paths and skip the expansion.
    """
    out: dict[str, None] = {p: None for p in gut_paths}
    for res in gut_paths:
        rel = res.replace("res://", "").strip("/")
        parent = (project_root / rel).parent
        if not parent.is_dir():
            continue
        for pth in sorted(parent.glob("test_*.gd")):
            try:
                rel2 = pth.relative_to(project_root)
                out[f"res://{rel2.as_posix()}"] = None
            except ValueError:
                continue
    if len(out) > cap:
        # Too many siblings — running them all would time out. Keep the
        # explicitly-changed paths and drop the expansion.
        return list(gut_paths)
    return list(out.keys())


def strip_gtest_argument(cmd: str) -> str:
    """Remove existing -gtest=... from a shell command (best-effort)."""
    s = (cmd or "").strip()
    if not s:
        return s
    s = re.sub(r"\s+-gtest=\S+", "", s)
    return s.strip()


def build_automatic_targeted_command(
    project_root: Path,
    base_cmd: str,
    files_changed: list[str],
    *,
    sibling_expansion_cap: int = _DEFAULT_SIBLING_EXPANSION_CAP,
) -> str | None:
    """If base looks like GUT cmdln and we have test paths, return cmd with -gtest=... only."""
    base = (base_cmd or "").strip()
    if not base or not files_changed:
        return None
    low = base.lower()
    if "gut" not in low and "-gtest" not in low:
        return None
    paths = collect_gut_paths_from_changes(project_root, files_changed)
    paths = expand_same_directory_gut_tests(project_root, paths, cap=sibling_expansion_cap)
    if len(paths) < 1:
        return None
    joined = ",".join(paths)
    cleaned = strip_gtest_argument(base)
    return f"{cleaned} -gtest={joined}"


def effective_implementation_test_command(
    project_root: Path,
    base_cmd: str | None,
    files_changed: list[str] | None,
    *,
    project_wide_tests_unstable: bool,
    config: dict,
) -> str:
    """Full suite command, or a targeted one when the run knows the gate is unstable."""
    base = (base_cmd or "").strip()
    if not base:
        return ""
    if not project_wide_tests_unstable:
        return base
    if not bool(config.get("implementation_use_targeted_tests_when_suite_unstable", True)):
        return base

    cap = max(
        1,
        int(
            config.get(
                "implementation_targeted_test_sibling_expansion_cap",
                _DEFAULT_SIBLING_EXPANSION_CAP,
            )
            or _DEFAULT_SIBLING_EXPANSION_CAP
        ),
    )

    tmpl = config.get("implementation_targeted_test_command")
    if isinstance(tmpl, str) and tmpl.strip():
        paths = expand_same_directory_gut_tests(
            project_root,
            collect_gut_paths_from_changes(project_root, files_changed or []),
            cap=cap,
        )
        joined = ",".join(paths) if paths else ""
        try:
            return tmpl.format(gtest_paths=joined, paths=joined).strip() or base
        except (KeyError, ValueError):
            return base

    auto = build_automatic_targeted_command(
        project_root,
        base,
        list(files_changed or []),
        sibling_expansion_cap=cap,
    )
    return auto if auto else base
