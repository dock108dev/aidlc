"""State persistence for AIDLC runs."""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from .models import RunState


def _chmod_owner_only(path: Path) -> None:
    """Best-effort owner-only permissions for local state artifacts."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


class RunLock:
    """PID-based lock file to prevent concurrent runs on the same project.

    Uses a lock file at .aidlc/run.lock containing the PID of the owning process.
    Stale locks (where the PID no longer exists) are automatically cleaned up.
    """

    def __init__(self, aidlc_dir: Path):
        self.lock_path = aidlc_dir / "run.lock"

    def acquire(self) -> None:
        """Acquire the run lock. Raises RuntimeError if another run is active."""
        if self.lock_path.exists():
            try:
                content = self.lock_path.read_text().strip()
                pid = int(content.split("\n")[0])
                if self._is_pid_alive(pid):
                    raise RuntimeError(
                        f"Another AIDLC run is active (PID {pid}). "
                        f"If this is stale, delete {self.lock_path}"
                    )
                else:
                    logging.getLogger("aidlc").warning(f"Cleaning up stale lock from PID {pid}")
            except (ValueError, IndexError):
                pass  # Corrupted lock file, overwrite it

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(f"{os.getpid()}\n{datetime.now(timezone.utc).isoformat()}\n")
        _chmod_owner_only(self.lock_path)

    def release(self) -> None:
        """Release the run lock."""
        try:
            if self.lock_path.exists():
                content = self.lock_path.read_text().strip()
                pid = int(content.split("\n")[0])
                if pid == os.getpid():
                    self.lock_path.unlink()
        except (ValueError, IndexError, OSError):
            pass

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        """Check if a process with the given PID is still running."""
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False


def generate_run_id(label: str = "run") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base = label.replace(".json", "").replace(" ", "_")
    return f"{base}_{ts}"


def save_state(state: RunState, run_dir: Path) -> Path:
    state.last_updated = datetime.now(timezone.utc).isoformat()
    state_path = run_dir / "state.json"
    tmp_path = state_path.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(state.to_dict(), f, indent=2)
    os.replace(tmp_path, state_path)
    _chmod_owner_only(state_path)
    return state_path


def load_state(run_dir: Path) -> RunState:
    logger = logging.getLogger("aidlc")
    state_path = run_dir / "state.json"

    if state_path.exists():
        try:
            with open(state_path) as f:
                data = json.load(f)
            return RunState.from_dict(data)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"state.json corrupted ({e}), trying checkpoint recovery")

    cp_dir = run_dir / "checkpoints"
    if cp_dir.exists():
        checkpoints = sorted(cp_dir.glob("checkpoint_*.json"), reverse=True)
        for cp_path in checkpoints:
            try:
                with open(cp_path) as f:
                    data = json.load(f)
                logger.warning(f"Recovered state from {cp_path.name}")
                return RunState.from_dict(data)
            except (json.JSONDecodeError, KeyError):
                continue

    raise FileNotFoundError(f"No valid state file or checkpoint at {run_dir}")


def checkpoint(state: RunState, run_dir: Path) -> None:
    state.checkpoint_count += 1
    cp_dir = run_dir / "checkpoints"
    cp_dir.mkdir(exist_ok=True)
    cp_path = cp_dir / f"checkpoint_{state.checkpoint_count:04d}.json"
    tmp_path = cp_path.with_suffix(".json.tmp")
    state.last_updated = datetime.now(timezone.utc).isoformat()
    with open(tmp_path, "w") as f:
        json.dump(state.to_dict(), f, indent=2)
    os.replace(tmp_path, cp_path)
    _chmod_owner_only(cp_path)
    save_state(state, run_dir)


def save_cycle_snapshot(state: RunState, run_dir: Path, cycle_num: int) -> Path:
    """Save a state snapshot for a specific planning cycle.

    These snapshots allow reverting to the state at the start of any cycle.
    """
    snap_dir = run_dir / "cycle_snapshots"
    snap_dir.mkdir(exist_ok=True)
    snap_path = snap_dir / f"cycle_{cycle_num:04d}.json"
    tmp_path = snap_path.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(state.to_dict(), f, indent=2)
    os.replace(tmp_path, snap_path)
    _chmod_owner_only(snap_path)
    return snap_path


def load_cycle_snapshot(run_dir: Path, cycle_num: int) -> RunState:
    """Load state from a specific cycle snapshot.

    Raises FileNotFoundError if the snapshot doesn't exist.
    """
    snap_path = run_dir / "cycle_snapshots" / f"cycle_{cycle_num:04d}.json"
    if not snap_path.exists():
        available = list_cycle_snapshots(run_dir)
        if available:
            raise FileNotFoundError(
                f"No snapshot for cycle {cycle_num}. "
                f"Available: {', '.join(str(c) for c in available)}"
            )
        else:
            raise FileNotFoundError(f"No cycle snapshots found in {run_dir}")

    with open(snap_path) as f:
        data = json.load(f)
    return RunState.from_dict(data)


def list_cycle_snapshots(run_dir: Path) -> list[int]:
    """List available cycle snapshot numbers."""
    snap_dir = run_dir / "cycle_snapshots"
    if not snap_dir.exists():
        return []
    cycles = []
    for f in sorted(snap_dir.glob("cycle_*.json")):
        try:
            num = int(f.stem.split("_")[1])
            cycles.append(num)
        except (IndexError, ValueError):
            continue
    return cycles


def find_latest_run(runs_dir: Path, config_name: str = "") -> Path | None:
    runs_path = Path(runs_dir)
    if not runs_path.exists():
        return None
    candidates = sorted(
        [d for d in runs_path.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for d in candidates:
        if config_name and not d.name.startswith(config_name):
            continue
        state_path = d / "state.json"
        if state_path.exists():
            return d
    return None
