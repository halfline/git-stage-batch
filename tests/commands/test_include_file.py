"""Tests for include-file command."""

import subprocess

import pytest

from git_stage_batch.commands.include import command_include_file
from git_stage_batch.commands.start import command_start


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
        """Test that include-file stages all hunks from the selected file."""
        # Create and commit a file with multiple hunks
        test_file = temp_git_repo / "multi.txt"
        test_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")
        subprocess.run(["git", "add", "multi.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add multi"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify multiple parts to create multiple hunks
        test_file.write_text("line 1 modified\nline 2\nline 3\nline 4\nline 5 modified\n")

        command_start()
        command_include_file(file="")

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
        assert "staged" in captured.err.lower()
        assert "multi.txt" in captured.err

    def test_include_file_no_changes(self, temp_git_repo, capsys):
        """Test include-file when no more files remain."""
        # Create a change
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nModified\n")

        # Start session and include the file
        command_start()
        command_include_file(file="")
        capsys.readouterr()  # Clear output

        # Try to include file again - should show "No changes"
        command_include_file(file="")

        captured = capsys.readouterr()
        assert "No changes to stage" in captured.err

    def test_include_file_only_selected_file(self, temp_git_repo, capsys):
        """Test that include-file only stages hunks from selected file, not others."""
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

        # Start session
        command_start()

        # Include-file should only stage file1
        command_include_file(file="")
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

    def test_include_file_refreshes_untracked_path_after_external_unstage(
        self,
        temp_git_repo,
        capsys,
    ):
        """Test explicit include can recover a recorded path that became untracked again."""
        (temp_git_repo / "a.txt").write_text("a\n")
        (temp_git_repo / "b.txt").write_text("b\n")

        command_start()
        command_include_file(file="a.txt")
        command_include_file(file="b.txt")
        capsys.readouterr()

        subprocess.run(
            ["git", "restore", "--staged", "b.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add a"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        command_include_file(file="b.txt")

        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "b.txt"

        captured = capsys.readouterr()
        assert "No hunks staged from b.txt" not in captured.err
