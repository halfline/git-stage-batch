"""Tests for suggest-fixup command infrastructure."""

from git_stage_batch.commands.stop import command_stop
from git_stage_batch.commands.suggest_fixup import command_suggest_fixup
from git_stage_batch.commands.suggest_fixup import command_suggest_fixup_line

import json
import subprocess

import pytest

from git_stage_batch.commands.suggest_fixup import (
    _find_next_fixup_candidate,
    _load_suggest_fixup_state,
    _reset_suggest_fixup_state,
    _save_suggest_fixup_state,
    _should_reset_suggest_fixup_state,
    command_suggest_fixup,
    command_suggest_fixup_line,
)
from git_stage_batch.commands.start import command_start
from git_stage_batch.data.hunk_tracking import fetch_next_change
from git_stage_batch.exceptions import CommandError
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


class TestCommandSuggestFixup:
    """Tests for suggest-fixup command."""

    def test_suggest_fixup_requires_selected_hunk(self, temp_git_repo):
        """Test that suggest-fixup requires an active hunk."""
        with pytest.raises(CommandError):
            command_suggest_fixup()

    def test_suggest_fixup_finds_commit(self, temp_git_repo, capsys):
        """Test finding a commit that modified selected hunk."""
        # Create a file
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify and commit
        test_file.write_text("line 1 modified\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Modify line 1"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create changes in working tree
        test_file.write_text("line 1 modified again\n")

        # Start session and cache hunk
        command_start()
        fetch_next_change()

        # Suggest fixup with boundary that includes the modification
        command_suggest_fixup(boundary="HEAD~2")

        captured = capsys.readouterr()
        assert "Candidate 1:" in captured.out
        assert "Modify line 1" in captured.out
        assert "git commit --fixup=" in captured.out

    def test_suggest_fixup_abort_clears_state(self, temp_git_repo):
        """Test that abort flag clears state."""
        # Create state
        _save_suggest_fixup_state({"hunk_hash": "test"})
        assert get_suggest_fixup_state_file_path().exists()

        command_suggest_fixup(abort=True)

        assert not get_suggest_fixup_state_file_path().exists()

    def test_suggest_fixup_reset_restarts_iteration(self, temp_git_repo, capsys):
        """Test that reset flag restarts iteration."""
        # Create file with modifications
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("line 1 v2\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Modify v2"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("line 1 v3\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Modify v3"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("line 1 v4\n")

        command_start()
        fetch_next_change()

        # First call
        command_suggest_fixup(boundary="HEAD~3")
        captured = capsys.readouterr()
        assert "Candidate 1:" in captured.out

        # Second call with reset
        command_suggest_fixup(boundary="HEAD~3", reset=True)
        captured = capsys.readouterr()
        assert "Candidate 1:" in captured.out  # Should restart at 1


class TestCommandSuggestFixupLine:
    """Tests for suggest-fixup --line command."""

    def test_suggest_fixup_line_requires_selected_hunk(self, temp_git_repo):
        """Test that suggest-fixup --line requires an active hunk."""
        with pytest.raises(CommandError):
            command_suggest_fixup_line("1")

    def test_suggest_fixup_line_finds_commit_for_specific_lines(self, temp_git_repo, capsys):
        """Test finding commit for specific lines."""
        # Create a file with multiple lines
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify only line 1
        test_file.write_text("line 1 modified\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Modify line 1"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify both lines in working tree
        test_file.write_text("line 1 modified again\nline 2 modified\nline 3\n")

        command_start()
        fetch_next_change()

        # Suggest fixup for line 1 only
        command_suggest_fixup_line("1", boundary="HEAD~2")

        captured = capsys.readouterr()
        assert "Candidate 1:" in captured.out
        assert "Modify line 1" in captured.out

    def test_suggest_fixup_line_errors_on_new_lines(self, temp_git_repo, capsys):
        """Test error when all specified lines are new."""


        # Create empty file
        test_file = temp_git_repo / "test.py"
        test_file.write_text("")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add empty file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add new lines (all additions, no old line numbers)
        test_file.write_text("new line 1\nnew line 2\n")

        command_start()

        # Should error because all lines are additions
        with pytest.raises(CommandError) as exc_info:
            command_suggest_fixup_line("1,2", boundary="HEAD~1")

        assert "No old line numbers" in str(exc_info.value.message)

        command_stop()

        # Modify in second commit
        test_file.write_text("modified line 1\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Modify test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create a working tree change
        test_file.write_text("final change\n")

        command_start()

        captured = capsys.readouterr()

        # Run suggest-fixup with --porcelain
        command_suggest_fixup(boundary="HEAD~2", porcelain=True)

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Verify JSON structure
        assert "candidate" in output
        assert "iteration" in output
        assert "boundary" in output

        assert "hash" in output["candidate"]
        assert "full_hash" in output["candidate"]
        assert "subject" in output["candidate"]
        assert "author" in output["candidate"]
        assert "date" in output["candidate"]
        assert "relative_date" in output["candidate"]

        assert output["iteration"] == 1
        assert output["boundary"] == "HEAD~2"

    def test_suggest_fixup_porcelain_abort_silent(self, temp_git_repo, capsys):
        """Test that suggest-fixup --porcelain --abort produces no output."""

        command_suggest_fixup(abort=True, porcelain=True)

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_suggest_fixup_line_porcelain_outputs_json(self, temp_git_repo, capsys):
        """Test that suggest-fixup-line --porcelain outputs JSON."""

        # Create base commit
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\nline 2\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify in second commit
        test_file.write_text("modified 1\nmodified 2\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Modify test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create working tree change
        test_file.write_text("final 1\nfinal 2\n")

        command_start()
        capsys.readouterr()

        # Run suggest-fixup-line with --porcelain
        command_suggest_fixup_line("1", boundary="HEAD~2", porcelain=True)

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Verify JSON structure (same as suggest-fixup)
        assert "candidate" in output
        assert "iteration" in output
        assert "boundary" in output
        assert output["iteration"] == 1
