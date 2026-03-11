"""Tests for skip --line command."""

import subprocess

import pytest

from git_stage_batch.commands import command_show, command_skip_line
from git_stage_batch.state import get_processed_skip_ids_file_path, read_text_file_contents


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


class TestCommandSkipLine:
    """Tests for skip --line command."""

    def test_skip_line_requires_current_hunk(self, temp_git_repo):
        """Test that skip --line requires an active hunk."""
        with pytest.raises(SystemExit):
            command_skip_line("1")

    def test_skip_line_marks_single_addition(self, temp_git_repo):
        """Test skipping a single added line."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew line\n")

        # Show to cache the hunk
        command_show()

        # Skip line 1
        command_skip_line("1")

        # Check that skip IDs were recorded
        skip_ids = read_text_file_contents(get_processed_skip_ids_file_path()).strip().split("\n")
        assert skip_ids == ["1"]

    def test_skip_line_marks_single_deletion(self, temp_git_repo):
        """Test skipping a single deleted line."""
        readme = temp_git_repo / "README.md"
        readme.write_text("")

        # Show to cache the hunk
        command_show()

        # Skip line 1
        command_skip_line("1")

        # Check that skip IDs were recorded
        skip_ids = read_text_file_contents(get_processed_skip_ids_file_path()).strip().split("\n")
        assert skip_ids == ["1"]

    def test_skip_line_with_range(self, temp_git_repo):
        """Test skipping a range of lines."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Show to cache the hunk
        command_show()

        # Skip lines 1-3
        command_skip_line("1-3")

        # Check that all IDs were recorded
        skip_ids = read_text_file_contents(get_processed_skip_ids_file_path()).strip().split("\n")
        assert skip_ids == ["1", "2", "3"]

    def test_skip_line_partial_selection(self, temp_git_repo):
        """Test skipping only some lines from a hunk."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Show to cache the hunk
        command_show()

        # Skip only line 1
        command_skip_line("1")

        # Check that only line 1 was recorded
        skip_ids = read_text_file_contents(get_processed_skip_ids_file_path()).strip().split("\n")
        assert skip_ids == ["1"]

    def test_skip_line_accumulates_ids(self, temp_git_repo):
        """Test that multiple skip --line calls accumulate."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Show to cache the hunk
        command_show()

        # Skip line 1
        command_skip_line("1")

        # Skip line 3
        command_skip_line("3")

        # Check that both IDs were recorded
        skip_ids = read_text_file_contents(get_processed_skip_ids_file_path()).strip().split("\n")
        assert set(skip_ids) == {"1", "3"}
