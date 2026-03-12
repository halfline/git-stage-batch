"""Tests for discard-file command."""

import subprocess

import pytest

from git_stage_batch.commands import command_abort, command_discard_file, command_start
from git_stage_batch.hashing import compute_stable_hunk_hash
from git_stage_batch.parser import parse_unified_diff_into_single_hunk_patches
from git_stage_batch.state import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    read_text_file_contents,
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

    def test_discard_file_marks_all_hunks_as_processed(self, temp_git_repo):
        """Test that discard-file marks all hunks as processed even after git rm stages deletion."""
        ensure_state_directory_exists()

        # Create and commit a file with content that will create multiple hunks
        # Need enough spacing (>6 lines) between changes to create separate hunks with U3
        test_file = temp_git_repo / "multi.txt"
        lines = [f"line {i}\n" for i in range(1, 21)]
        test_file.write_text("".join(lines))
        subprocess.run(["git", "add", "multi.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add multi"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify lines far apart to create multiple hunks (change lines 1, 10, and 19)
        lines[0] = "line 1 modified\n"
        lines[9] = "line 10 modified\n"
        lines[18] = "line 19 modified\n"
        test_file.write_text("".join(lines))

        # Get all hunks from the file before discarding
        result = subprocess.run(
            ["git", "diff", "-U3", "--no-color"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        patches = parse_unified_diff_into_single_hunk_patches(result.stdout)
        expected_hashes = {compute_stable_hunk_hash(patch.to_patch_text()) for patch in patches}

        # Verify we have multiple hunks
        assert len(expected_hashes) >= 2, "Test requires at least 2 hunks"

        # Discard the file
        command_discard_file()

        # File should be removed and staged for deletion
        assert not test_file.exists()
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-status"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "D\tmulti.txt" in result.stdout

        # All hunks should be marked as processed in blocklist
        blocklist_path = get_block_list_file_path()
        assert blocklist_path.exists()
        blocklist_text = read_text_file_contents(blocklist_path)
        blocked_hashes = set(blocklist_text.splitlines())

        # Every hunk hash from the file should be in the blocklist
        assert expected_hashes.issubset(blocked_hashes), \
            f"Expected all {len(expected_hashes)} hunks to be blocked, but only {len(expected_hashes & blocked_hashes)} were"
