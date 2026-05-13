"""Tests for auto_add_untracked_files function."""

import subprocess

import pytest

from git_stage_batch.data.file_tracking import auto_add_untracked_files
from git_stage_batch.utils.file_io import read_file_paths_file
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_auto_added_files_file_path,
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


class TestAutoAddUntrackedFiles:
    """Tests for auto_add_untracked_files."""

    def test_auto_add_single_untracked_file(self, temp_git_repo):
        """Test auto-adding a single untracked file."""
        ensure_state_directory_exists()

        # Create an untracked file
        new_file = temp_git_repo / "new.txt"
        new_file.write_text("content\n")

        auto_add_untracked_files()

        # Check file is in auto-added list
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert "new.txt" in auto_added

        # Verify file was added with -N (intent-to-add)
        result = subprocess.run(
            ["git", "ls-files", "--", "new.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "new.txt" in result.stdout

        # Verify file content is not staged by git add -N.
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "new.txt" not in result.stdout

    def test_auto_add_multiple_untracked_files(self, temp_git_repo):
        """Test auto-adding multiple untracked files."""
        ensure_state_directory_exists()

        # Create multiple untracked files
        (temp_git_repo / "file1.txt").write_text("content1\n")
        (temp_git_repo / "file2.py").write_text("print('hello')\n")
        (temp_git_repo / "file3.md").write_text("# Header\n")

        auto_add_untracked_files()

        # Check all files are in auto-added list
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert "file1.txt" in auto_added
        assert "file2.py" in auto_added
        assert "file3.md" in auto_added

    def test_auto_add_respects_gitignore(self, temp_git_repo):
        """Test that files matching .gitignore patterns are not auto-added."""
        ensure_state_directory_exists()

        # Create .gitignore
        gitignore = temp_git_repo / ".gitignore"
        gitignore.write_text("*.log\n*.tmp\n")
        subprocess.run(["git", "add", ".gitignore"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add gitignore"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create files, some matching .gitignore
        (temp_git_repo / "normal.txt").write_text("content\n")
        (temp_git_repo / "debug.log").write_text("log content\n")
        (temp_git_repo / "temp.tmp").write_text("temp content\n")

        auto_add_untracked_files()

        # Check only normal file was auto-added
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert "normal.txt" in auto_added
        assert "debug.log" not in auto_added
        assert "temp.tmp" not in auto_added

    def test_auto_add_is_idempotent(self, temp_git_repo):
        """Test that calling auto_add multiple times doesn't cause issues."""
        ensure_state_directory_exists()

        # Create an untracked file
        (temp_git_repo / "file.txt").write_text("content\n")

        # Call auto_add twice
        auto_add_untracked_files()
        auto_add_untracked_files()

        # File should only appear once in auto-added list
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert auto_added.count("file.txt") == 1

    def test_auto_add_accepts_target_paths(self, temp_git_repo):
        """Test auto-adding only the requested untracked paths."""
        ensure_state_directory_exists()

        (temp_git_repo / "target.txt").write_text("target\n")
        (temp_git_repo / "other.txt").write_text("other\n")

        auto_add_untracked_files(["target.txt"])

        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert auto_added == ["target.txt"]

        result = subprocess.run(
            ["git", "ls-files", "--", "target.txt", "other.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "target.txt" in result.stdout
        assert "other.txt" not in result.stdout

    def test_auto_add_reruns_for_recorded_untracked_path(self, temp_git_repo):
        """Test re-adding a recorded path after its intent-to-add entry is removed."""
        ensure_state_directory_exists()

        (temp_git_repo / "file.txt").write_text("content\n")
        auto_add_untracked_files()

        subprocess.run(
            ["git", "restore", "--staged", "file.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        auto_add_untracked_files(["file.txt"])

        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert auto_added.count("file.txt") == 1

        result = subprocess.run(
            ["git", "ls-files", "--", "file.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "file.txt"

    def test_auto_add_handles_no_untracked_files(self, temp_git_repo):
        """Test auto_add when there are no untracked files."""
        ensure_state_directory_exists()

        # No untracked files exist
        auto_add_untracked_files()

        # Auto-added list should be empty
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert auto_added == []

    def test_auto_add_with_nested_directories(self, temp_git_repo):
        """Test auto-adding files in nested directories."""
        ensure_state_directory_exists()

        # Create nested directory structure
        subdir = temp_git_repo / "src" / "lib"
        subdir.mkdir(parents=True)
        (subdir / "module.py").write_text("def foo(): pass\n")

        auto_add_untracked_files()

        # Check nested file was auto-added
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert "src/lib/module.py" in auto_added

        # Verify it's tracked
        result = subprocess.run(
            ["git", "ls-files", "--", "src/lib/module.py"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "src/lib/module.py" in result.stdout

    def test_auto_add_after_some_already_added(self, temp_git_repo):
        """Test auto_add when some files are already auto-added."""
        ensure_state_directory_exists()

        # Create and auto-add first file
        (temp_git_repo / "file1.txt").write_text("content1\n")
        auto_add_untracked_files()

        # Create second file
        (temp_git_repo / "file2.txt").write_text("content2\n")
        auto_add_untracked_files()

        # Both should be in auto-added list
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert "file1.txt" in auto_added
        assert "file2.txt" in auto_added

    def test_auto_add_with_spaces_in_filename(self, temp_git_repo):
        """Test auto-adding files with spaces in the name."""
        ensure_state_directory_exists()

        # Create file with spaces
        file_with_spaces = temp_git_repo / "my file.txt"
        file_with_spaces.write_text("content\n")

        auto_add_untracked_files()

        # Check it was auto-added
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert "my file.txt" in auto_added

    def test_auto_add_skips_untracked_embedded_git_repository(self, temp_git_repo):
        """Test that untracked embedded repositories are not auto-added."""
        ensure_state_directory_exists()

        embedded_repo = temp_git_repo / "embedded"
        embedded_repo.mkdir()
        subprocess.run(["git", "init"], check=True, cwd=embedded_repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=embedded_repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=embedded_repo, capture_output=True)
        (embedded_repo / "file.txt").write_text("content\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=embedded_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=embedded_repo, capture_output=True)

        auto_add_untracked_files()

        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert "embedded" not in auto_added
        assert "embedded/" not in auto_added

        result = subprocess.run(
            ["git", "ls-files", "--stage", "--", "embedded"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == ""
