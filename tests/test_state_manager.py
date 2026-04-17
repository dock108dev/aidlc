"""Tests for aidlc.state_manager module."""

import json
import os
from unittest.mock import patch

import pytest
from aidlc.models import RunPhase, RunState, RunStatus
from aidlc.state_manager import (
    RunLock,
    checkpoint,
    find_latest_run,
    generate_run_id,
    list_cycle_snapshots,
    load_cycle_snapshot,
    load_state,
    save_cycle_snapshot,
    save_state,
)


class TestGenerateRunId:
    def test_format(self):
        run_id = generate_run_id("aidlc")
        assert run_id.startswith("aidlc_")
        parts = run_id.split("_", 1)
        assert len(parts) == 2
        # Timestamp part should be YYYYMMDD_HHMMSS
        assert len(parts[1]) == 15  # 8 + 1 + 6

    def test_custom_label(self):
        run_id = generate_run_id("myrun")
        assert run_id.startswith("myrun_")

    def test_label_sanitization(self):
        run_id = generate_run_id("my config.json")
        assert "my_config" in run_id
        assert ".json" not in run_id


class TestSaveAndLoadState:
    def test_save_creates_file(self, tmp_path):
        state = RunState(run_id="test_001", config_name="default")
        path = save_state(state, tmp_path)
        assert path.exists()
        assert path.name == "state.json"

    def test_save_is_atomic(self, tmp_path):
        """Verify no .tmp file left behind."""
        state = RunState(run_id="test_001", config_name="default")
        save_state(state, tmp_path)
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_save_updates_last_updated(self, tmp_path):
        state = RunState(run_id="test_001", config_name="default")
        assert state.last_updated is None
        save_state(state, tmp_path)
        assert state.last_updated is not None

    @patch("aidlc.state_manager.os.chmod")
    def test_save_restricts_permissions(self, mock_chmod, tmp_path):
        state = RunState(run_id="test_001", config_name="default")
        path = save_state(state, tmp_path)
        mock_chmod.assert_called_once_with(path, 0o600)

    def test_load_roundtrip(self, tmp_path):
        state = RunState(run_id="test_rt", config_name="myconfig")
        state.status = RunStatus.RUNNING
        state.phase = RunPhase.PLANNING
        state.elapsed_seconds = 42.5
        state.issues = [{"id": "ISSUE-001", "title": "T", "status": "pending"}]
        save_state(state, tmp_path)

        loaded = load_state(tmp_path)
        assert loaded.run_id == "test_rt"
        assert loaded.status == RunStatus.RUNNING
        assert loaded.elapsed_seconds == 42.5
        assert len(loaded.issues) == 1

    def test_load_corrupted_falls_back_to_checkpoint(self, tmp_path):
        # Write a valid checkpoint first
        state = RunState(run_id="cp_test", config_name="default")
        state.status = RunStatus.RUNNING
        checkpoint(state, tmp_path)

        # Corrupt the main state.json
        (tmp_path / "state.json").write_text("{invalid json")

        loaded = load_state(tmp_path)
        assert loaded.run_id == "cp_test"

    def test_load_no_state_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_state(tmp_path)

    def test_load_state_raises_when_checkpoints_also_corrupt(self, tmp_path):
        (tmp_path / "state.json").write_text("{bad")
        cp = tmp_path / "checkpoints"
        cp.mkdir()
        (cp / "checkpoint_0001.json").write_text("{bad")
        with pytest.raises(FileNotFoundError):
            load_state(tmp_path)

    def test_load_skips_corrupt_checkpoints(self, tmp_path):
        cp_dir = tmp_path / "checkpoints"
        cp_dir.mkdir(parents=True)
        (cp_dir / "checkpoint_0001.json").write_text("{not json")
        state_ok = RunState(run_id="recovered", config_name="default")
        (cp_dir / "checkpoint_0002.json").write_text(json.dumps(state_ok.to_dict()))
        (tmp_path / "state.json").write_text("{corrupt")
        loaded = load_state(tmp_path)
        assert loaded.run_id == "recovered"


class TestCheckpoint:
    def test_creates_checkpoint_file(self, tmp_path):
        state = RunState(run_id="cp_test", config_name="default")
        checkpoint(state, tmp_path)
        cp_dir = tmp_path / "checkpoints"
        assert cp_dir.exists()
        cps = list(cp_dir.glob("checkpoint_*.json"))
        assert len(cps) == 1
        assert state.checkpoint_count == 1

    def test_increments_count(self, tmp_path):
        state = RunState(run_id="cp_test", config_name="default")
        checkpoint(state, tmp_path)
        checkpoint(state, tmp_path)
        assert state.checkpoint_count == 2
        cps = list((tmp_path / "checkpoints").glob("checkpoint_*.json"))
        assert len(cps) == 2

    def test_checkpoint_is_valid_json(self, tmp_path):
        state = RunState(run_id="cp_test", config_name="default")
        state.issues = [{"id": "ISSUE-001", "title": "T", "status": "pending"}]
        checkpoint(state, tmp_path)
        cp_file = list((tmp_path / "checkpoints").glob("checkpoint_*.json"))[0]
        data = json.loads(cp_file.read_text())
        assert data["run_id"] == "cp_test"

    @patch("aidlc.state_manager.os.chmod")
    def test_checkpoint_restricts_permissions(self, mock_chmod, tmp_path):
        state = RunState(run_id="cp_test", config_name="default")
        checkpoint(state, tmp_path)
        chmod_paths = [call.args[0].name for call in mock_chmod.call_args_list]
        assert "checkpoint_0001.json" in chmod_paths
        assert "state.json" in chmod_paths


class TestFindLatestRun:
    def test_finds_latest(self, tmp_path):
        # Create two run dirs
        run1 = tmp_path / "run_20240101_000000"
        run1.mkdir()
        (run1 / "state.json").write_text("{}")

        run2 = tmp_path / "run_20240102_000000"
        run2.mkdir()
        (run2 / "state.json").write_text("{}")

        result = find_latest_run(tmp_path)
        # Should find one of them (the latest by mtime)
        assert result is not None
        assert result.name in ("run_20240101_000000", "run_20240102_000000")

    def test_no_runs(self, tmp_path):
        assert find_latest_run(tmp_path) is None

    def test_nonexistent_dir(self, tmp_path):
        assert find_latest_run(tmp_path / "nope") is None

    def test_skips_dirs_without_state(self, tmp_path):
        run1 = tmp_path / "run_001"
        run1.mkdir()
        # No state.json
        assert find_latest_run(tmp_path) is None


class TestRunLock:
    def test_acquire_and_release(self, tmp_path):
        lock = RunLock(tmp_path)
        lock.acquire()
        assert lock.lock_path.exists()
        content = lock.lock_path.read_text()
        assert str(os.getpid()) in content
        lock.release()
        assert not lock.lock_path.exists()

    @patch("aidlc.state_manager.os.chmod")
    def test_acquire_restricts_permissions(self, mock_chmod, tmp_path):
        lock = RunLock(tmp_path)
        lock.acquire()
        mock_chmod.assert_called_once_with(lock.lock_path, 0o600)

    def test_context_manager(self, tmp_path):
        with RunLock(tmp_path) as lock:
            assert lock.lock_path.exists()
        assert not lock.lock_path.exists()

    def test_stale_lock_cleaned(self, tmp_path):
        lock_path = tmp_path / "run.lock"
        # Write a lock with a PID that definitely doesn't exist
        lock_path.write_text("999999999\n2024-01-01T00:00:00\n")

        lock = RunLock(tmp_path)
        lock.acquire()  # Should succeed by cleaning stale lock
        assert lock.lock_path.exists()
        content = lock.lock_path.read_text()
        assert str(os.getpid()) in content
        lock.release()

    def test_active_lock_raises(self, tmp_path):
        lock_path = tmp_path / "run.lock"
        # Write a lock with our own PID (which is alive)
        lock_path.write_text(f"{os.getpid()}\n2024-01-01T00:00:00\n")

        lock = RunLock(tmp_path)
        with pytest.raises(RuntimeError, match="Another AIDLC run is active"):
            lock.acquire()

    def test_corrupted_lock_overwritten(self, tmp_path):
        lock_path = tmp_path / "run.lock"
        lock_path.write_text("not a valid pid\n")

        lock = RunLock(tmp_path)
        lock.acquire()  # Should succeed
        lock.release()

    def test_release_ignores_other_pid(self, tmp_path):
        lock_path = tmp_path / "run.lock"
        lock_path.write_text("999999999\n2024-01-01T00:00:00\n")

        lock = RunLock(tmp_path)
        lock.release()  # Should not delete — different PID
        assert lock_path.exists()

    def test_release_swallows_corrupt_lock_file(self, tmp_path):
        lock_path = tmp_path / "run.lock"
        lock_path.write_text("not-a-pid\n")
        RunLock(tmp_path).release()
        assert lock_path.exists()

    @patch("aidlc.state_manager.os.chmod", side_effect=OSError("chmod"))
    def test_chmod_owner_only_swallows_oserror(self, _mock_chmod, tmp_path):
        state = RunState(run_id="c", config_name="default")
        save_state(state, tmp_path)
        assert (tmp_path / "state.json").exists()


class TestCycleSnapshots:
    def test_save_and_load_cycle_snapshot(self, tmp_path):
        state = RunState(run_id="snap", config_name="default")
        save_cycle_snapshot(state, tmp_path, 3)
        loaded = load_cycle_snapshot(tmp_path, 3)
        assert loaded.run_id == "snap"
        assert list_cycle_snapshots(tmp_path) == [3]

    def test_load_cycle_snapshot_missing_shows_available(self, tmp_path):
        state = RunState(run_id="s", config_name="default")
        save_cycle_snapshot(state, tmp_path, 1)
        with pytest.raises(FileNotFoundError, match="Available"):
            load_cycle_snapshot(tmp_path, 99)

    def test_load_cycle_snapshot_missing_no_dir(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="No cycle snapshots"):
            load_cycle_snapshot(tmp_path, 1)

    def test_list_cycle_snapshots_skips_malformed_names(self, tmp_path):
        snap_dir = tmp_path / "cycle_snapshots"
        snap_dir.mkdir()
        (snap_dir / "cycle_0007.json").write_text("{}")
        (snap_dir / "cycle_bad.json").write_text("{}")
        assert list_cycle_snapshots(tmp_path) == [7]


class TestListCycleSnapshotsEmpty:
    def test_list_cycle_snapshots_missing_dir(self, tmp_path):
        assert list_cycle_snapshots(tmp_path) == []


class TestFindLatestRunFilter:
    def test_find_latest_respects_config_name_prefix(self, tmp_path):
        older = tmp_path / "default_20240101_000000"
        older.mkdir()
        (older / "state.json").write_text("{}")

        newer = tmp_path / "other_20240102_000000"
        newer.mkdir()
        (newer / "state.json").write_text("{}")

        found = find_latest_run(tmp_path, config_name="default")
        assert found is not None
        assert found.name.startswith("default")

    def test_find_latest_returns_none_when_prefix_matches_nothing(self, tmp_path):
        d = tmp_path / "solo_20240101"
        d.mkdir()
        (d / "state.json").write_text("{}")
        assert find_latest_run(tmp_path, config_name="nomatch") is None
