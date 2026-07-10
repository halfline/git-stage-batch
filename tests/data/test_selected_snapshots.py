"""Tests for selected-file snapshot persistence."""

import subprocess

import pytest

from git_stage_batch.data.selected_change.snapshots import (
    snapshots_are_stale,
    write_snapshots_for_selected_file_path,
)
from git_stage_batch.utils.file_io import read_text_file_contents
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_index_snapshot_file_path,
    get_working_tree_snapshot_file_path,
)


class TestWriteSnapshotsForCurrentFilePath:
    """Tests for write_snapshots_for_selected_file_path with intent-to-add entries."""

    @pytest.fixture
    def temp_git_repo(self, tmp_path, monkeypatch):
        """Create a temporary git repository."""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, capture_output=True)

        # Create initial commit
        (tmp_path / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

        # Ensure state directory exists
        ensure_state_directory_exists()

        return tmp_path

    def test_intent_to_add_tracked_file_uses_head_content(self, temp_git_repo):
        """When a tracked file has intent-to-add entry, index snapshot should use HEAD content."""
        # Create and commit a file
        test_file = temp_git_repo / "tracked.py"
        original_content = '''"""Module docstring."""

def original_function():
    """Original implementation."""
    return "original"
'''
        test_file.write_text(original_content)
        subprocess.run(["git", "add", "tracked.py"], cwd=temp_git_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add tracked file"], cwd=temp_git_repo, check=True, capture_output=True)

        # Modify the file in working tree
        modified_content = '''"""Module docstring."""

def original_function():
    """Modified implementation."""
    return "modified"

def new_function():
    """New function."""
    return "new"
'''
        test_file.write_text(modified_content)

        # Simulate intent-to-add by removing from cache and re-adding with -N.
        # This creates an empty blob (e69de29...) in the index.
        subprocess.run(["git", "rm", "--cached", "tracked.py"], cwd=temp_git_repo, check=True, capture_output=True)
        subprocess.run(["git", "add", "-N", "tracked.py"], cwd=temp_git_repo, check=True, capture_output=True)

        # Verify we have an empty blob in index.
        ls_result = subprocess.run(
            ["git", "ls-files", "--stage", "tracked.py"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True,
        )
        assert "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391" in ls_result.stdout, "Should have empty blob in index"

        # Verify git show :file returns empty content.
        show_result = subprocess.run(
            ["git", "show", ":tracked.py"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True,
        )
        assert show_result.stdout == "", "Index should return empty content for intent-to-add"

        write_snapshots_for_selected_file_path("tracked.py")

        index_snapshot_path = get_index_snapshot_file_path()
        index_snapshot_content = read_text_file_contents(index_snapshot_path)

        assert index_snapshot_content == original_content, (
            "Index snapshot should contain HEAD content for intent-to-add tracked file, "
            "not empty content"
        )

        working_tree_snapshot_path = get_working_tree_snapshot_file_path()
        working_tree_snapshot_content = read_text_file_contents(working_tree_snapshot_path)
        assert working_tree_snapshot_content == modified_content

    def test_intent_to_add_new_file_keeps_empty_index(self, temp_git_repo):
        """New files with intent-to-add should keep empty index snapshot."""
        test_file = temp_git_repo / "newfile.py"
        new_content = '''"""New file."""

def new_function():
    return "new"
'''
        test_file.write_text(new_content)

        subprocess.run(["git", "add", "-N", "newfile.py"], cwd=temp_git_repo, check=True, capture_output=True)

        head_check = subprocess.run(
            ["git", "cat-file", "-e", "HEAD:newfile.py"],
            cwd=temp_git_repo,
            capture_output=True,
        )
        assert head_check.returncode != 0, "New file should not exist in HEAD"

        write_snapshots_for_selected_file_path("newfile.py")

        index_snapshot_path = get_index_snapshot_file_path()
        index_snapshot_content = read_text_file_contents(index_snapshot_path)

        assert index_snapshot_content == "", "New file should have empty index snapshot"

        working_tree_snapshot_path = get_working_tree_snapshot_file_path()
        working_tree_snapshot_content = read_text_file_contents(working_tree_snapshot_path)
        assert working_tree_snapshot_content == new_content

    def test_normal_tracked_file_uses_index_content(self, temp_git_repo):
        """Normal tracked files should use index content without fallback."""
        test_file = temp_git_repo / "normal.py"
        original_content = "original content\n"
        test_file.write_text(original_content)
        subprocess.run(["git", "add", "normal.py"], cwd=temp_git_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add normal file"], cwd=temp_git_repo, check=True, capture_output=True)

        staged_content = "staged content\n"
        test_file.write_text(staged_content)
        subprocess.run(["git", "add", "normal.py"], cwd=temp_git_repo, check=True, capture_output=True)

        working_content = "working tree content\n"
        test_file.write_text(working_content)

        write_snapshots_for_selected_file_path("normal.py")

        index_snapshot_path = get_index_snapshot_file_path()
        index_snapshot_content = read_text_file_contents(index_snapshot_path)
        assert index_snapshot_content == staged_content

        working_tree_snapshot_path = get_working_tree_snapshot_file_path()
        working_tree_snapshot_content = read_text_file_contents(working_tree_snapshot_path)
        assert working_tree_snapshot_content == working_content


class TestSnapshotsAreStale:
    """Tests for snapshots_are_stale()."""

    @pytest.fixture
    def temp_git_repo(self, tmp_path, monkeypatch):
        """Create a temporary git repository."""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, capture_output=True)

        (tmp_path / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

        ensure_state_directory_exists()

        return tmp_path

    def test_detects_missing_snapshots(self, temp_git_repo):
        """Missing snapshots should be considered stale."""
        assert snapshots_are_stale("test.txt") is True

    def test_detects_fresh_snapshots(self, temp_git_repo):
        """Current index and working tree snapshots should not be stale."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("content\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("modified\n")

        write_snapshots_for_selected_file_path("test.txt")

        assert snapshots_are_stale("test.txt") is False

    def test_detects_index_change(self, temp_git_repo):
        """Index changes should make selected-file snapshots stale."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("content\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("modified\n")
        write_snapshots_for_selected_file_path("test.txt")

        test_file.write_text("changed again\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        assert snapshots_are_stale("test.txt") is True

    def test_detects_working_tree_change(self, temp_git_repo):
        """Working tree changes should make selected-file snapshots stale."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("content\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("modified\n")
        write_snapshots_for_selected_file_path("test.txt")

        test_file.write_text("different content\n")

        assert snapshots_are_stale("test.txt") is True
