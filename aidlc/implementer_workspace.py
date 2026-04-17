"""Git change detection, autosync commits, and .aidlc artifact pruning."""

import shutil
import subprocess
from pathlib import Path

from .timing import add_console_time


def get_changed_files(
    project_root: Path,
    state,
    logger,
    with_status: bool = False,
) -> list[str] | tuple[list[str], bool]:
    """List files changed in the working tree (unstaged + staged) via git."""
    import time

    detection_ok = True
    proc = None
    t0 = time.time()
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        detection_ok = False
        logger.warning(f"Unable to run git diff for change detection: {e}")
    finally:
        add_console_time(state, t0)
    if proc and proc.returncode == 0 and proc.stdout.strip():
        files = [f.strip() for f in proc.stdout.strip().split("\n") if f.strip()]
        return (files, True) if with_status else files
    proc = None
    t0 = time.time()
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        detection_ok = False
        logger.warning(f"Unable to run git ls-files for change detection: {e}")
    finally:
        add_console_time(state, t0)
    if proc and proc.returncode == 0 and proc.stdout.strip():
        files = [f.strip() for f in proc.stdout.strip().split("\n") if f.strip()]
        return (files, True) if with_status else files
    return ([], detection_ok) if with_status else []


def git_has_changes(project_root: Path, state, logger) -> bool:
    import time

    t0 = time.time()
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=15,
        )
        return proc.returncode == 0 and bool((proc.stdout or "").strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    finally:
        add_console_time(state, t0)


def git_current_branch(project_root: Path, state, logger) -> str | None:
    import time

    t0 = time.time()
    try:
        proc = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            branch = (proc.stdout or "").strip()
            return branch or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    finally:
        add_console_time(state, t0)
    return None


def git_commit_cycle_snapshot(
    project_root: Path,
    cycle_num: int,
    logger,
    state,
    commit_message_template: str,
) -> bool:
    import time

    if not git_has_changes(project_root, state, logger):
        logger.info("Autosync: no git changes to commit.")
        return False

    t0 = time.time()
    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

        commit_message = commit_message_template.format(cycle=cycle_num)
        commit = subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if commit.returncode != 0:
            stderr_text = (commit.stderr or "").strip().lower()
            stdout_text = (commit.stdout or "").strip().lower()
            if "nothing to commit" in stderr_text or "nothing to commit" in stdout_text:
                logger.info("Autosync: nothing to commit after staging.")
                return False
            logger.warning(
                "Autosync commit failed: "
                f"{(commit.stderr or commit.stdout or 'unknown git error').strip()}"
            )
            return False

        logger.info(f"Autosync commit created at cycle {cycle_num}.")
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.CalledProcessError) as e:
        logger.warning(f"Autosync commit error: {e}")
        return False
    finally:
        add_console_time(state, t0)


def git_push_current_branch(project_root: Path, logger, state) -> bool:
    import time

    branch = git_current_branch(project_root, state, logger)
    if not branch:
        logger.warning("Autosync push skipped: could not determine current branch.")
        return False

    t0 = time.time()
    try:
        push = subprocess.run(
            ["git", "push"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=90,
        )
        if push.returncode == 0:
            logger.info(f"Autosync pushed to remote on branch '{branch}'.")
            return True

        stderr_text = (push.stderr or "").lower()
        if "no upstream" in stderr_text or "set-upstream" in stderr_text:
            push_upstream = subprocess.run(
                ["git", "push", "-u", "origin", branch],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=90,
            )
            if push_upstream.returncode == 0:
                logger.info(f"Autosync pushed and set upstream origin/{branch}.")
                return True
            logger.warning(
                "Autosync push with upstream failed: "
                f"{(push_upstream.stderr or push_upstream.stdout or 'unknown git error').strip()}"
            )
            return False

        logger.warning(
            f"Autosync push failed: {(push.stderr or push.stdout or 'unknown git error').strip()}"
        )
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning(f"Autosync push error: {e}")
        return False
    finally:
        add_console_time(state, t0)


def prune_aidlc_data(
    project_root: Path,
    run_dir: Path,
    state,
    logger,
    runs_to_keep: int,
    keep_claude_outputs: int,
) -> None:
    """Prune stale .aidlc run artifacts while keeping current and most recent history."""
    aidlc_dir = project_root / ".aidlc"
    runs_dir = aidlc_dir / "runs"
    reports_dir = aidlc_dir / "reports"

    if runs_dir.exists() and runs_dir.is_dir():
        run_dirs = [p for p in runs_dir.iterdir() if p.is_dir()]
        run_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        keep_ids = {state.run_id}
        for p in run_dirs:
            if len(keep_ids) >= runs_to_keep:
                break
            keep_ids.add(p.name)

        for run_path in run_dirs:
            if run_path.name in keep_ids:
                continue
            try:
                shutil.rmtree(run_path)
                logger.info(f"Pruned old run cache: {run_path.name}")
            except OSError as e:
                logger.warning(f"Failed to prune run cache {run_path.name}: {e}")

    if reports_dir.exists() and reports_dir.is_dir():
        for report_path in reports_dir.iterdir():
            if not report_path.is_dir():
                continue
            if report_path.name == state.run_id:
                continue
            if runs_dir.exists() and (runs_dir / report_path.name).exists():
                continue
            try:
                shutil.rmtree(report_path)
                logger.info(f"Pruned old report cache: {report_path.name}")
            except OSError as e:
                logger.warning(f"Failed to prune report cache {report_path.name}: {e}")

    outputs_dir = run_dir / "claude_outputs"
    if outputs_dir.exists() and outputs_dir.is_dir():
        outputs = [p for p in outputs_dir.iterdir() if p.is_file()]
        outputs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for old in outputs[keep_claude_outputs:]:
            try:
                old.unlink()
            except OSError:
                pass
