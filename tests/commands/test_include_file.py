"""Tests for include-file command."""

import subprocess

import pytest

from git_stage_batch.commands.include import command_include_file


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


class TestCommandIncludeFile:
    """Tests for include-file command."""

    def test_include_file_stages_all_hunks_from_file(self, temp_git_repo, capsys):
        """Test that include-file stages all hunks from the current file."""
        # Create and commit a file with multiple hunks
        test_file = temp_git_repo / "multi.txt"
        test_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")
        subprocess.run(["git", "add", "multi.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add multi"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify multiple parts to create multiple hunks
        test_file.write_text("line 1 modified\nline 2\nline 3\nline 4\nline 5 modified\n")

        command_include_file()

        # Check that all changes are staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "+line 1 modified" in result.stdout
        assert "+line 5 modified" in result.stdout

        # Verify command produced output (either summary or per-hunk messages)
        captured = capsys.readouterr()
        assert "staged" in captured.out.lower()
        assert "multi.txt" in captured.out

    def test_include_file_no_changes(self, temp_git_repo, capsys):
        """Test include-file when no changes exist."""
        command_include_file()

        captured = capsys.readouterr()
        assert "No changes to stage" in captured.out

    def test_include_file_only_current_file(self, temp_git_repo, capsys):
        """Test that include-file only stages hunks from current file, not others."""
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

        # Include-file should only stage file1
        command_include_file()
        capsys.readouterr()  # Clear output

        # Verify only file1 is staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "file1.txt" in result.stdout
        assert "file2.txt" not in result.stdout

        # file2 should still be in working tree
        result = subprocess.run(
            ["git", "diff"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "file2.txt" in result.stdout
