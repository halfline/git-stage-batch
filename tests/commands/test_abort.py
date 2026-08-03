"""Tests for abort command."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from git_stage_batch.commands.abort import command_abort
from git_stage_batch.commands import abort as abort_module
from git_stage_batch.commands.again import command_again
from git_stage_batch.commands.discard import command_discard
from git_stage_batch.commands.start import command_start
from git_stage_batch.exceptions import CommandError
from git_stage_batch.data.session import snapshot_file_if_untracked
from git_stage_batch.utils.file_io import (
    append_file_path_to_file,
    write_file_paths_file,
)
from git_stage_batch.utils.paths import (
    get_abort_intent_to_add_entries_file_path,
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
    get_auto_added_files_file_path,
    get_state_directory_path,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestCommandAbort:
    """Tests for abort command."""

    def test_abort_no_session(self, temp_git_repo):
        """Test abort when no session exists."""
        # Should error when no abort state exists
        with pytest.raises(CommandError) as exc_info:
            command_abort()

        assert "No session to abort" in exc_info.value.message

    def test_abort_restores_working_tree(self, temp_git_repo):
        """Test that abort restores working tree state from session start."""
        # Create a file with uncommitted changes
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nUncommitted change\n")

        # Start session (this saves the uncommitted state in stash)
        command_start()

        # Make more changes and discard them
        readme.write_text("# Test\nAnother change\n")
        command_again()
        command_discard()

        # File should be back to original committed state
        assert readme.read_text() == "# Test\n"

        # Abort should restore the uncommitted changes from session start
        command_abort()

        # File should have the uncommitted changes from before session
        assert readme.read_text() == "# Test\nUncommitted change\n"

    def test_abort_restores_missing_intent_to_add_entry(self, temp_git_repo):
        """Abort should restore a pre-session ITA whose worktree path is absent."""
        missing_path = temp_git_repo / "missing.txt"
        missing_path.write_text("moved away before the session\n")
        subprocess.run(
            ["git", "add", "-N", missing_path.name],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
        )
        missing_path.unlink()
        original_entry = subprocess.run(
            ["git", "ls-files", "--stage", "--", missing_path.name],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        (temp_git_repo / "README.md").write_text("# Test\nSession change\n")
        command_start()
        subprocess.run(
            ["git", "update-index", "--force-remove", "--", missing_path.name],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
        )

        command_abort(quiet=True)

        restored_entry = subprocess.run(
            ["git", "ls-files", "--stage", "--", missing_path.name],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        debug_entry = subprocess.run(
            ["git", "ls-files", "--debug", "--", missing_path.name],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert not missing_path.exists()
        assert restored_entry == original_entry
        assert "flags: 20004000" in debug_entry

    def test_abort_undoes_commits(self, temp_git_repo):
        """Test that abort undoes commits made during session."""
        # Get initial HEAD
        initial_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Create changes and start session
        (temp_git_repo / "README.md").write_text("# Test\nNew content\n")

        # Start session
        command_start()

        # Make a change and commit it
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Session commit"], check=True, cwd=temp_git_repo, capture_output=True)

        # Verify HEAD moved
        new_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert new_head != initial_head

        # Abort should restore HEAD
        command_abort()

        restored_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert restored_head == initial_head

    def test_abort_clears_state(self, temp_git_repo):
        """Test that abort clears all session state."""
        # Create changes and start
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start()
        state_dir = get_state_directory_path()
        assert state_dir.exists()

        command_abort()

        assert not state_dir.exists()

    def test_abort_with_staged_changes_before_session(self, temp_git_repo):
        """Test abort restores staged changes from before session."""
        # Create and stage a new file before session
        new_file = temp_git_repo / "new.txt"
        new_file.write_text("new content\n")
        subprocess.run(["git", "add", "new.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Verify it's staged
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "new.txt" in result.stdout

        # Create unstaged changes so start has something to work with
        (temp_git_repo / "README.md").write_text("# Test\nModified\n")

        # Start session
        command_start()

        # Unstage and delete the file
        subprocess.run(["git", "reset", "new.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        new_file.unlink()

        # Abort should restore the staged file
        command_abort()

        # File should exist again
        assert new_file.exists()
        assert new_file.read_text() == "new content\n"

        # And should be staged
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "new.txt" in result.stdout

    def test_abort_resets_auto_added_files(self, temp_git_repo):
        """Test that abort resets auto-added files."""
        # Create an untracked file
        new_file = temp_git_repo / "untracked.txt"
        new_file.write_text("untracked content\n")

        # Create a diff so start has something to work with
        (temp_git_repo / "README.md").write_text("# Test\nModified\n")

        # Start session
        command_start()

        # Simulate auto-add by adding with -N and tracking it
        subprocess.run(["git", "add", "-N", "untracked.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        # Record it in auto-added list
        append_file_path_to_file(get_auto_added_files_file_path(), "untracked.txt")

        # Verify it's in index
        result = subprocess.run(
            ["git", "ls-files", "untracked.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "untracked.txt" in result.stdout

        # Abort should reset the auto-added file
        command_abort()

        # File should no longer be in index
        result = subprocess.run(
            ["git", "ls-files", "untracked.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == ""

    def test_abort_retry_accepts_already_restored_snapshot_directory(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """A later abort failure must not make a restored directory block retry."""
        (temp_git_repo / "README.md").write_text("# Test\nModified\n")
        command_start(quiet=True)
        nested = temp_git_repo / "untracked-dir"
        nested.mkdir()
        (nested / "saved.txt").write_text("saved\n")
        snapshot_file_if_untracked("untracked-dir")
        shutil.rmtree(nested)

        original_restore = abort_module.restore_batch_refs
        restore_calls = 0

        def fail_first_restore(snapshot):
            nonlocal restore_calls
            restore_calls += 1
            if restore_calls == 1:
                raise CommandError("forced restore failure")
            return original_restore(snapshot)

        monkeypatch.setattr(abort_module, "restore_batch_refs", fail_first_restore)

        with pytest.raises(CommandError, match="forced restore failure"):
            command_abort(quiet=True)
        assert (nested / "saved.txt").read_text() == "saved\n"

        command_abort(quiet=True)

        assert (nested / "saved.txt").read_text() == "saved\n"

    def test_abort_directory_copy_failure_leaves_retryable_destination(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """A partial staging copy must not poison the repository destination."""
        (temp_git_repo / "README.md").write_text("# Test\nModified\n")
        command_start(quiet=True)
        target = temp_git_repo / "untracked-dir"
        target.mkdir()
        (target / "one.txt").write_text("one\n")
        (target / "two.txt").write_text("two\n")
        snapshot_file_if_untracked("untracked-dir")
        shutil.rmtree(target)
        real_copytree = shutil.copytree

        def fail_after_partial_copy(source, destination, *args, **kwargs):
            destination_path = Path(destination)
            destination_path.mkdir()
            shutil.copy2(Path(source) / "one.txt", destination_path / "one.txt")
            raise OSError("forced partial directory copy")

        monkeypatch.setattr(abort_module.shutil, "copytree", fail_after_partial_copy)
        with pytest.raises(OSError, match="forced partial directory copy"):
            command_abort(quiet=True)

        assert not target.exists()
        monkeypatch.setattr(abort_module.shutil, "copytree", real_copytree)

        command_abort(quiet=True)

        assert (target / "one.txt").read_text() == "one\n"
        assert (target / "two.txt").read_text() == "two\n"

    def test_abort_file_copy_failure_does_not_publish_partial_bytes(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """A failed file copy must leave the repository path untouched."""
        (temp_git_repo / "README.md").write_text("# Test\nModified\n")
        command_start(quiet=True)
        target = temp_git_repo / "untracked.txt"
        target.write_text("saved\n")
        snapshot_file_if_untracked("untracked.txt")
        target.write_text("during session\n")
        real_copy2 = shutil.copy2

        def fail_after_partial_copy(source, destination, *args, **kwargs):
            Path(destination).write_text("partial")
            raise OSError("forced partial file copy")

        monkeypatch.setattr(abort_module.shutil, "copy2", fail_after_partial_copy)
        with pytest.raises(OSError, match="forced partial file copy"):
            command_abort(quiet=True)

        assert target.read_text() == "during session\n"
        monkeypatch.setattr(abort_module.shutil, "copy2", real_copy2)

        command_abort(quiet=True)

        assert target.read_text() == "saved\n"

    def test_abort_refuses_file_snapshot_obstructed_by_directory(
        self,
        temp_git_repo,
    ):
        """A file snapshot must not be copied inside an obstructing directory."""
        (temp_git_repo / "README.md").write_text("# Test\nModified\n")
        command_start(quiet=True)
        target = temp_git_repo / "untracked.txt"
        target.write_text("saved\n")
        snapshot_file_if_untracked("untracked.txt")
        target.unlink()
        target.mkdir()

        with pytest.raises(CommandError, match="untracked file.*now a directory"):
            command_abort(quiet=True)

        assert target.is_dir()
        assert not (target / "untracked.txt").exists()

        target.rmdir()
        command_abort(quiet=True)
        assert target.read_text() == "saved\n"

    def test_abort_fails_closed_when_untracked_snapshot_is_missing(
        self,
        temp_git_repo,
    ):
        """Missing recovery bytes must not be skipped before session cleanup."""
        (temp_git_repo / "README.md").write_text("# Test\nModified\n")
        command_start(quiet=True)
        target = temp_git_repo / "untracked.txt"
        target.write_text("saved\n")
        snapshot_file_if_untracked("untracked.txt")
        snapshot = get_abort_snapshots_directory_path() / "untracked.txt"
        snapshot.unlink()

        with pytest.raises(CommandError, match="abort snapshot is unavailable"):
            command_abort(quiet=True)

        assert get_state_directory_path().exists()

    def test_abort_rejects_snapshot_path_traversal_before_reset(
        self,
        temp_git_repo,
    ):
        """Corrupt snapshot paths must not escape the repository or mutate it."""
        (temp_git_repo / "README.md").write_text("# Test\nBefore session\n")
        command_start(quiet=True)
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nDuring session\n")
        write_file_paths_file(
            get_abort_snapshot_list_file_path(),
            ["../outside.txt"],
        )

        with pytest.raises(CommandError, match="invalid repository path"):
            command_abort(quiet=True)

        assert readme.read_text() == "# Test\nDuring session\n"
        assert get_state_directory_path().exists()

    def test_abort_rejects_nested_restore_through_symlinked_parent(
        self,
        temp_git_repo,
    ):
        """A nested snapshot restore must never write through a parent symlink."""
        (temp_git_repo / "README.md").write_text("# Test\nBefore session\n")
        command_start(quiet=True)
        target_parent = temp_git_repo / "nested"
        target_parent.mkdir()
        target = target_parent / "saved.txt"
        target.write_text("saved\n")
        snapshot_file_if_untracked("nested/saved.txt")
        shutil.rmtree(target_parent)

        outside = temp_git_repo.parent / "outside"
        outside.mkdir()
        outside_target = outside / "saved.txt"
        outside_target.write_text("outside\n")
        target_parent.symlink_to(outside, target_is_directory=True)

        with pytest.raises(CommandError, match="parent path is not a real directory"):
            command_abort(quiet=True)

        assert outside_target.read_text() == "outside\n"
        assert get_state_directory_path().exists()

        target_parent.unlink()
        command_abort(quiet=True)

        assert target.read_text() == "saved\n"
        assert outside_target.read_text() == "outside\n"

    def test_abort_rejects_invalid_intent_object_id_before_reset(
        self,
        temp_git_repo,
    ):
        """Corrupt exact-index metadata must fail before destructive recovery."""
        intent_path = temp_git_repo / "intent.txt"
        intent_path.write_text("intent\n")
        subprocess.run(
            ["git", "add", "-N", intent_path.name],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        (temp_git_repo / "README.md").write_text("# Test\nBefore session\n")
        command_start(quiet=True)
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nDuring session\n")

        entries_path = get_abort_intent_to_add_entries_file_path()
        entries = json.loads(entries_path.read_text())
        entries[intent_path.name]["object_id"] = "-" + "0" * 39
        entries_path.write_text(json.dumps(entries))

        with pytest.raises(CommandError, match="invalid index entry"):
            command_abort(quiet=True)

        assert readme.read_text() == "# Test\nDuring session\n"
        assert get_state_directory_path().exists()

    def test_abort_fails_closed_when_snapshot_disappears_after_preflight(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """A preflight race must not turn a required restore into a silent skip."""
        (temp_git_repo / "README.md").write_text("# Test\nModified\n")
        command_start(quiet=True)
        target = temp_git_repo / "untracked.txt"
        target.write_text("saved\n")
        snapshot_file_if_untracked("untracked.txt")
        target.unlink()
        snapshot = get_abort_snapshots_directory_path() / "untracked.txt"
        snapshot_was_checked = False
        real_lexists = os.path.lexists
        real_mkdir = Path.mkdir

        def track_snapshot_preflight(path):
            nonlocal snapshot_was_checked
            if os.fspath(path) == os.fspath(snapshot):
                snapshot_was_checked = True
            return real_lexists(path)

        def remove_snapshot_before_restore(path, *args, **kwargs):
            if (
                path == target.parent
                and snapshot_was_checked
                and snapshot.exists()
            ):
                snapshot.unlink()
            return real_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(abort_module.os.path, "lexists", track_snapshot_preflight)
        monkeypatch.setattr(Path, "mkdir", remove_snapshot_before_restore)

        with pytest.raises(OSError):
            command_abort(quiet=True)

        assert get_state_directory_path().exists()
        assert not target.exists()


def test_snapshot_directory_comparison_avoids_entry_scale_python_heap(tmp_path):
    """Retry comparison should stream directory entries instead of indexing them."""
    probe = """
import gc
import sys
import tracemalloc
from pathlib import Path
from git_stage_batch.commands.abort import _snapshot_path_matches_target

gc.collect()
tracemalloc.start()
try:
    assert _snapshot_path_matches_target(Path(sys.argv[1]), Path(sys.argv[2]))
    _current_heap, peak_heap = tracemalloc.get_traced_memory()
finally:
    tracemalloc.stop()
print(peak_heap)
"""
    heap_peaks = []
    for child_count in (128, 1024):
        snapshot = tmp_path / f"snapshot-{child_count}"
        target = tmp_path / f"target-{child_count}"
        snapshot.mkdir()
        target.mkdir()
        for child_index in range(child_count):
            name = f"entry-{child_index:05d}"
            (snapshot / name).write_bytes(b"")
            (target / name).write_bytes(b"")

        result = subprocess.run(
            [sys.executable, "-c", probe, str(snapshot), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
        heap_peaks.append(int(result.stdout.strip()))

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + 64 * 1024


def test_snapshot_file_comparison_rechecks_same_stat_signature(tmp_path):
    """A prior retry comparison must not cache later same-size target bytes."""
    snapshot = tmp_path / "snapshot.txt"
    target = tmp_path / "target.txt"
    snapshot.write_bytes(b"saved\n")
    target.write_bytes(b"saved\n")
    target_metadata = target.stat()

    assert abort_module._snapshot_path_matches_target(snapshot, target)

    target.write_bytes(b"other\n")
    os.utime(
        target,
        ns=(target_metadata.st_atime_ns, target_metadata.st_mtime_ns),
    )

    assert not abort_module._snapshot_path_matches_target(snapshot, target)
