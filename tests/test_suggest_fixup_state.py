"""Tests for suggest-fixup state management helpers."""

import subprocess

import pytest

from git_stage_batch.commands import (
    _find_next_fixup_candidate,
    _load_suggest_fixup_state,
    _reset_suggest_fixup_state,
    _save_suggest_fixup_state,
    _should_reset_suggest_fixup_state,
    command_start,
)
from git_stage_batch.state import (
    ensure_state_directory_exists,
    get_suggest_fixup_state_file_path,
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


class TestSuggestFixupStateHelpers:
    """Tests for suggest-fixup state management helper functions."""

    def test_load_state_nonexistent(self, temp_git_repo):
        """Test loading state when file doesn't exist."""
        ensure_state_directory_exists()
        state = _load_suggest_fixup_state()
        assert state is None

    def test_save_and_load_state(self, temp_git_repo):
        """Test saving and loading state."""
        ensure_state_directory_exists()

        test_state = {
            "hunk_hash": "abc123",
            "line_ids": None,
            "boundary": "@{upstream}",
            "file_path": "test.txt",
            "min_line": 10,
            "max_line": 20,
            "last_shown_commit": "def456",
            "iteration": 1
        }

        _save_suggest_fixup_state(test_state)

        loaded_state = _load_suggest_fixup_state()
        assert loaded_state == test_state

    def test_reset_state(self, temp_git_repo):
        """Test resetting state removes the file."""
        ensure_state_directory_exists()

        test_state = {"iteration": 1}
        _save_suggest_fixup_state(test_state)

        assert get_suggest_fixup_state_file_path().exists()

        _reset_suggest_fixup_state()

        assert not get_suggest_fixup_state_file_path().exists()
        assert _load_suggest_fixup_state() is None

    def test_should_reset_no_state(self, temp_git_repo):
        """Test that should_reset returns True when no state exists."""
        ensure_state_directory_exists()
        assert _should_reset_suggest_fixup_state(
            "hash1", None, "@{upstream}", "file.txt", 1, 10
        ) is True

    def test_should_reset_hunk_changed(self, temp_git_repo):
        """Test that state resets when hunk hash changes."""
        ensure_state_directory_exists()

        _save_suggest_fixup_state({
            "hunk_hash": "hash1",
            "line_ids": None,
            "boundary": "@{upstream}",
            "file_path": "file.txt",
            "min_line": 1,
            "max_line": 10
        })

        assert _should_reset_suggest_fixup_state(
            "hash2", None, "@{upstream}", "file.txt", 1, 10
        ) is True

    def test_should_reset_boundary_changed(self, temp_git_repo):
        """Test that state resets when boundary changes."""
        ensure_state_directory_exists()

        _save_suggest_fixup_state({
            "hunk_hash": "hash1",
            "line_ids": None,
            "boundary": "@{upstream}",
            "file_path": "file.txt",
            "min_line": 1,
            "max_line": 10
        })

        assert _should_reset_suggest_fixup_state(
            "hash1", None, "main", "file.txt", 1, 10
        ) is True

    def test_should_reset_file_changed(self, temp_git_repo):
        """Test that state resets when file path changes."""
        ensure_state_directory_exists()

        _save_suggest_fixup_state({
            "hunk_hash": "hash1",
            "line_ids": None,
            "boundary": "@{upstream}",
            "file_path": "file1.txt",
            "min_line": 1,
            "max_line": 10
        })

        assert _should_reset_suggest_fixup_state(
            "hash1", None, "@{upstream}", "file2.txt", 1, 10
        ) is True

    def test_should_reset_line_range_changed(self, temp_git_repo):
        """Test that state resets when line range changes."""
        ensure_state_directory_exists()

        _save_suggest_fixup_state({
            "hunk_hash": "hash1",
            "line_ids": None,
            "boundary": "@{upstream}",
            "file_path": "file.txt",
            "min_line": 1,
            "max_line": 10
        })

        assert _should_reset_suggest_fixup_state(
            "hash1", None, "@{upstream}", "file.txt", 5, 15
        ) is True

    def test_should_reset_line_ids_changed(self, temp_git_repo):
        """Test that state resets when line IDs change."""
        ensure_state_directory_exists()

        _save_suggest_fixup_state({
            "hunk_hash": "hash1",
            "line_ids": [1, 2, 3],
            "boundary": "@{upstream}",
            "file_path": "file.txt",
            "min_line": 1,
            "max_line": 10
        })

        assert _should_reset_suggest_fixup_state(
            "hash1", [1, 2], "@{upstream}", "file.txt", 1, 10
        ) is True

    def test_should_not_reset_when_same(self, temp_git_repo):
        """Test that state doesn't reset when all parameters match."""
        ensure_state_directory_exists()

        _save_suggest_fixup_state({
            "hunk_hash": "hash1",
            "line_ids": None,
            "boundary": "@{upstream}",
            "file_path": "file.txt",
            "min_line": 1,
            "max_line": 10
        })

        assert _should_reset_suggest_fixup_state(
            "hash1", None, "@{upstream}", "file.txt", 1, 10
        ) is False


class TestFindNextFixupCandidate:
    """Tests for finding fixup candidates using git log -L."""

    def test_find_candidate_most_recent(self, temp_git_repo):
        """Test finding the most recent commit that modified lines."""
        # Create a file and commit it
        (temp_git_repo / "test.txt").write_text("line1\nline2\nline3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify line 2
        (temp_git_repo / "test.txt").write_text("line1\nmodified\nline3\n")
        subprocess.run(["git", "commit", "-am", "Modify line 2"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create boundary before the modification
        subprocess.run(["git", "branch", "boundary", "HEAD~1"], check=True, cwd=temp_git_repo, capture_output=True)

        # Find candidate
        candidate = _find_next_fixup_candidate("test.txt", 2, 2, "boundary", None)

        assert candidate is not None
        # Verify it's the "Modify line 2" commit
        result = subprocess.run(
            ["git", "log", "--format=%s", "-1", candidate],
            check=True, cwd=temp_git_repo, capture_output=True, text=True
        )
        assert "Modify line 2" in result.stdout

    def test_find_candidate_multiple_commits(self, temp_git_repo):
        """Test finding candidates iteratively through multiple commits."""
        # Create file
        (temp_git_repo / "test.txt").write_text("line1\nline2\nline3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # First modification
        (temp_git_repo / "test.txt").write_text("line1\nv1\nline3\n")
        subprocess.run(["git", "commit", "-am", "Change 1"], check=True, cwd=temp_git_repo, capture_output=True)

        # Second modification
        (temp_git_repo / "test.txt").write_text("line1\nv2\nline3\n")
        subprocess.run(["git", "commit", "-am", "Change 2"], check=True, cwd=temp_git_repo, capture_output=True)

        subprocess.run(["git", "branch", "boundary", "HEAD~2"], check=True, cwd=temp_git_repo, capture_output=True)

        # Find first candidate (most recent)
        candidate1 = _find_next_fixup_candidate("test.txt", 2, 2, "boundary", None)
        assert candidate1 is not None

        # Find second candidate (before first)
        candidate2 = _find_next_fixup_candidate("test.txt", 2, 2, "boundary", candidate1)
        assert candidate2 is not None
        assert candidate2 != candidate1

        # Verify order (candidate1 is more recent than candidate2)
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{candidate2}..{candidate1}"],
            check=True, cwd=temp_git_repo, capture_output=True, text=True
        )
        assert int(result.stdout.strip()) == 1

    def test_find_candidate_no_match(self, temp_git_repo):
        """Test that None is returned when no commits modified the lines."""
        # Create file
        (temp_git_repo / "test.txt").write_text("line1\nline2\nline3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Use HEAD as boundary (no commits between HEAD and HEAD)
        candidate = _find_next_fixup_candidate("test.txt", 2, 2, "HEAD", None)
        assert candidate is None

    def test_find_candidate_exhausted(self, temp_git_repo):
        """Test that None is returned when all candidates exhausted."""
        # Create file with one modification
        (temp_git_repo / "test.txt").write_text("line1\nline2\nline3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        (temp_git_repo / "test.txt").write_text("line1\nmodified\nline3\n")
        subprocess.run(["git", "commit", "-am", "Modify"], check=True, cwd=temp_git_repo, capture_output=True)

        subprocess.run(["git", "branch", "boundary", "HEAD~1"], check=True, cwd=temp_git_repo, capture_output=True)

        # Find first (and only) candidate
        candidate1 = _find_next_fixup_candidate("test.txt", 2, 2, "boundary", None)
        assert candidate1 is not None

        # Try to find another - should be None
        candidate2 = _find_next_fixup_candidate("test.txt", 2, 2, "boundary", candidate1)
        assert candidate2 is None
