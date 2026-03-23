"""Tests for suggest-fixup command infrastructure."""

import json
import subprocess

import pytest

from git_stage_batch.commands.suggest_fixup import (
    _find_next_fixup_candidate,
    _load_suggest_fixup_state,
    _reset_suggest_fixup_state,
    _save_suggest_fixup_state,
    _should_reset_suggest_fixup_state,
)
from git_stage_batch.utils.paths import get_suggest_fixup_state_file_path


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
    """Tests for suggest-fixup state management helpers."""

    def test_load_state_when_no_file_exists(self, temp_git_repo):
        """Test loading state when file doesn't exist returns None."""
        state = _load_suggest_fixup_state()
        assert state is None

    def test_save_and_load_state(self, temp_git_repo):
        """Test saving and loading state."""
        test_state = {
            "hunk_hash": "abc123",
            "line_ids": [1, 2, 3],
            "boundary": "@{upstream}",
            "file_path": "test.py",
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
        test_state = {"hunk_hash": "abc123"}
        _save_suggest_fixup_state(test_state)

        assert get_suggest_fixup_state_file_path().exists()

        _reset_suggest_fixup_state()

        assert not get_suggest_fixup_state_file_path().exists()
        assert _load_suggest_fixup_state() is None

    def test_should_reset_when_no_state_exists(self, temp_git_repo):
        """Test should_reset returns True when no state exists."""
        should_reset = _should_reset_suggest_fixup_state(
            "hash1", [1, 2], "@{upstream}", "test.py", 10, 20
        )
        assert should_reset is True

    def test_should_reset_when_hunk_hash_changes(self, temp_git_repo):
        """Test should_reset returns True when hunk hash changes."""
        _save_suggest_fixup_state({
            "hunk_hash": "hash1",
            "line_ids": [1, 2],
            "boundary": "@{upstream}",
            "file_path": "test.py",
            "min_line": 10,
            "max_line": 20
        })

        should_reset = _should_reset_suggest_fixup_state(
            "hash2", [1, 2], "@{upstream}", "test.py", 10, 20
        )
        assert should_reset is True

    def test_should_reset_when_line_ids_change(self, temp_git_repo):
        """Test should_reset returns True when line IDs change."""
        _save_suggest_fixup_state({
            "hunk_hash": "hash1",
            "line_ids": [1, 2],
            "boundary": "@{upstream}",
            "file_path": "test.py",
            "min_line": 10,
            "max_line": 20
        })

        should_reset = _should_reset_suggest_fixup_state(
            "hash1", [1, 2, 3], "@{upstream}", "test.py", 10, 20
        )
        assert should_reset is True

    def test_should_reset_when_boundary_changes(self, temp_git_repo):
        """Test should_reset returns True when boundary changes."""
        _save_suggest_fixup_state({
            "hunk_hash": "hash1",
            "line_ids": [1, 2],
            "boundary": "@{upstream}",
            "file_path": "test.py",
            "min_line": 10,
            "max_line": 20
        })

        should_reset = _should_reset_suggest_fixup_state(
            "hash1", [1, 2], "HEAD~5", "test.py", 10, 20
        )
        assert should_reset is True

    def test_should_not_reset_when_parameters_match(self, temp_git_repo):
        """Test should_reset returns False when all parameters match."""
        _save_suggest_fixup_state({
            "hunk_hash": "hash1",
            "line_ids": [1, 2],
            "boundary": "@{upstream}",
            "file_path": "test.py",
            "min_line": 10,
            "max_line": 20
        })

        should_reset = _should_reset_suggest_fixup_state(
            "hash1", [1, 2], "@{upstream}", "test.py", 10, 20
        )
        assert should_reset is False


class TestFindNextFixupCandidate:
    """Tests for finding fixup candidates."""

    def test_find_candidate_in_simple_history(self, temp_git_repo):
        """Test finding a commit that modified lines."""
        # Create a file and commit it
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify lines and commit
        test_file.write_text("line 1 modified\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Modify line 1"], check=True, cwd=temp_git_repo, capture_output=True)

        # Get the commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        expected_commit = result.stdout.strip()

        # Find candidate for line 1
        candidate = _find_next_fixup_candidate("test.py", 1, 1, "HEAD~2", None)

        assert candidate == expected_commit

    def test_find_returns_none_when_no_commits(self, temp_git_repo):
        """Test finding candidate returns None when no commits match."""
        # Create a file and commit it
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        # Try to find candidate with boundary that excludes all commits
        candidate = _find_next_fixup_candidate("test.py", 1, 1, "HEAD", None)

        assert candidate is None

    def test_find_iterates_through_multiple_commits(self, temp_git_repo):
        """Test finding multiple candidates by iteration."""
        # Create a file
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        result = subprocess.run(["git", "rev-parse", "HEAD"], check=True, cwd=temp_git_repo, capture_output=True, text=True)
        commit0 = result.stdout.strip()

        # First modification
        test_file.write_text("line 1 v2\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Modify v2"], check=True, cwd=temp_git_repo, capture_output=True)
        result = subprocess.run(["git", "rev-parse", "HEAD"], check=True, cwd=temp_git_repo, capture_output=True, text=True)
        commit1 = result.stdout.strip()

        # Second modification
        test_file.write_text("line 1 v3\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Modify v3"], check=True, cwd=temp_git_repo, capture_output=True)
        result = subprocess.run(["git", "rev-parse", "HEAD"], check=True, cwd=temp_git_repo, capture_output=True, text=True)
        commit2 = result.stdout.strip()

        # Find first candidate (most recent)
        candidate1 = _find_next_fixup_candidate("test.py", 1, 1, "HEAD~3", None)
        assert candidate1 == commit2

        # Find second candidate (pass first as last_shown)
        candidate2 = _find_next_fixup_candidate("test.py", 1, 1, "HEAD~3", candidate1)
        assert candidate2 == commit1

        # Find third candidate (the original addition)
        candidate3 = _find_next_fixup_candidate("test.py", 1, 1, "HEAD~3", candidate2)
        assert candidate3 == commit0

        # Find fourth candidate (should be None - exhausted)
        candidate4 = _find_next_fixup_candidate("test.py", 1, 1, "HEAD~3", candidate3)
        assert candidate4 is None
