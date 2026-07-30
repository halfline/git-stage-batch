"""Tests for again command."""

from git_stage_batch.batch.state.batch_names import batch_exists
from git_stage_batch.batch.state.query import read_batch_metadata
from git_stage_batch.commands.discard import command_discard_to_batch
from git_stage_batch.commands.include import command_include
from git_stage_batch.commands.show import command_show, command_show_file_list
from git_stage_batch.data.file_review.state import read_last_file_review_state
from git_stage_batch.data.hunk_tracking import fetch_next_change
from git_stage_batch.data.session import get_iteration_count
from git_stage_batch.data.selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
)
from git_stage_batch.data.selected_change.paths import get_selected_change_file_path
from git_stage_batch.exceptions import NoMoreHunks

import subprocess

import pytest

from git_stage_batch.commands.again import command_again
from git_stage_batch.commands.start import command_start
from git_stage_batch.batch.state.lifecycle import create_batch
from git_stage_batch.utils.file_io import read_text_file_contents, write_text_file_contents
from git_stage_batch.utils.paths import (
    get_abort_head_file_path,
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
    get_abort_stash_file_path,
    get_block_list_file_path,
    get_batches_directory_path,
    get_selected_change_clear_reason_file_path,
    get_session_batch_sources_file_path,
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


class TestCommandAgain:
    """Tests for again command."""
    def test_again_advances_session_iteration(self, temp_git_repo):
        """Again should report each fresh pass as the next iteration."""
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start(quiet=True)
        assert get_iteration_count() == 1

        command_again(quiet=True)
        assert get_iteration_count() == 2

        command_again(quiet=True)
        assert get_iteration_count() == 3

    def test_again_clears_iteration_state(self, temp_git_repo):
        """Test that again clears iteration-specific state but preserves permanent state."""
        # Create changes for start to process
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start()
        state_dir = get_state_directory_path()

        # Create iteration-specific files
        blocklist = get_block_list_file_path()
        write_text_file_contents(blocklist, "test")

        # Create permanent files
        journal = state_dir / "journal.jsonl"
        journal.write_text("test")
        abort_head = get_abort_head_file_path()
        abort_head.write_text("test")

        assert blocklist.exists()
        assert journal.exists()
        assert abort_head.exists()

        command_again()

        # Blocklist should be cleared
        assert not blocklist.exists()

        # Permanent files should be preserved
        assert journal.exists()
        assert abort_head.exists()

    def test_again_clears_file_list_clear_reason(self, temp_git_repo):
        """Again should make a fresh selection after a navigational file list."""
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start()
        command_show_file_list(["README.md"])

        assert read_selected_change_kind() is None
        assert get_selected_change_clear_reason_file_path().exists()

        command_again(quiet=True)

        assert not get_selected_change_clear_reason_file_path().exists()
        assert read_selected_change_kind() == SelectedChangeKind.HUNK
        assert get_selected_change_file_path() == "README.md"

    def test_again_clears_file_review_state(self, temp_git_repo):
        """Again should not leave page-review state from the previous selected file."""
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start()
        command_show(file="README.md", page="all")

        assert read_last_file_review_state() is not None
        assert read_selected_change_kind() == SelectedChangeKind.FILE

        command_again(quiet=True)

        assert read_last_file_review_state() is None
        assert read_selected_change_kind() == SelectedChangeKind.HUNK
        assert get_selected_change_file_path() == "README.md"

    def test_again_clears_stale_binary_guard_for_fresh_pass(self, temp_git_repo):
        """A stale binary selection should not survive an explicit fresh pass."""
        binary_file = temp_git_repo / "asset.bin"
        binary_file.write_bytes(b"\x00\x01\x02")
        subprocess.run(["git", "add", "asset.bin"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add binary"], check=True, cwd=temp_git_repo, capture_output=True)

        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")
        binary_file.write_bytes(b"\x00\x03\x04")

        command_start()
        command_show(file="asset.bin", porcelain=True)
        subprocess.run(["git", "restore", "asset.bin"], check=True, cwd=temp_git_repo, capture_output=True)

        assert read_selected_change_kind() == SelectedChangeKind.BINARY

        command_again(quiet=True)

        assert read_selected_change_kind() == SelectedChangeKind.HUNK
        assert get_selected_change_file_path() == "README.md"

        command_include(quiet=True)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout
        assert "M  README.md" in status

    def test_again_when_no_state_exists(self, temp_git_repo):
        """Test that again works when state directory gets recreated."""
        # Create changes for start to process
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        # Start a session first
        command_start()
        state_dir = get_state_directory_path()
        assert state_dir.exists()

        # Call again which clears and recreates state
        command_again()

        # State directory should still exist
        assert state_dir.exists()

    def test_again_preserves_batch_state(self, temp_git_repo):
        """Again should preserve authoritative batch metadata."""

        # Create changes and start session
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")
        command_start()

        # Create a current batch.
        create_batch("my-batch", "Test batch")

        # Run again
        command_again()

        assert batch_exists("my-batch")
        assert read_batch_metadata("my-batch")["note"] == "Test batch"

    def test_again_preserves_multiple_batches(self, temp_git_repo):
        """Test that again preserves multiple batches correctly."""
        # Create changes and start session
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")
        command_start()

        # Create multiple batches
        create_batch("batch1", "First")
        create_batch("batch2", "Second")

        # Run again
        command_again()

        # Both batches and their current metadata should be preserved.
        assert batch_exists("batch1")
        assert batch_exists("batch2")
        batch1 = read_batch_metadata("batch1")
        batch2 = read_batch_metadata("batch2")
        assert batch1["note"] == "First"
        assert batch2["note"] == "Second"

    def test_again_preserves_abort_head(self, temp_git_repo):
        """Test that again preserves abort-head file."""
        # Create changes and start session
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")
        command_start()

        # Create abort-head file
        abort_head_file = get_abort_head_file_path()
        write_text_file_contents(abort_head_file, "abc123def456\n")

        # Run again
        command_again()

        # Abort-head should be preserved
        assert abort_head_file.exists()
        content = read_text_file_contents(abort_head_file)
        assert content == "abc123def456\n"

    def test_again_preserves_abort_stash(self, temp_git_repo):
        """Test that again preserves abort-stash file."""
        # Create changes and start session
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")
        command_start()

        # Create abort-stash file
        abort_stash_file = get_abort_stash_file_path()
        write_text_file_contents(abort_stash_file, "stash@{0}\n")

        # Run again
        command_again()

        # Abort-stash should be preserved
        assert abort_stash_file.exists()
        content = read_text_file_contents(abort_stash_file)
        assert content == "stash@{0}\n"

    def test_again_preserves_abort_snapshot_list(self, temp_git_repo):
        """Test that again preserves abort snapshot list file."""
        # Create changes and start session
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")
        command_start()

        # Create snapshot list file
        snapshot_list_file = get_abort_snapshot_list_file_path()
        write_text_file_contents(snapshot_list_file, "snapshot1\nsnapshot2\n")

        # Run again
        command_again()

        # Snapshot list should be preserved
        assert snapshot_list_file.exists()
        content = read_text_file_contents(snapshot_list_file)
        assert content == "snapshot1\nsnapshot2\n"

    def test_again_preserves_snapshots_directory(self, temp_git_repo):
        """Test that again preserves snapshots directory and its contents."""
        # Create changes and start session
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")
        command_start()

        # Create snapshots directory with files
        snapshots_dir = get_abort_snapshots_directory_path()
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        (snapshots_dir / "snapshot1").write_text("content1")
        (snapshots_dir / "snapshot2").write_text("content2")

        # Run again
        command_again()

        # Snapshots directory should exist
        assert snapshots_dir.exists()

        # Snapshot files should be preserved
        assert (snapshots_dir / "snapshot1").exists()
        assert (snapshots_dir / "snapshot1").read_text() == "content1"
        assert (snapshots_dir / "snapshot2").exists()
        assert (snapshots_dir / "snapshot2").read_text() == "content2"

    def test_again_works_without_batches(self, temp_git_repo):
        """Test that again works correctly when no batches exist."""
        # Create changes and start session
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")
        command_start()

        # Create iteration-specific file to verify it was cleared
        state_dir = get_state_directory_path()
        blocklist = get_block_list_file_path()
        write_text_file_contents(blocklist, "test")

        # Run again (no batches exist)
        command_again()

        # State directory should exist
        assert state_dir.exists()
        # Iteration-specific file should be cleared
        assert not blocklist.exists()

        # Batches directory should not exist (wasn't created)
        batches_dir = get_batches_directory_path()
        assert not batches_dir.exists()

    def test_again_works_without_abort_state(self, temp_git_repo):
        """Test that again works correctly when no abort state exists."""
        # Create changes and start session
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")
        command_start()

        # Run again without creating any abort state files
        command_again()

        # Should complete without error
        assert get_state_directory_path().exists()

    def test_again_preserves_session_batch_sources(self, temp_git_repo):
        """Test that 'again' preserves session-batch-sources.json."""
        # Create changes and start session
        (temp_git_repo / "test.txt").write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add test"], check=True, capture_output=True)

        (temp_git_repo / "test.txt").write_text("line 1 MODIFIED\nline 2\nline 3\n")

        command_start()

        # Discard to batch (creates batch source)

        fetch_next_change()
        command_discard_to_batch("test-batch", quiet=True)

        batch_sources_file = get_session_batch_sources_file_path()

        assert batch_sources_file.exists(), "session-batch-sources.json should exist"
        content_before = read_text_file_contents(batch_sources_file)
        assert content_before, "session-batch-sources.json should have content"

        command_again()

        assert batch_sources_file.exists(), "session-batch-sources.json should be preserved by 'again'"
        content_after = read_text_file_contents(batch_sources_file)
        assert content_after, "session-batch-sources.json should still have content"
        assert "test.txt" in content_after, "test.txt should still be in batch sources"

    def test_again_discarded_hunk_does_not_reappear(self, temp_git_repo):
        """Test that hunks discarded to batch don't reappear after 'again'."""
        # Create changes and start session
        (temp_git_repo / "test.txt").write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add test"], check=True, capture_output=True)

        (temp_git_repo / "test.txt").write_text("line 1 MODIFIED\nline 2\nline 3\n")

        command_start()


        # Find and discard the hunk
        hunk_before = fetch_next_change()
        assert hunk_before is not None, "Should have a hunk"

        command_discard_to_batch("test-batch", quiet=True)

        # The discarded hunk is filtered from the session.

        with pytest.raises(NoMoreHunks):
            fetch_next_change()

        command_again()

        with pytest.raises(NoMoreHunks):
            fetch_next_change()
