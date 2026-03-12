"""Tests for discard-file command."""

import subprocess

import pytest

from git_stage_batch.commands import command_abort, command_discard_file, command_start
from git_stage_batch.state import ensure_state_directory_exists


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


class TestCommandDiscardFile:
    """Tests for discard-file command."""

    def test_discard_file_removes_file_from_working_tree(self, temp_git_repo, capsys):
        """Test that discard-file removes the entire file from working tree."""
        # Create and commit a file
        test_file = temp_git_repo / "unwanted.txt"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "unwanted.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify the file
        test_file.write_text("line 1 modified\nline 2 modified\nline 3 modified\n")

        command_discard_file()

        # File should be completely removed from working tree
        assert not test_file.exists()

        # File should be staged for deletion
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-status"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "D\tunwanted.txt" in result.stdout

        captured = capsys.readouterr()
        assert "File discarded: unwanted.txt" in captured.out

    def test_discard_file_with_multiple_hunks(self, temp_git_repo, capsys):
        """Test that discard-file removes file even with multiple hunks."""
        # Create and commit a file with content that will create multiple hunks
        test_file = temp_git_repo / "multi.txt"
        test_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5\nline 6\nline 7\n")
        subprocess.run(["git", "add", "multi.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add multi"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify multiple parts to create multiple hunks
        test_file.write_text("line 1 modified\nline 2\nline 3\nline 4 modified\nline 5\nline 6\nline 7 modified\n")

        command_discard_file()

        # File should be completely removed
        assert not test_file.exists()

        # Verify it's staged for deletion
        result = subprocess.run(
            ["git", "status", "--short"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "D  multi.txt" in result.stdout

        captured = capsys.readouterr()
        assert "File discarded: multi.txt" in captured.out

    def test_discard_file_only_affects_current_file(self, temp_git_repo, capsys):
        """Test that discard-file only removes the current file, not others."""
        # Create and commit two files
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify both files
        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        command_discard_file()

        # Only the first file should be removed
        assert not file1.exists()
        assert file2.exists()

        # Verify file2 still has its changes
        assert file2.read_text() == "modified 2\n"

        # Verify file1 is staged for deletion
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-status"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "D\tfile1.txt" in result.stdout

        captured = capsys.readouterr()
        assert "File discarded: file1.txt" in captured.out

    def test_discard_file_no_changes(self, temp_git_repo, capsys):
        """Test discard-file when there are no changes."""
        command_discard_file()

        captured = capsys.readouterr()
        assert "No changes to discard" in captured.out

    def test_abort_restores_discarded_untracked_file(self, temp_git_repo):
        """Test that abort restores untracked files discarded with discard-file."""
        ensure_state_directory_exists()

        # Create an untracked file
        untracked_file = temp_git_repo / "untracked.txt"
        original_content = "untracked content\n"
        untracked_file.write_text(original_content)

        # Add the file with -N to make it visible to diff (simulating auto-add)
        subprocess.run(["git", "add", "-N", "untracked.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Start session (this initializes abort state)
        command_start()

        # Discard the file (should snapshot before deleting)
        command_discard_file()

        # File should be deleted
        assert not untracked_file.exists()

        # Abort should restore it
        command_abort()

        # File should be restored with original content
        assert untracked_file.exists()
        assert untracked_file.read_text() == original_content
