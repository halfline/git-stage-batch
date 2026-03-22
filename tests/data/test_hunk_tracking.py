"""Tests for hunk navigation and state management."""

import subprocess

import pytest

from git_stage_batch.data.hunk_tracking import (
    advance_to_next_change,
    clear_selected_change_state_files,
    fetch_next_change,
)
from git_stage_batch.utils.file_io import append_lines_to_file, write_text_file_contents
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
    get_line_changes_json_file_path,
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
    get_working_tree_snapshot_file_path,
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


class TestClearCurrentHunkStateFiles:
    """Tests for clear_selected_change_state_files()."""

    def test_clears_all_state_files(self, temp_git_repo):
        """Test that all selected hunk state files are cleared."""
        # Create state files
        get_selected_hunk_patch_file_path().write_text("patch")
        get_selected_hunk_hash_file_path().write_text("hash")
        get_line_changes_json_file_path().write_text("{}")
        get_index_snapshot_file_path().write_text("index")
        get_working_tree_snapshot_file_path().write_text("tree")
        get_processed_include_ids_file_path().write_text("1\n2\n")

        # Clear state
        clear_selected_change_state_files()

        # Verify all files are removed
        assert not get_selected_hunk_patch_file_path().exists()
        assert not get_selected_hunk_hash_file_path().exists()
        assert not get_line_changes_json_file_path().exists()
        assert not get_index_snapshot_file_path().exists()
        assert not get_working_tree_snapshot_file_path().exists()
        assert not get_processed_include_ids_file_path().exists()

    def test_handles_missing_files(self, temp_git_repo):
        """Test that clearing works even when files don't exist."""
        # Should not raise error when files don't exist
        clear_selected_change_state_files()


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
        from git_stage_batch.core.hashing import compute_stable_hunk_hash
        from git_stage_batch.core.diff_parser import parse_unified_diff_into_single_hunk_patches

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
            capture_output=True,
            text=True,
        )
        patches = parse_unified_diff_into_single_hunk_patches(result.stdout)
        first_hash = compute_stable_hunk_hash(patches[0].to_patch_text())

        append_lines_to_file(get_block_list_file_path(), [first_hash])

        # Find next hunk - should skip blocked one
        line_changes = fetch_next_change()

        assert line_changes is not None
        assert line_changes.path == "file2.txt"

    def test_skips_blocked_files(self, temp_git_repo):
        """Test that hunks from blocked files are skipped."""
        from git_stage_batch.utils.paths import get_blocked_files_file_path
        from git_stage_batch.utils.file_io import append_file_path_to_file

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
        """Test that None is returned when there are no changes."""
        line_changes = fetch_next_change()

        assert line_changes is None

    def test_returns_none_when_all_blocked(self, temp_git_repo):
        """Test that None is returned when all hunks are blocked."""
        from git_stage_batch.core.hashing import compute_stable_hunk_hash
        from git_stage_batch.core.diff_parser import parse_unified_diff_into_single_hunk_patches

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
            capture_output=True,
            text=True,
        )
        patches = parse_unified_diff_into_single_hunk_patches(result.stdout)
        hunk_hash = compute_stable_hunk_hash(patches[0].to_patch_text())

        append_lines_to_file(get_block_list_file_path(), [hunk_hash])

        # Try to find next hunk
        line_changes = fetch_next_change()

        assert line_changes is None


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

        # Advance to next hunk
        advance_to_next_change()

        # Old processed IDs should be cleared
        assert not get_processed_include_ids_file_path().exists()

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
