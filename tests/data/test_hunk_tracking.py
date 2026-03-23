"""Tests for hunk navigation and state management."""

import subprocess

import pytest

from git_stage_batch.data.hunk_tracking import (
    advance_to_next_change,
    clear_selected_change_state_files,
    fetch_next_change,
    recalculate_selected_hunk_for_file,
    require_selected_hunk,
    snapshots_are_stale,
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


class TestSnapshotsAreStale:
    """Tests for snapshots_are_stale()."""

    def test_detects_missing_snapshots(self, temp_git_repo):
        """Test that missing snapshots are detected as stale."""
        # No snapshots exist yet
        assert snapshots_are_stale("test.txt") is True

    def test_detects_fresh_snapshots(self, temp_git_repo):
        """Test that fresh snapshots are not stale."""
        # Create a file and cache it
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("content\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("modified\n")

        # Cache the hunk with snapshots
        fetch_next_change()

        # Snapshots should be fresh
        assert snapshots_are_stale("test.txt") is False

    def test_detects_index_change(self, temp_git_repo):
        """Test that index changes make snapshots stale."""
        # Create a file and cache it
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("content\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("modified\n")

        # Cache the hunk with snapshots
        fetch_next_change()

        # Change the index
        test_file.write_text("changed again\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Snapshots should now be stale
        assert snapshots_are_stale("test.txt") is True

    def test_detects_working_tree_change(self, temp_git_repo):
        """Test that working tree changes make snapshots stale."""
        # Create a file and cache it
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("content\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("modified\n")

        # Cache the hunk with snapshots
        fetch_next_change()

        # Change the working tree
        test_file.write_text("different content\n")

        # Snapshots should now be stale
        assert snapshots_are_stale("test.txt") is True


class TestRequireCurrentHunkAndCheckStale:
    """Tests for require_selected_hunk()."""

    def test_exits_when_no_hunk_cached(self, temp_git_repo):
        """Test that it exits with error when no hunk is cached."""
        from git_stage_batch.exceptions import CommandError

        with pytest.raises(CommandError) as exc_info:
            require_selected_hunk()

        assert "No selected hunk" in exc_info.value.message

    def test_exits_when_hunk_is_stale(self, temp_git_repo):
        """Test that it exits with error when cached hunk is stale."""
        from git_stage_batch.exceptions import CommandError

        # Create a file and cache it
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("content\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("modified\n")

        # Cache the hunk
        fetch_next_change()

        # Make it stale
        test_file.write_text("different\n")

        with pytest.raises(CommandError) as exc_info:
            require_selected_hunk()

        assert "stale" in exc_info.value.message.lower()

    def test_succeeds_when_hunk_is_fresh(self, temp_git_repo):
        """Test that it succeeds when cached hunk is fresh."""
        # Create a file and cache it
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("content\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("modified\n")

        # Cache the hunk
        fetch_next_change()

        # Should not raise
        require_selected_hunk()


class TestRecalculateCurrentHunkForFile:
    """Tests for recalculate_selected_hunk_for_file()."""

    def test_recalculates_hunk_after_modification(self, temp_git_repo):
        """Test that hunk is recalculated after file modification."""
        # Create a file with multiple lines
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nline2\nline3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("changed1\nchanged2\nchanged3\n")

        # Cache initial hunk
        initial_lines = fetch_next_change()
        assert initial_lines is not None

        # Partially modify the file (simulate line-level operation)
        test_file.write_text("changed1\nline2\nchanged3\n")

        # Recalculate
        import io
        import sys
        captured = io.StringIO()
        sys.stdout = captured
        try:
            recalculate_selected_hunk_for_file("test.txt")
        finally:
            sys.stdout = sys.__stdout__

        # Should have updated the cached hunk
        assert get_selected_hunk_patch_file_path().exists()
        new_patch = get_selected_hunk_patch_file_path().read_text()
        assert "test.txt" in new_patch

    def test_clears_processed_ids(self, temp_git_repo):
        """Test that processed IDs are cleared when recalculating."""
        # Create a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("original\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("modified\n")

        # Cache hunk and add some processed IDs
        fetch_next_change()
        get_processed_include_ids_file_path().write_text("1\n2\n")

        # Recalculate
        import io
        import sys
        captured = io.StringIO()
        sys.stdout = captured
        try:
            recalculate_selected_hunk_for_file("test.txt")
        finally:
            sys.stdout = sys.__stdout__

        # Processed IDs should be cleared
        if get_processed_include_ids_file_path().exists():
            content = get_processed_include_ids_file_path().read_text()
            assert content.strip() == ""

    def test_skips_blocked_hunks(self, temp_git_repo):
        """Test that blocked hunks are skipped during recalculation."""
        from git_stage_batch.core.hashing import compute_stable_hunk_hash
        from git_stage_batch.data.session import initialize_abort_state

        # Create a file with change
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("original\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("modified\n")

        # Get the hunk hash and block it
        result = subprocess.run(
            ["git", "diff", "--no-color", "test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        from git_stage_batch.core.diff_parser import parse_unified_diff_into_single_hunk_patches
        patches = parse_unified_diff_into_single_hunk_patches(result.stdout)
        hunk_hash = compute_stable_hunk_hash(patches[0].to_patch_text())

        append_lines_to_file(get_block_list_file_path(), [hunk_hash])

        # Initialize session state
        initialize_abort_state()

        # Try to recalculate - should find no hunks
        import io
        import sys
        captured = io.StringIO()
        sys.stderr = captured
        try:
            recalculate_selected_hunk_for_file("test.txt")
        finally:
            sys.stderr = sys.__stderr__

        output = captured.getvalue()
        assert "No pending hunks" in output or not get_selected_hunk_patch_file_path().exists()
