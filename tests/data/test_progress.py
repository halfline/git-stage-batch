"""Tests for progress tracking functions."""

import subprocess

import pytest

from git_stage_batch.data.progress import get_file_progress, get_hunk_counts
from git_stage_batch.utils.file_io import write_text_file_contents
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_current_lines_json_file_path,
    get_discarded_hunks_file_path,
    get_included_hunks_file_path,
    get_skipped_hunks_jsonl_file_path,
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


class TestHunkCounts:
    """Tests for get_hunk_counts function."""

    def test_get_hunk_counts_empty(self, temp_git_repo):
        """Test getting hunk counts when no state files exist."""
        ensure_state_directory_exists()
        counts = get_hunk_counts()

        assert counts["included"] == 0
        assert counts["skipped"] == 0
        assert counts["discarded"] == 0
        assert counts["remaining"] == 0

    def test_get_hunk_counts_with_included(self, temp_git_repo):
        """Test counting included hunks."""
        ensure_state_directory_exists()
        included_file = get_included_hunks_file_path()
        write_text_file_contents(included_file, "hash1\nhash2\nhash3\n")

        counts = get_hunk_counts()
        assert counts["included"] == 3

    def test_get_hunk_counts_with_skipped(self, temp_git_repo):
        """Test counting skipped hunks (JSONL format)."""
        ensure_state_directory_exists()
        skipped_file = get_skipped_hunks_jsonl_file_path()
        write_text_file_contents(
            skipped_file,
            '{"hash": "h1", "path": "a.py"}\n{"hash": "h2", "path": "b.py"}\n',
        )

        counts = get_hunk_counts()
        assert counts["skipped"] == 2

    def test_get_hunk_counts_with_discarded(self, temp_git_repo):
        """Test counting discarded hunks."""
        ensure_state_directory_exists()
        discarded_file = get_discarded_hunks_file_path()
        write_text_file_contents(discarded_file, "hash1\nhash2\n")

        counts = get_hunk_counts()
        assert counts["discarded"] == 2

    def test_get_hunk_counts_all_types(self, temp_git_repo):
        """Test counting hunks of all types."""
        ensure_state_directory_exists()

        write_text_file_contents(get_included_hunks_file_path(), "h1\nh2\n")
        write_text_file_contents(get_skipped_hunks_jsonl_file_path(), "{}\n{}\n{}\n")
        write_text_file_contents(get_discarded_hunks_file_path(), "h1\n")

        counts = get_hunk_counts()
        assert counts["included"] == 2
        assert counts["skipped"] == 3
        assert counts["discarded"] == 1

    def test_get_hunk_counts_ignores_empty_lines(self, temp_git_repo):
        """Test that empty lines are not counted."""
        ensure_state_directory_exists()
        included_file = get_included_hunks_file_path()
        write_text_file_contents(included_file, "hash1\n\n\nhash2\n")

        counts = get_hunk_counts()
        assert counts["included"] == 2


class TestFileProgress:
    """Tests for get_file_progress function."""

    def test_get_file_progress_no_current_lines(self, temp_git_repo):
        """Test getting file progress when no current lines cached."""
        ensure_state_directory_exists()
        file_index, total = get_file_progress()
        assert file_index == 0
        assert total == 0

    def test_get_file_progress_invalid_json(self, temp_git_repo):
        """Test getting file progress with invalid JSON."""
        ensure_state_directory_exists()
        current_lines_file = get_current_lines_json_file_path()
        write_text_file_contents(current_lines_file, "not valid json")

        file_index, total = get_file_progress()
        assert file_index == 0
        assert total == 0

    def test_get_file_progress_with_current_file(self, temp_git_repo):
        """Test getting file progress with a current file."""
        ensure_state_directory_exists()

        # Create and commit some files first
        (temp_git_repo / "file1.txt").write_text("original1\n")
        (temp_git_repo / "file2.txt").write_text("original2\n")
        (temp_git_repo / "file3.txt").write_text("original3\n")
        subprocess.run(
            ["git", "add", "file1.txt", "file2.txt", "file3.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Now modify them to create diff
        (temp_git_repo / "file1.txt").write_text("change1\n")
        (temp_git_repo / "file2.txt").write_text("change2\n")
        (temp_git_repo / "file3.txt").write_text("change3\n")

        # Set current file to file2.txt
        current_lines_file = get_current_lines_json_file_path()
        write_text_file_contents(current_lines_file, '{"path": "file2.txt"}')

        file_index, total = get_file_progress()
        assert total == 3  # Three files changed
        assert file_index == 2  # file2.txt is second in sorted order

    def test_get_file_progress_file_not_in_diff(self, temp_git_repo):
        """Test getting file progress when cached file is not in diff."""
        ensure_state_directory_exists()

        # Create and commit a file, then modify it
        (temp_git_repo / "file1.txt").write_text("original\n")
        subprocess.run(["git", "add", "file1.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file1"], check=True, cwd=temp_git_repo, capture_output=True)
        (temp_git_repo / "file1.txt").write_text("change\n")

        # Set current file to a different file
        current_lines_file = get_current_lines_json_file_path()
        write_text_file_contents(current_lines_file, '{"path": "nonexistent.txt"}')

        file_index, total = get_file_progress()
        assert total == 1
        assert file_index == 0  # File not found in diff

    def test_get_file_progress_empty_path(self, temp_git_repo):
        """Test getting file progress with empty path in cached data."""
        ensure_state_directory_exists()
        current_lines_file = get_current_lines_json_file_path()
        write_text_file_contents(current_lines_file, '{"path": ""}')

        file_index, total = get_file_progress()
        assert file_index == 0
        assert total == 0
