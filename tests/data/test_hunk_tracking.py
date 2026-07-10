"""Tests for hunk navigation, state management, and staleness detection."""

import json
from git_stage_batch.core.hashing import compute_stable_hunk_hash_from_lines
from tests.diff_parser_helpers import collect_unified_diff
from git_stage_batch.utils.paths import get_blocked_files_file_path
from git_stage_batch.utils.file_io import append_file_path_to_file
from git_stage_batch.exceptions import NoMoreHunks
from git_stage_batch.commands.include import command_include_to_batch

import subprocess

import pytest

import git_stage_batch.data.hunk_tracking as hunk_tracking
from git_stage_batch.commands.again import command_again
from git_stage_batch.commands.start import command_start
from git_stage_batch.data.hunk_tracking import (
    advance_to_next_change,
    fetch_next_change,
)
from git_stage_batch.utils.file_io import append_lines_to_file
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
    get_line_changes_json_file_path,
    get_processed_batch_ids_file_path,
    get_processed_include_ids_file_path,
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

    ensure_state_directory_exists()

    return repo


def test_batch_metadata_snapshot_loads_once(monkeypatch):
    """One hunk scan should reuse a single batch metadata snapshot."""
    calls = []

    monkeypatch.setattr(hunk_tracking, "list_batch_names", lambda: ["batch-a"])

    def fake_read_batch_metadata_for_batches(batch_names):
        calls.append(tuple(batch_names))
        return {"batch-a": {"files": {}}}

    monkeypatch.setattr(
        hunk_tracking,
        "read_batch_metadata_for_batches",
        fake_read_batch_metadata_for_batches,
    )

    snapshot = hunk_tracking._BatchMetadataSnapshot()

    first = snapshot.metadata_by_name()
    second = snapshot.metadata_by_name()

    assert first == {"batch-a": {"files": {}}}
    assert second is first
    assert calls == [("batch-a",)]


class TestFindAndCacheNextUnblockedHunk:
    """Tests for fetch_next_change()."""

    def test_finds_and_caches_first_hunk(self, temp_git_repo):
        """Test that first unblocked hunk is found and cached."""
        # Create a change
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("original\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("modified\n")

        # Find and cache
        line_changes = fetch_next_change()

        assert line_changes is not None
        assert line_changes.path == "test.txt"
        assert get_selected_hunk_patch_file_path().exists()
        assert get_selected_hunk_hash_file_path().exists()
        assert get_line_changes_json_file_path().exists()

    def test_skips_blocked_hunks(self, temp_git_repo):
        """Test that blocked hunks are skipped."""

        # Create two files
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "."], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        # Get hash of first hunk and block it
        result = subprocess.run(
            ["git", "diff", "--no-color"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,)
        stdout_bytes = result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode("utf-8")
        patches = list(collect_unified_diff(stdout_bytes.splitlines(keepends=True)))
        first_hash = compute_stable_hunk_hash_from_lines(patches[0].lines)

        append_lines_to_file(get_block_list_file_path(), [first_hash])

        # Find next hunk - should skip blocked one
        line_changes = fetch_next_change()

        assert line_changes is not None
        assert line_changes.path == "file2.txt"

    def test_skips_blocked_files(self, temp_git_repo):
        """Test that hunks from blocked files are skipped."""

        # Create two files
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "."], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        # Block file1
        append_file_path_to_file(get_blocked_files_file_path(), "file1.txt")

        # Find next hunk - should skip blocked file
        line_changes = fetch_next_change()

        assert line_changes is not None
        assert line_changes.path == "file2.txt"

    def test_returns_none_when_no_changes(self, temp_git_repo):
        """Test that NoMoreHunks is raised when there are no changes."""

        with pytest.raises(NoMoreHunks):
            fetch_next_change()

    def test_returns_none_when_all_blocked(self, temp_git_repo):
        """Test that None is returned when all hunks are blocked."""

        # Create a change
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("original\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("modified\n")

        # Block the hunk
        result = subprocess.run(
            ["git", "diff", "--no-color"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,)
        stdout_bytes = result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode("utf-8")
        patches = list(collect_unified_diff(stdout_bytes.splitlines(keepends=True)))
        hunk_hash = compute_stable_hunk_hash_from_lines(patches[0].lines)

        append_lines_to_file(get_block_list_file_path(), [hunk_hash])

        # Try to find next hunk - should raise since all are blocked

        with pytest.raises(NoMoreHunks):
            fetch_next_change()

    def test_skips_batched_hunks(self, temp_git_repo):
        """Test that hunks are skipped when claimed by a batch."""

        # Create two files
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "."], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        # Start session and include first file to a batch
        command_start()
        first_change = fetch_next_change()
        assert first_change.path == "file1.txt"
        command_include_to_batch("mybatch", quiet=True)

        # Find next hunk - should skip batched one
        command_again()
        line_changes = fetch_next_change()

        assert line_changes is not None
        assert line_changes.path == "file2.txt"

    def test_returns_none_when_all_batched(self, temp_git_repo):
        """Test that NoMoreHunks is raised when all hunks are batched."""

        # Create a change
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("original\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("modified\n")

        # Include the hunk to a batch
        command_start()
        fetch_next_change()
        command_include_to_batch("mybatch", quiet=True)

        # Try to find next hunk - should raise since all are batched
        command_again()

        with pytest.raises(NoMoreHunks):
            fetch_next_change()


class TestAdvanceToNextHunk:
    """Tests for advance_to_next_change()."""

    def test_clears_state_and_finds_next(self, temp_git_repo):
        """Test that advance clears old state and finds next hunk."""

        # Create two changes
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "."], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        # Cache first hunk
        get_selected_hunk_patch_file_path().write_text("old patch")
        get_selected_hunk_hash_file_path().write_text("old hash")
        get_processed_include_ids_file_path().write_text("1\n")
        # processed.batch uses JSON format now and is global state (persists across hunks)
        get_processed_batch_ids_file_path().write_text(json.dumps({"file1.txt": {"presence_claims": [{"source_lines": ["2"]}]}}))

        # Advance to next hunk
        advance_to_next_change()

        # Old per-hunk processed IDs should be cleared
        assert not get_processed_include_ids_file_path().exists()
        # processed.batch is global state - should still exist
        assert get_processed_batch_ids_file_path().exists()

        # New hunk should be cached
        assert get_selected_hunk_patch_file_path().exists()
        assert get_selected_hunk_hash_file_path().exists()
        patch_content = get_selected_hunk_patch_file_path().read_text()
        assert "file1.txt" in patch_content or "file2.txt" in patch_content
        assert patch_content != "old patch"

    def test_handles_no_more_hunks(self, temp_git_repo):
        """Test that advance handles having no more hunks gracefully."""
        # No changes, so no hunks
        advance_to_next_change()

        # State files should be cleared
        assert not get_selected_hunk_patch_file_path().exists()
        assert not get_selected_hunk_hash_file_path().exists()
