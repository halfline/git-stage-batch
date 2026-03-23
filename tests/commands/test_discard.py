"""Tests for discard command."""

import subprocess

import pytest

from git_stage_batch.commands.discard import command_discard, command_discard_line
from git_stage_batch.commands.include import command_include
from git_stage_batch.commands.start import command_start
from git_stage_batch.data.hunk_tracking import find_and_cache_next_unblocked_hunk
from git_stage_batch.exceptions import CommandError


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


class TestCommandDiscard:
    """Tests for discard command."""

    def test_discard_removes_hunk_from_working_tree(self, temp_git_repo, capsys):
        """Test that discard removes a hunk from the working tree."""
        # Modify README
        readme = temp_git_repo / "README.md"
        original_content = readme.read_text()
        readme.write_text("# Test\nNew content\n")

        command_discard()

        # Changes should be removed from working tree
        assert readme.read_text() == original_content

        # Nothing should be staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == ""

        captured = capsys.readouterr()
        assert "Hunk discarded" in captured.out

    def test_discard_no_changes(self, temp_git_repo, capsys):
        """Test discard when no changes exist."""
        command_discard()

        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.out

    def test_discard_then_include_next(self, temp_git_repo, capsys):
        """Test discarding one hunk then including the next."""
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

        # Discard first hunk
        command_discard()
        captured = capsys.readouterr()
        assert "file1.txt" in captured.out

        # Verify file1 is restored
        assert file1.read_text() == "original 1\n"

        # Include second hunk
        command_include()
        captured = capsys.readouterr()
        assert "file2.txt" in captured.out

        # Verify only file2 is staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "file2.txt" in result.stdout
        assert "file1.txt" not in result.stdout

    def test_discard_all_hunks_processed(self, temp_git_repo, capsys):
        """Test discard when all hunks have been processed."""
        command_start()

        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        # Discard the only hunk
        command_discard()
        capsys.readouterr()  # Clear output

        # Try to discard again
        command_discard()
        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.out

    def test_discard_snapshots_untracked_file(self, temp_git_repo):
        """Test that discard snapshots content of untracked files."""
        from git_stage_batch.utils.paths import get_abort_snapshots_directory_path

        # Create an untracked file
        untracked_file = temp_git_repo / "untracked.txt"
        original_content = "untracked content\n"
        untracked_file.write_text(original_content)

        # Auto-add with -N to make it visible to diff
        subprocess.run(["git", "add", "-N", "untracked.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Start session (initializes abort state)
        command_start()

        # Discard the file (should create snapshot before discarding)
        command_discard()

        # Verify snapshot was created
        snapshot_dir = get_abort_snapshots_directory_path()
        snapshot_file = snapshot_dir / "untracked.txt"
        assert snapshot_file.exists()
        assert snapshot_file.read_text() == original_content


class TestCommandDiscardLine:
    """Tests for discard --line command."""

    def test_discard_line_requires_current_hunk(self, temp_git_repo):
        """Test that discard --line requires an active hunk."""
        with pytest.raises(CommandError):
            command_discard_line("1")

    def test_discard_line_removes_single_addition(self, temp_git_repo):
        """Test discarding a single added line."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew line\n")

        # Cache the hunk
        command_start()
        find_and_cache_next_unblocked_hunk(quiet=True)

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

        # Cache the hunk
        command_start()
        find_and_cache_next_unblocked_hunk(quiet=True)

        # Discard line 1 (the deletion)
        command_discard_line("1")

        # Check that the line was restored in working tree
        content = readme.read_text()
        assert content == "# Test\n"

    def test_discard_line_with_range(self, temp_git_repo):
        """Test discarding a range of lines."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        find_and_cache_next_unblocked_hunk(quiet=True)

        # Discard lines 1-2
        command_discard_line("1-2")

        # Check that lines 1-2 were removed but line 3 remains
        content = readme.read_text()
        assert content == "# Test\nLine 3\n"

    def test_discard_line_partial_selection(self, temp_git_repo):
        """Test discarding only some lines from a hunk."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        find_and_cache_next_unblocked_hunk(quiet=True)

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

        # Cache the hunk
        command_start()
        find_and_cache_next_unblocked_hunk(quiet=True)

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

        # Cache the hunk
        command_start()
        find_and_cache_next_unblocked_hunk(quiet=True)

        # Try to discard - should fail because file doesn't exist
        with pytest.raises(CommandError):
            command_discard_line("1")
