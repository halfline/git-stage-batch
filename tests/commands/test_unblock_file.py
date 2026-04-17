"""Tests for unblock-file command."""

import subprocess

import pytest

from git_stage_batch.commands.block_file import command_block_file
from git_stage_batch.commands.unblock_file import command_unblock_file
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.file_io import append_file_path_to_file, read_file_paths_file
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


class TestCommandUnblockFile:
    """Tests for unblock-file command."""

    def test_unblock_file_requires_argument(self, temp_git_repo):
        """Test that unblock-file requires a file path argument."""
        with pytest.raises(CommandError):
            command_unblock_file("")

    def test_unblock_file_removes_from_gitignore(self, temp_git_repo, capsys):
        """Test that unblock-file removes file from .gitignore."""
        # Create and block a file
        (temp_git_repo / "temp.txt").write_text("content\n")
        command_block_file("temp.txt")

        # Verify it's in .gitignore
        gitignore = get_gitignore_path()
        assert "temp.txt\n" in gitignore.read_text()

        # Unblock it
        command_unblock_file("temp.txt")

        # Should be removed from .gitignore
        content = gitignore.read_text()
        assert "temp.txt" not in content

        captured = capsys.readouterr()
        assert "Unblocked file: temp.txt" in captured.err

    def test_unblock_file_removes_from_blocked_list(self, temp_git_repo):
        """Test that unblock-file removes from blocked list."""
        # Create and block a file
        (temp_git_repo / "temp.txt").write_text("content\n")
        command_block_file("temp.txt")

        # Verify it's in blocked list
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "temp.txt" in blocked

        # Unblock it
        command_unblock_file("temp.txt")

        # Should be removed from blocked list
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "temp.txt" not in blocked

    def test_unblock_file_makes_file_available_again(self, temp_git_repo):
        """Test that unblocked file is removed from both blocked list and .gitignore."""
        # Create and block a file
        (temp_git_repo / "temp.txt").write_text("content\n")
        command_block_file("temp.txt")

        # Verify it's blocked
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "temp.txt" in blocked
        assert "temp.txt" in get_gitignore_path().read_text()

        # Unblock it
        command_unblock_file("temp.txt")

        # Should be removed from blocked list and .gitignore
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "temp.txt" not in blocked
        assert "temp.txt" not in get_gitignore_path().read_text()

    def test_unblock_file_not_in_gitignore(self, temp_git_repo, capsys):
        """Test unblocking a file that's only in blocked list but not .gitignore."""
        # Add to blocked list without adding to .gitignore
        append_file_path_to_file(get_blocked_files_file_path(), "manual.txt")

        # Unblock it
        command_unblock_file("manual.txt")

        # Should be removed from blocked list
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "manual.txt" not in blocked

        # Should show appropriate message
        captured = capsys.readouterr()
        assert "Removed from blocked list: manual.txt (was not in .gitignore)" in captured.err

    def test_unblock_file_restores_intent_to_add_in_index(self, temp_git_repo):
        """Test that unblock-file re-adds the file to the index as intent-to-add during a session."""
        from git_stage_batch.commands.start import command_start

        # Create file and start a session (auto-adds with intent-to-add)
        (temp_git_repo / "temp.txt").write_text("content\n")
        command_start()

        # Block the file (removes from index during session)
        command_block_file("temp.txt")

        # Verify it's not in the index
        result = subprocess.run(["git", "ls-files", "--", "temp.txt"], capture_output=True, text=True, cwd=temp_git_repo)
        assert "temp.txt" not in result.stdout

        # Unblock it
        command_unblock_file("temp.txt")

        # Should be back in the index as intent-to-add
        result = subprocess.run(["git", "ls-files", "--", "temp.txt"], capture_output=True, text=True, cwd=temp_git_repo)
        assert "temp.txt" in result.stdout

    def test_unblock_file_preserves_staged_tracked_file_on_stop(self, temp_git_repo):
        """Test that unblock-file does not record tracked files as auto-added."""
        from git_stage_batch.commands.start import command_start
        from git_stage_batch.commands.stop import command_stop

        # Stage a tracked file before the session starts.
        (temp_git_repo / "README.md").write_text("# Test\n\nStaged\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)

        # Put the tracked file in blocked state so unblock-file can remove it.
        get_gitignore_path().write_text("README.md\n")
        append_file_path_to_file(get_blocked_files_file_path(), "README.md")

        command_start(quiet=True)
        command_unblock_file("README.md")
        command_stop()

        result = subprocess.run(["git", "status", "--porcelain", "--", "README.md"], capture_output=True, text=True, cwd=temp_git_repo)
        assert result.stdout.startswith("M  ")

    def test_unblock_file_shows_next_hunk_during_session(self, temp_git_repo, capsys):
        """Test that unblock-file shows the next hunk when a session is active."""
        from git_stage_batch.commands.start import command_start

        # Create two files: one to block/unblock, one to remain as next hunk
        (temp_git_repo / "toggled.txt").write_text("toggle me\n")
        (temp_git_repo / "other.txt").write_text("other content\n")

        # Start session so both files are visible
        command_start()
        capsys.readouterr()

        # Block toggled.txt (removes from session)
        command_block_file("toggled.txt")
        capsys.readouterr()

        # Unblock it (should show next hunk since session is active)
        command_unblock_file("toggled.txt")
        captured = capsys.readouterr()

        assert "Unblocked file: toggled.txt" in captured.err
        # Should show a hunk after unblocking
        assert "::" in captured.out

    def test_unblock_file_with_subdirectory(self, temp_git_repo):
        """Test unblocking a file in a subdirectory."""
        # Create subdirectory and file
        subdir = temp_git_repo / "src"
        subdir.mkdir()
        (subdir / "file.txt").write_text("content\n")

        # Block it
        command_block_file("src/file.txt")

        # Verify it's blocked
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "src/file.txt" in blocked

        # Unblock it
        command_unblock_file("src/file.txt")

        # Should be removed
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "src/file.txt" not in blocked
        assert "src/file.txt" not in get_gitignore_path().read_text()

    def test_unblock_file_resolves_absolute_path(self, temp_git_repo):
        """Test that unblock-file resolves absolute paths to repo-relative."""
        # Create and block a file
        (temp_git_repo / "file.txt").write_text("content\n")
        command_block_file("file.txt")

        # Unblock using absolute path
        abs_path = str(temp_git_repo / "file.txt")
        command_unblock_file(abs_path)

        # Should be removed (using relative path)
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "file.txt" not in blocked
