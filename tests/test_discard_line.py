"""Tests for discard --line command."""

import subprocess

import pytest

from git_stage_batch.commands import command_discard_line, command_show
from git_stage_batch.state import get_git_repository_root_path


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


class TestCommandDiscardLine:
    """Tests for discard --line command."""

    def test_discard_line_requires_current_hunk(self, temp_git_repo):
        """Test that discard --line requires an active hunk."""
        with pytest.raises(SystemExit):
            command_discard_line("1")

    def test_discard_line_removes_single_addition(self, temp_git_repo):
        """Test discarding a single added line."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew line\n")

        # Show to cache the hunk
        command_show()

        # Discard line 1
        command_discard_line("1")

        # Check that the line was removed from working tree
        content = readme.read_text()
        assert content == "# Test\n"
        assert "New line" not in content

    def test_discard_line_restores_single_deletion(self, temp_git_repo):
        """Test discarding a single deleted line (restores it)."""
        readme = temp_git_repo / "README.md"
        readme.write_text("")

        # Show to cache the hunk
        command_show()

        # Discard line 1 (the deletion)
        command_discard_line("1")

        # Check that the line was restored in working tree
        content = readme.read_text()
        assert content == "# Test\n"

    def test_discard_line_with_range(self, temp_git_repo):
        """Test discarding a range of lines."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Show to cache the hunk
        command_show()

        # Discard lines 1-2
        command_discard_line("1-2")

        # Check that lines 1-2 were removed but line 3 remains
        content = readme.read_text()
        assert content == "# Test\nLine 3\n"

    def test_discard_line_partial_selection(self, temp_git_repo):
        """Test discarding only some lines from a hunk."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Show to cache the hunk
        command_show()

        # Discard only line 2
        command_discard_line("2")

        # Check that only line 2 was removed
        content = readme.read_text()
        assert content == "# Test\nLine 1\nLine 3\n"

    def test_discard_line_mixed_changes(self, temp_git_repo):
        """Test discarding from a hunk with both additions and deletions."""
        readme = temp_git_repo / "README.md"
        # Start with some content
        readme.write_text("# Test\nOld line 1\nOld line 2\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add content"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make changes: delete old lines, add new lines
        readme.write_text("# Test\nNew line 1\nNew line 2\n")

        # Show to cache the hunk
        command_show()

        # Discard the first change (deletion of "Old line 1")
        command_discard_line("1")

        # Check that the deletion was undone (old line restored)
        content = readme.read_text()
        assert "Old line 1" in content

    def test_discard_line_invalid_file_path(self, temp_git_repo):
        """Test discarding from a file that doesn't exist in working tree."""
        # Create a file and commit it
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("content\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Delete the file (shows as deletions in diff)
        test_file.unlink()

        # Show to cache the hunk
        command_show()

        # Try to discard - should fail because file doesn't exist
        with pytest.raises(SystemExit):
            command_discard_line("1")
