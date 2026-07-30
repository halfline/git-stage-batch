"""Tests for progress tracking functions."""

import subprocess

import pytest

from git_stage_batch.data.progress import (
    get_hunk_counts,
    record_hunk_discarded,
    record_hunk_included,
)
from git_stage_batch.utils.file_io import write_text_file_contents
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
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


class TestRecordHunkFunctions:
    """Tests for hunk recording functions."""

    def test_record_hunk_included(self, temp_git_repo):
        """Test recording included hunk."""
        ensure_state_directory_exists()

        record_hunk_included("abc123")

        included_file = get_included_hunks_file_path()
        assert included_file.exists()
        assert "abc123" in included_file.read_text()

    def test_record_hunk_discarded(self, temp_git_repo):
        """Test recording discarded hunk."""
        ensure_state_directory_exists()

        record_hunk_discarded("xyz789")

        discarded_file = get_discarded_hunks_file_path()
        assert discarded_file.exists()
        assert "xyz789" in discarded_file.read_text()


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
