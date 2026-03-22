"""Tests for block-file command."""

import subprocess

import pytest

from git_stage_batch.commands.block_file import command_block_file
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.file_io import read_file_paths_file
from git_stage_batch.utils.git import get_gitignore_path
from git_stage_batch.utils.paths import get_blocked_files_file_path


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


class TestCommandBlockFile:
    """Tests for block-file command."""

    def test_block_file_requires_argument(self, temp_git_repo):
        """Test that block-file requires a file path argument."""
        with pytest.raises(CommandError):
            command_block_file("")

    def test_block_file_adds_to_gitignore(self, temp_git_repo, capsys):
        """Test that block-file adds file to .gitignore."""
        # Create untracked file
        (temp_git_repo / "unwanted.txt").write_text("ignore me\n")

        # Block the file
        command_block_file("unwanted.txt")

        # Check .gitignore
        gitignore = get_gitignore_path()
        content = gitignore.read_text()
        assert "unwanted.txt\n" in content

        captured = capsys.readouterr()
        assert "Blocked file: unwanted.txt" in captured.out

    def test_block_file_adds_to_blocked_list(self, temp_git_repo):
        """Test that block-file adds file to blocked-files state."""
        # Create untracked file
        (temp_git_repo / "blocked.txt").write_text("blocked content\n")

        # Block it
        command_block_file("blocked.txt")

        # Verify it's in blocked list
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "blocked.txt" in blocked

    def test_block_file_resolves_absolute_path(self, temp_git_repo):
        """Test that block-file resolves absolute paths to repo-relative."""
        # Create untracked file
        (temp_git_repo / "file.txt").write_text("content\n")

        # Block using absolute path
        abs_path = str(temp_git_repo / "file.txt")
        command_block_file(abs_path)

        # Should be stored as relative path
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "file.txt" in blocked

        # .gitignore should have relative path
        gitignore = get_gitignore_path()
        content = gitignore.read_text()
        assert "file.txt\n" in content

    def test_block_file_no_duplicates_in_gitignore(self, temp_git_repo):
        """Test that blocking the same file twice doesn't duplicate entries."""
        (temp_git_repo / "dup.txt").write_text("content\n")

        # Block twice
        command_block_file("dup.txt")
        command_block_file("dup.txt")

        # Should only appear once in .gitignore
        gitignore = get_gitignore_path()
        content = gitignore.read_text()
        assert content.count("dup.txt") == 1

    def test_block_file_no_duplicates_in_blocked_list(self, temp_git_repo):
        """Test that blocking the same file twice doesn't duplicate in blocked list."""
        (temp_git_repo / "dup.txt").write_text("content\n")

        # Block twice
        command_block_file("dup.txt")
        command_block_file("dup.txt")

        # Should only appear once in blocked list
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert blocked.count("dup.txt") == 1

    def test_block_file_with_subdirectory(self, temp_git_repo):
        """Test blocking a file in a subdirectory."""
        # Create subdirectory and file
        subdir = temp_git_repo / "src"
        subdir.mkdir()
        (subdir / "file.txt").write_text("content\n")

        # Block it
        command_block_file("src/file.txt")

        # Check blocked list
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "src/file.txt" in blocked

        # Check .gitignore
        gitignore = get_gitignore_path()
        content = gitignore.read_text()
        assert "src/file.txt\n" in content
