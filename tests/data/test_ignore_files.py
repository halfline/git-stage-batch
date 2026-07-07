"""Tests for repository ignore-file editing helpers."""

import subprocess

import pytest

from git_stage_batch.data.ignore_files import (
    add_file_to_gitignore,
    get_gitignore_path,
    read_gitignore_lines,
    remove_file_from_gitignore,
    write_gitignore_lines,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    return repo


class TestGitignoreManipulation:
    """Tests for .gitignore manipulation functions."""

    def test_read_gitignore_lines_nonexistent(self, temp_git_repo):
        """Test reading .gitignore when it doesn't exist."""

        lines = read_gitignore_lines()
        assert lines == []

    def test_read_gitignore_lines_existing(self, temp_git_repo):
        """Test reading existing .gitignore."""

        gitignore = get_gitignore_path()
        gitignore.write_text("*.pyc\n__pycache__/\n.env\n")

        lines = read_gitignore_lines()
        assert lines == ["*.pyc\n", "__pycache__/\n", ".env\n"]

    def test_write_gitignore_lines(self, temp_git_repo):
        """Test writing .gitignore lines."""

        lines = ["*.pyc\n", "__pycache__/\n", ".env\n"]
        write_gitignore_lines(lines)

        gitignore = get_gitignore_path()
        content = gitignore.read_text()
        assert content == "*.pyc\n__pycache__/\n.env\n"

    def test_add_file_to_gitignore_new(self, temp_git_repo):
        """Test adding a file to .gitignore when .gitignore doesn't exist."""

        add_file_to_gitignore("test.txt")

        lines = read_gitignore_lines()
        assert "test.txt\n" in lines

    def test_add_file_to_gitignore_existing(self, temp_git_repo):
        """Test adding a file to existing .gitignore."""

        gitignore = get_gitignore_path()
        gitignore.write_text("*.pyc\n__pycache__/\n")

        add_file_to_gitignore("test.txt")

        lines = read_gitignore_lines()
        assert "*.pyc\n" in lines
        assert "__pycache__/\n" in lines
        assert "test.txt\n" in lines

    def test_add_file_to_gitignore_no_duplicates(self, temp_git_repo):
        """Test adding a file twice doesn't create duplicates."""

        add_file_to_gitignore("test.txt")
        add_file_to_gitignore("test.txt")

        content = get_gitignore_path().read_text()
        assert content.count("test.txt") == 1

    def test_add_file_to_gitignore_preserves_no_trailing_newline(
        self,
        temp_git_repo,
    ):
        """Test adding to .gitignore when existing file has no trailing newline."""

        gitignore = get_gitignore_path()
        gitignore.write_text("*.pyc")

        add_file_to_gitignore("test.txt")

        content = gitignore.read_text()
        assert content == "*.pyc\ntest.txt\n"

    def test_remove_file_from_gitignore_with_marker(self, temp_git_repo):
        """Test removing a file from .gitignore."""

        add_file_to_gitignore("test.txt")

        removed = remove_file_from_gitignore("test.txt")
        assert removed is True

        lines = read_gitignore_lines()
        assert "test.txt\n" not in lines

    def test_remove_file_from_gitignore_without_marker(self, temp_git_repo):
        """Test that we can remove any entry from .gitignore."""

        gitignore = get_gitignore_path()
        gitignore.write_text("test.txt\n*.pyc\n")

        removed = remove_file_from_gitignore("test.txt")
        assert removed is True

        lines = read_gitignore_lines()
        assert "test.txt\n" not in lines
        assert "*.pyc\n" in lines

    def test_remove_file_from_gitignore_preserves_other_entries(
        self,
        temp_git_repo,
    ):
        """Test that removing one entry preserves others."""

        gitignore = get_gitignore_path()
        gitignore.write_text("*.pyc\n")

        add_file_to_gitignore("test1.txt")
        add_file_to_gitignore("test2.txt")

        remove_file_from_gitignore("test1.txt")

        lines = read_gitignore_lines()
        assert "*.pyc\n" in lines
        assert "test1.txt\n" not in lines
        assert "test2.txt\n" in lines

    def test_remove_file_from_gitignore_nonexistent(self, temp_git_repo):
        """Test removing a file that doesn't exist in .gitignore."""

        gitignore = get_gitignore_path()
        gitignore.write_text("*.pyc\n")

        removed = remove_file_from_gitignore("nonexistent.txt")
        assert removed is False

        assert gitignore.read_text() == "*.pyc\n"
