"""Tests for git repository location helpers."""

import subprocess
from pathlib import Path

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.git_repository import (
    get_git_repository_root_path,
    require_git_repository,
    resolve_file_path_to_repo_relative,
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


class TestRequireGitRepository:
    """Tests for require_git_repository function."""

    def test_succeeds_in_git_repository(self, temp_git_repo):
        """Test that function succeeds when inside a git repository."""
        require_git_repository()

    def test_exits_outside_git_repository(self, tmp_path, monkeypatch):
        """Test that function exits with error outside git repository."""
        monkeypatch.chdir(tmp_path)

        with pytest.raises(CommandError):
            require_git_repository()


class TestGetGitRepositoryRootPath:
    """Tests for get_git_repository_root_path function."""

    def test_returns_repository_root(self, temp_git_repo):
        """Test that function returns the repository root path."""
        root = get_git_repository_root_path()

        assert isinstance(root, Path)
        assert root.is_absolute()
        assert (root / ".git").exists()

    def test_returns_same_path_from_subdirectory(self, temp_git_repo, monkeypatch):
        """Test that function returns root even from subdirectory."""
        subdir = temp_git_repo / "subdir"
        subdir.mkdir()
        monkeypatch.chdir(subdir)

        root = get_git_repository_root_path()

        assert root == temp_git_repo


class TestResolveFilePathToRepoRelative:
    """Tests for resolve_file_path_to_repo_relative function."""

    def test_resolve_file_path_to_repo_relative_relative(self, temp_git_repo):
        """Test that relative paths are returned as-is."""
        result = resolve_file_path_to_repo_relative("src/file.py")

        assert result == "src/file.py"

    def test_resolve_file_path_to_repo_relative_absolute(self, temp_git_repo):
        """Test that absolute paths inside repo are made relative."""
        absolute_path = str(temp_git_repo / "src" / "file.py")
        result = resolve_file_path_to_repo_relative(absolute_path)

        assert result == "src/file.py"

    def test_resolve_file_path_to_repo_relative_outside_repo(self, temp_git_repo, tmp_path):
        """Test that paths outside repo are returned as-is."""
        outside_path = str(tmp_path / "outside.txt")
        result = resolve_file_path_to_repo_relative(outside_path)

        assert result == outside_path
