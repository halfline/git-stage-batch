"""Tests for again command."""

import json
from git_stage_batch.core.line_selection import parse_line_selection
from git_stage_batch.commands.discard import command_discard_to_batch
from git_stage_batch.data.hunk_tracking import fetch_next_change
from git_stage_batch.exceptions import NoMoreHunks

import subprocess

import pytest

from git_stage_batch.commands.again import command_again
from git_stage_batch.commands.start import command_start
from git_stage_batch.batch.operations import create_batch
from git_stage_batch.utils.file_io import read_text_file_contents, write_text_file_contents
from git_stage_batch.utils.paths import (
    get_abort_head_file_path,
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
    get_abort_stash_file_path,
    get_batch_claimed_hunks_file_path,
    get_batch_directory_path,
    get_batches_directory_path,
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

    def test_again_clears_iteration_state(self, temp_git_repo):
        """Test that again clears iteration-specific state but preserves permanent state."""
        # Create changes for start to process
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start()
        state_dir = get_state_directory_path()

        # Create iteration-specific files
        selected_hunk = state_dir / "selected-hunk-hash"
        selected_hunk.write_text("test")
        blocklist = state_dir / "blocklist"
        blocklist.write_text("test")

        # Create permanent files
        journal = state_dir / "journal.jsonl"
        journal.write_text("test")
        abort_head = state_dir / "abort-head"
        abort_head.write_text("test")

        assert selected_hunk.exists()
        assert blocklist.exists()
        assert journal.exists()
        assert abort_head.exists()

        command_again()

        # Iteration-specific files should be deleted
        assert not selected_hunk.exists()
        assert not blocklist.exists()

        # Permanent files should be preserved
        assert journal.exists()
        assert abort_head.exists()

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

    def test_again_preserves_batch_directories(self, temp_git_repo):
        """Test that again preserves batch directories across state wipe."""

        # Create changes and start session
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")
        command_start()

        # Create a batch with claim files
        create_batch("my-batch", "Test batch")
        batch_dir = get_batch_directory_path("my-batch")

        # Add claimed hunks
        claimed_hunks_path = get_batch_claimed_hunks_file_path("my-batch")
        write_text_file_contents(claimed_hunks_path, "hash1\nhash2\n")

        # Add claimed lines to metadata (JSON format)
        metadata_path = batch_dir / "metadata.json"
        metadata = json.loads(read_text_file_contents(metadata_path))
        metadata["files"] = {
            "README.md": {
                "batch_source_commit": "dummy",
                "claimed_lines": ["1-3"],
                "deletions": [],
                "mode": "100644"
            }
        }
        write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))

        # Run again
        command_again()

        # Batch directory should still exist
        assert batch_dir.exists()

        # Claim files should be preserved
        assert claimed_hunks_path.exists()
        content = read_text_file_contents(claimed_hunks_path)
        assert content == "hash1\nhash2\n"

        # Metadata should be preserved
        assert metadata_path.exists()
        metadata_after = json.loads(read_text_file_contents(metadata_path))
        file_data = metadata_after["files"]["README.md"]
        line_ids = set()
        for range_str in file_data.get("claimed_lines", []):
            line_ids.update(parse_line_selection(range_str))
        assert line_ids == {1, 2, 3}

    def test_again_preserves_multiple_batches(self, temp_git_repo):
        """Test that again preserves multiple batches correctly."""
        # Create changes and start session
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")
        command_start()

        # Create multiple batches
        create_batch("batch1", "First")
        create_batch("batch2", "Second")

        write_text_file_contents(get_batch_claimed_hunks_file_path("batch1"), "hash1\n")
        write_text_file_contents(get_batch_claimed_hunks_file_path("batch2"), "hash2\n")

        # Run again
        command_again()

        # Both batches should exist
        assert get_batch_directory_path("batch1").exists()
        assert get_batch_directory_path("batch2").exists()

        # Claims should be preserved
        assert read_text_file_contents(get_batch_claimed_hunks_file_path("batch1")) == "hash1\n"
        assert read_text_file_contents(get_batch_claimed_hunks_file_path("batch2")) == "hash2\n"

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
        blocklist = state_dir / "blocklist"
        blocklist.write_text("test")

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

        state_dir = get_state_directory_path()
        batch_sources_file = state_dir / "session-batch-sources.json"

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
