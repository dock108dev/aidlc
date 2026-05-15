"""Git change detection, autosync commits, and .aidlc artifact pruning.

Supports two project layouts:

* **Single repo** — ``project_root`` itself contains ``.git``. All git
  commands run with ``cwd=project_root`` and file paths are returned as
  reported by git.
* **Multi repo** — ``project_root`` is a parent dir whose immediate
  children are independent git repos (e.g. ``sports/sda/.git``,
  ``sports/scroll-down-web/.git``). Each git command is run once per
  sub-repo, file paths are prefixed with the sub-repo's relative
  directory, and operations like commit/push/branch aggregate across
  all sub-repos.

See :mod:`aidlc._git_repos` for the discovery rule.
"""

import shutil
import subprocess
from pathlib import Path

from ._git_repos import discover_repos
from .timing import add_console_time


def _prefix_path(repo: Path, project_root: Path, path: str) -> str:
    """Prefix a git-reported path with the sub-repo dir, if nested."""
    try:
        rel = repo.relative_to(project_root)
    except ValueError:
        return path
    rel_str = str(rel)
    if rel_str in ("", "."):
        return path
    return f"{rel_str}/{path}"


def get_changed_files(
    project_root: Path,
    state,
    logger,
    with_status: bool = False,
) -> list[str] | tuple[list[str], bool]:
    """List files changed in the working tree (unstaged + staged) via git.

    Iterates over every repo discovered under ``project_root``. In a
    nested layout, returned paths are prefixed with the sub-repo's
    directory (e.g. ``sda/foo.py``) so callers can tell which sub-repo
    a file belongs to.
    """
    import time

    repos = discover_repos(Path(project_root))
    detection_ok = True
    aggregated: list[str] = []

    for repo in repos:
        proc = None
        t0 = time.time()
        try:
            proc = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            detection_ok = False
            logger.warning(f"Unable to run git diff for change detection in {repo}: {e}")
        finally:
            add_console_time(state, t0)
        if proc and proc.returncode == 0 and proc.stdout.strip():
            for f in proc.stdout.strip().split("\n"):
                f = f.strip()
                if f:
                    aggregated.append(_prefix_path(repo, Path(project_root), f))
            continue

        proc = None
        t0 = time.time()
        try:
            proc = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            detection_ok = False
            logger.warning(f"Unable to run git ls-files for change detection in {repo}: {e}")
        finally:
            add_console_time(state, t0)
        if proc and proc.returncode == 0 and proc.stdout.strip():
            for f in proc.stdout.strip().split("\n"):
                f = f.strip()
                if f:
                    aggregated.append(_prefix_path(repo, Path(project_root), f))

    return (aggregated, detection_ok) if with_status else aggregated


def git_has_changes(project_root: Path, state, logger) -> bool:
    """True when any discovered repo has uncommitted changes."""
    import time

    for repo in discover_repos(Path(project_root)):
        t0 = time.time()
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=15,
            )
            if proc.returncode == 0 and (proc.stdout or "").strip():
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
        finally:
            add_console_time(state, t0)
    return False


def _branch_for_repo(repo: Path, state, logger) -> str | None:
    import time

    t0 = time.time()
    try:
        proc = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(repo),
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


def git_current_branch(project_root: Path, state, logger) -> str | None:
    """Current branch across all discovered repos.

    Returns the common branch name if every sub-repo agrees, ``None``
    if they diverge or any sub-repo can't be read. Callers that need
    per-repo branches should use the autosync helpers, which look up
    each repo's branch internally.
    """
    branches: set[str] = set()
    for repo in discover_repos(Path(project_root)):
        branch = _branch_for_repo(repo, state, logger)
        if not branch:
            return None
        branches.add(branch)
    if len(branches) == 1:
        return next(iter(branches))
    return None


def git_commit_cycle_snapshot(
    project_root: Path,
    cycle_num: int,
    logger,
    state,
    commit_message_template: str,
) -> bool:
    """Stage and commit dirty changes in every discovered repo.

    Returns ``True`` if at least one repo produced a commit.
    """
    import time

    if not git_has_changes(project_root, state, logger):
        logger.info("Autosync: no git changes to commit.")
        return False

    commit_message = commit_message_template.format(cycle=cycle_num)
    repos = discover_repos(Path(project_root))
    any_committed = False

    for repo in repos:
        # Skip repos that have nothing to commit so we don't error on them.
        t0 = time.time()
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning(f"Autosync status failed for {repo}: {e}")
            continue
        finally:
            add_console_time(state, t0)
        if status.returncode != 0 or not (status.stdout or "").strip():
            continue

        t0 = time.time()
        try:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )

            commit = subprocess.run(
                ["git", "commit", "-m", commit_message],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if commit.returncode != 0:
                stderr_text = (commit.stderr or "").strip().lower()
                stdout_text = (commit.stdout or "").strip().lower()
                if "nothing to commit" in stderr_text or "nothing to commit" in stdout_text:
                    logger.info(f"Autosync: nothing to commit in {repo.name} after staging.")
                    continue
                logger.warning(
                    f"Autosync commit failed in {repo.name}: "
                    f"{(commit.stderr or commit.stdout or 'unknown git error').strip()}"
                )
                continue

            logger.info(f"Autosync commit created in {repo.name} at cycle {cycle_num}.")
            any_committed = True
        except (
            subprocess.TimeoutExpired,
            FileNotFoundError,
            subprocess.CalledProcessError,
        ) as e:
            logger.warning(f"Autosync commit error in {repo.name}: {e}")
            continue
        finally:
            add_console_time(state, t0)

    return any_committed


def _push_repo(repo: Path, logger, state) -> bool:
    import time

    branch = _branch_for_repo(repo, state, logger)
    if not branch:
        logger.warning(f"Autosync push skipped for {repo.name}: could not determine branch.")
        return False

    t0 = time.time()
    try:
        push = subprocess.run(
            ["git", "push"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=90,
        )
        if push.returncode == 0:
            logger.info(f"Autosync pushed {repo.name} on branch '{branch}'.")
            return True

        stderr_text = (push.stderr or "").lower()
        if "no upstream" in stderr_text or "set-upstream" in stderr_text:
            push_upstream = subprocess.run(
                ["git", "push", "-u", "origin", branch],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=90,
            )
            if push_upstream.returncode == 0:
                logger.info(f"Autosync pushed {repo.name} and set upstream origin/{branch}.")
                return True
            logger.warning(
                f"Autosync push with upstream failed for {repo.name}: "
                f"{(push_upstream.stderr or push_upstream.stdout or 'unknown git error').strip()}"
            )
            return False

        logger.warning(
            f"Autosync push failed for {repo.name}: "
            f"{(push.stderr or push.stdout or 'unknown git error').strip()}"
        )
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning(f"Autosync push error for {repo.name}: {e}")
        return False
    finally:
        add_console_time(state, t0)


def git_push_current_branch(project_root: Path, logger, state) -> bool:
    """Push the current branch in every discovered repo.

    Returns ``True`` only if every repo's push succeeded. Each sub-repo
    is pushed on its own current branch, so layouts where the two
    sub-repos sit on different branches still work.
    """
    repos = discover_repos(Path(project_root))
    if not repos:
        return False
    return all(_push_repo(repo, logger, state) for repo in repos)


def prune_aidlc_data(
    project_root: Path,
    run_dir: Path,
    state,
    logger,
    runs_to_keep: int,
    keep_provider_outputs: int,
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

    outputs_dir = run_dir / "provider_outputs"
    if outputs_dir.exists() and outputs_dir.is_dir():
        outputs = [p for p in outputs_dir.iterdir() if p.is_file()]
        outputs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for old in outputs[keep_provider_outputs:]:
            try:
                old.unlink()
            except OSError:
                pass
