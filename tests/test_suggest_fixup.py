"""Tests for suggest-fixup commands."""

import subprocess

import pytest

from git_stage_batch.commands import (
    command_show,
    command_start,
    command_suggest_fixup,
    command_suggest_fixup_line,
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


class TestCommandSuggestFixup:
    """Tests for command_suggest_fixup."""

    def test_suggest_fixup_finds_most_recent_commit(self, temp_git_repo, capsys):
        """Test that suggest-fixup finds the most recent commit that modified lines."""
        # Create a series of commits modifying the same line
        (temp_git_repo / "test.txt").write_text("line1\nchanged once\nline3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "First change"], check=True, cwd=temp_git_repo, capture_output=True)

        (temp_git_repo / "test.txt").write_text("line1\nchanged twice\nline3\n")
        subprocess.run(["git", "commit", "-am", "Second change"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make a working tree change
        (temp_git_repo / "test.txt").write_text("line1\nworking tree change\nline3\n")

        # Start session and load hunk
        command_start()
        command_show()
        capsys.readouterr()  # Clear output

        # Create a fake upstream ref
        subprocess.run(["git", "branch", "fake-upstream", "HEAD~2"], check=True, cwd=temp_git_repo, capture_output=True)

        command_suggest_fixup("fake-upstream")
        captured = capsys.readouterr()

        # Should suggest the most recent commit (Second change)
        assert "Candidate 1:" in captured.out
        assert "Second change" in captured.out
        assert "git commit --fixup=" in captured.out

    def test_suggest_fixup_iterates_through_candidates(self, temp_git_repo, capsys):
        """Test that repeated calls show progressively older commits."""
        # Create multiple commits modifying the same line
        (temp_git_repo / "test.txt").write_text("line1\nv1\nline3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Commit 1"], check=True, cwd=temp_git_repo, capture_output=True)

        (temp_git_repo / "test.txt").write_text("line1\nv2\nline3\n")
        subprocess.run(["git", "commit", "-am", "Commit 2"], check=True, cwd=temp_git_repo, capture_output=True)

        (temp_git_repo / "test.txt").write_text("line1\nv3\nline3\n")
        subprocess.run(["git", "commit", "-am", "Commit 3"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make working tree change
        (temp_git_repo / "test.txt").write_text("line1\nworking\nline3\n")

        command_start()
        command_show()
        capsys.readouterr()

        # Create upstream ref
        subprocess.run(["git", "branch", "fake-upstream", "HEAD~3"], check=True, cwd=temp_git_repo, capture_output=True)

        # First call - should show Commit 3
        command_suggest_fixup("fake-upstream")
        captured = capsys.readouterr()
        assert "Candidate 1:" in captured.out
        assert "Commit 3" in captured.out

        # Second call - should show Commit 2
        command_suggest_fixup("fake-upstream")
        captured = capsys.readouterr()
        assert "Candidate 2:" in captured.out
        assert "Commit 2" in captured.out

        # Third call - should show Commit 1
        command_suggest_fixup("fake-upstream")
        captured = capsys.readouterr()
        assert "Candidate 3:" in captured.out
        assert "Commit 1" in captured.out

        # Fourth call - no more candidates
        with pytest.raises(SystemExit):
            command_suggest_fixup("fake-upstream")
        captured = capsys.readouterr()
        assert "No more candidates found" in captured.out

    def test_suggest_fixup_reset_flag(self, temp_git_repo, capsys):
        """Test that --reset flag restarts from the most recent candidate."""
        # Create commits
        (temp_git_repo / "test.txt").write_text("line1\nv1\nline3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Commit 1"], check=True, cwd=temp_git_repo, capture_output=True)

        (temp_git_repo / "test.txt").write_text("line1\nv2\nline3\n")
        subprocess.run(["git", "commit", "-am", "Commit 2"], check=True, cwd=temp_git_repo, capture_output=True)

        (temp_git_repo / "test.txt").write_text("line1\nworking\nline3\n")

        command_start()
        command_show()
        capsys.readouterr()

        subprocess.run(["git", "branch", "fake-upstream", "HEAD~2"], check=True, cwd=temp_git_repo, capture_output=True)

        # First call
        command_suggest_fixup("fake-upstream")
        captured = capsys.readouterr()
        assert "Candidate 1:" in captured.out

        # Second call
        command_suggest_fixup("fake-upstream")
        captured = capsys.readouterr()
        assert "Candidate 2:" in captured.out

        # Reset - should restart at Candidate 1
        command_suggest_fixup("fake-upstream", reset=True)
        captured = capsys.readouterr()
        assert "Candidate 1:" in captured.out
        assert "Commit 2" in captured.out

    def test_suggest_fixup_abort_flag(self, temp_git_repo, capsys):
        """Test that --abort flag clears state without showing candidates."""
        # Create commit
        (temp_git_repo / "test.txt").write_text("line1\nv1\nline3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Commit 1"], check=True, cwd=temp_git_repo, capture_output=True)

        (temp_git_repo / "test.txt").write_text("line1\nworking\nline3\n")

        command_start()
        command_show()
        capsys.readouterr()

        subprocess.run(["git", "branch", "fake-upstream", "HEAD~1"], check=True, cwd=temp_git_repo, capture_output=True)

        # Start iteration
        command_suggest_fixup("fake-upstream")
        capsys.readouterr()

        # Abort - should clear state without showing candidates
        command_suggest_fixup("fake-upstream", abort=True)
        captured = capsys.readouterr()
        assert "Suggest-fixup iteration cleared" in captured.out
        assert "Candidate" not in captured.out

        # Next call should start fresh at Candidate 1
        command_suggest_fixup("fake-upstream")
        captured = capsys.readouterr()
        assert "Candidate 1:" in captured.out

    def test_suggest_fixup_no_commits_in_range(self, temp_git_repo, capsys):
        """Test that suggest-fixup exits gracefully when no commits are found."""
        # Create and commit a file first
        (temp_git_repo / "test.txt").write_text("line1\nline2\nline3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make a change
        (temp_git_repo / "test.txt").write_text("line1\nmodified\nline3\n")

        command_start()
        command_show()
        capsys.readouterr()

        # Use HEAD as boundary (no commits between HEAD and HEAD)
        with pytest.raises(SystemExit):
            command_suggest_fixup("HEAD")
        captured = capsys.readouterr()
        assert "No commits found in range" in captured.err

    def test_suggest_fixup_no_current_hunk(self, temp_git_repo, capsys):
        """Test that suggest-fixup requires an active session."""
        with pytest.raises(SystemExit):
            command_suggest_fixup("HEAD~1")
        captured = capsys.readouterr()
        assert "No current hunk" in captured.err


class TestCommandSuggestFixupLine:
    """Tests for command_suggest_fixup_line."""

    def test_suggest_fixup_line_with_specific_lines(self, temp_git_repo, capsys):
        """Test suggest-fixup with --line flag for specific line IDs."""
        # Create commit modifying line 2
        (temp_git_repo / "test.txt").write_text("line1\nchanged\nline3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Change line 2"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make change to the same line
        (temp_git_repo / "test.txt").write_text("line1\nworking\nline3\n")

        command_start()
        command_show()
        capsys.readouterr()

        subprocess.run(["git", "branch", "fake-upstream", "HEAD~1"], check=True, cwd=temp_git_repo, capture_output=True)

        # Suggest fixup for line 1 (which is the changed line in the hunk)
        command_suggest_fixup_line("1", "fake-upstream")
        captured = capsys.readouterr()

        assert "Candidate 1:" in captured.out
        assert "Change line 2" in captured.out

    def test_suggest_fixup_line_iterates_independently(self, temp_git_repo, capsys):
        """Test that line-specific suggest-fixup has independent state."""
        # Create commits
        (temp_git_repo / "test.txt").write_text("line1\nv1\nline3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Commit 1"], check=True, cwd=temp_git_repo, capture_output=True)

        (temp_git_repo / "test.txt").write_text("line1\nv2\nline3\n")
        subprocess.run(["git", "commit", "-am", "Commit 2"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make change
        (temp_git_repo / "test.txt").write_text("line1\nworking\nline3\n")

        command_start()
        command_show()
        capsys.readouterr()

        subprocess.run(["git", "branch", "fake-upstream", "HEAD~2"], check=True, cwd=temp_git_repo, capture_output=True)

        # Call regular suggest-fixup
        command_suggest_fixup("fake-upstream")
        capsys.readouterr()

        # Call line-specific suggest-fixup - should reset and start fresh
        command_suggest_fixup_line("1", "fake-upstream")
        captured = capsys.readouterr()
        assert "Candidate 1:" in captured.out

    def test_suggest_fixup_line_no_old_lines(self, temp_git_repo, capsys):
        """Test suggest-fixup-line with newly added lines (no old line numbers)."""
        # Create file
        (temp_git_repo / "test.txt").write_text("line1\nline2\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add new lines
        (temp_git_repo / "test.txt").write_text("line1\nnew line\nline2\n")

        command_start()
        command_show()
        capsys.readouterr()

        subprocess.run(["git", "branch", "fake-upstream", "HEAD~1"], check=True, cwd=temp_git_repo, capture_output=True)

        # Try to suggest fixup for the new line (ID 1)
        with pytest.raises(SystemExit):
            command_suggest_fixup_line("1", "fake-upstream")
        captured = capsys.readouterr()
        assert "No old line numbers found" in captured.err
