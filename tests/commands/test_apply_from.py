"""Tests for apply from batch command."""

from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.include import command_include_to_batch
from git_stage_batch.batch.ownership import BatchOwnership
from git_stage_batch.batch.storage import add_file_to_batch
from git_stage_batch.batch.ownership import BatchOwnership, DeletionClaim

import subprocess

import pytest

from git_stage_batch.batch import create_batch
from git_stage_batch.commands.apply_from import command_apply_from_batch
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.paths import ensure_state_directory_exists


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

    # Initialize session for batch operations
    ensure_state_directory_exists()
    initialize_abort_state()

    return repo


class TestCommandApplyFromBatch:
    """Tests for apply from batch command."""

    def test_apply_from_batch_modifies_working_tree(self, temp_git_repo):
        """Test applying changes from a batch to working tree."""

        # Commit a file with multiple lines
        (temp_git_repo / "file.txt").write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add a new line and save to batch
        (temp_git_repo / "file.txt").write_text("line 1\nline 2\nnew line\nline 3\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        # Reset file to original
        (temp_git_repo / "file.txt").write_text("line 1\nline 2\nline 3\n")

        command_apply_from_batch("test-batch")

        # File should have the new line added
        content = (temp_git_repo / "file.txt").read_text()
        assert "new line" in content
        # Original lines should still be present
        assert "line 1" in content
        assert "line 2" in content
        assert "line 3" in content

    def test_apply_from_batch_does_not_stage(self, temp_git_repo):
        """Test that apply does not stage changes to index."""

        # Commit a file with multiple lines
        (temp_git_repo / "file.txt").write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add a new line and save to batch
        (temp_git_repo / "file.txt").write_text("line 1\nline 2\nnew line\nline 3\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        # Reset file to original
        (temp_git_repo / "file.txt").write_text("line 1\nline 2\nline 3\n")

        command_apply_from_batch("test-batch")

        # Index should be clean (no staged changes)
        result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert result.stdout == ""

    def test_apply_from_empty_batch_fails(self, temp_git_repo):
        """Test applying from an empty batch fails."""
        create_batch("empty-batch")
        # Empty batch (only contains baseline from HEAD) has no diff

        with pytest.raises(CommandError):
            command_apply_from_batch("empty-batch")

    def test_apply_from_nonexistent_batch_fails(self, temp_git_repo):
        """Test applying from nonexistent batch fails."""
        with pytest.raises(CommandError):
            command_apply_from_batch("nonexistent")

    def test_apply_from_batch_outside_repo_fails(self, tmp_path, monkeypatch):
        """Test applying from batch outside repo fails."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        with pytest.raises(CommandError):
            command_apply_from_batch("test-batch")

    def test_apply_line_level_requires_single_file_context(self, temp_git_repo):
        """Test that line-level apply with multiple files errors out."""

        # Create two files and commit them
        (temp_git_repo / "file1.txt").write_text("line 1\nline 2\n")
        (temp_git_repo / "file2.txt").write_text("line A\nline B\n")
        subprocess.run(["git", "add", "."], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create batch with both files
        create_batch("multi-file-batch")

        # Modify both files and add to batch
        (temp_git_repo / "file1.txt").write_text("line 1\nnew line\nline 2\n")
        (temp_git_repo / "file2.txt").write_text("line A\nnew line\nline B\n")
        subprocess.run(["git", "add", "."], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Modified state"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add both files to batch manually
        ownership1 = BatchOwnership(claimed_lines=["2"], deletions=[])
        ownership2 = BatchOwnership(claimed_lines=["2"], deletions=[])
        add_file_to_batch("multi-file-batch", "file1.txt", ownership1, "100644")
        add_file_to_batch("multi-file-batch", "file2.txt", ownership2, "100644")

        # Reset to old state
        subprocess.run(["git", "reset", "--hard", "HEAD~1"], check=True, cwd=temp_git_repo, capture_output=True)

        # Try line-level apply without file context - should fail
        with pytest.raises(CommandError, match="Line-level apply.*requires single-file context"):
            command_apply_from_batch("multi-file-batch", line_ids="1")

    def test_apply_line_level_with_file_context_succeeds(self, temp_git_repo):
        """Test that line-level apply with --file context succeeds."""

        # Create two files
        (temp_git_repo / "file1.txt").write_text("line 1\nline 2\n")
        (temp_git_repo / "file2.txt").write_text("line A\nline B\n")
        subprocess.run(["git", "add", "."], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create batch
        create_batch("multi-file-batch")

        # Modify both files
        (temp_git_repo / "file1.txt").write_text("line 1\nnew line\nline 2\n")
        (temp_git_repo / "file2.txt").write_text("line A\nnew line\nline B\n")
        subprocess.run(["git", "add", "."], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Modified"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add both files to batch - claiming the "new line" (line 2 in batch source)
        ownership1 = BatchOwnership(claimed_lines=["2"], deletions=[])
        ownership2 = BatchOwnership(claimed_lines=["2"], deletions=[])
        add_file_to_batch("multi-file-batch", "file1.txt", ownership1, "100644")
        add_file_to_batch("multi-file-batch", "file2.txt", ownership2, "100644")

        # Reset to old state
        subprocess.run(["git", "reset", "--hard", "HEAD~1"], check=True, cwd=temp_git_repo, capture_output=True)

        # Apply only file1 with line filtering (line 1 is the claimed line "new line")
        command_apply_from_batch("multi-file-batch", line_ids="1", file="file1.txt")

        # file1 should have new line
        content1 = (temp_git_repo / "file1.txt").read_text()
        assert "new line" in content1

        # file2 should remain unchanged.
        content2 = (temp_git_repo / "file2.txt").read_text()
        assert "new line" not in content2

    def test_apply_replacement_unit_includes_deletion(self, temp_git_repo):
        """Test that applying a replacement unit includes both claimed line and deletion."""

        # Commit initial file
        (temp_git_repo / "file.txt").write_text("old line\nline 2\nline 3\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create a batch with replacement-style ownership
        # (claimed line 1 + deletion of "old line")
        create_batch("replacement-batch")

        # Manually construct replacement ownership:
        # - Claim line 1 (which now contains "new line")
        # - Delete "old line\n" anchored at None (start of file)
        # First, modify file to have new content
        (temp_git_repo / "file.txt").write_text("new line\nline 2\nline 3\n")

        # Save current state as batch source
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "New state for batch"], check=True, cwd=temp_git_repo, capture_output=True)

        ownership = BatchOwnership(
            claimed_lines=["1"],
            deletions=[DeletionClaim(anchor_line=None, content_lines=[b"old line\n"])]
        )
        add_file_to_batch("replacement-batch", "file.txt", ownership, "100644")

        # Reset working tree to old state
        subprocess.run(["git", "reset", "--hard", "HEAD~1"], check=True, cwd=temp_git_repo, capture_output=True)

        # Verify we're back at old state
        content_before = (temp_git_repo / "file.txt").read_text()
        assert content_before == "old line\nline 2\nline 3\n"

        # Apply the replacement unit by selecting display line 1 (deletion) and line 2 (claimed line)
        # This should apply both the deletion constraint and the claimed line
        command_apply_from_batch("replacement-batch", line_ids="1-2", file="file.txt")

        # Should have the new line after the deletion was applied.
        content_after = (temp_git_repo / "file.txt").read_text()
        assert "new line" in content_after
        assert "old line" not in content_after
        assert "line 2" in content_after
        assert "line 3" in content_after

    def test_apply_partial_atomic_unit_fails(self, temp_git_repo):
        """Test that partial selection of atomic unit fails with clear error."""

        # Commit initial file
        (temp_git_repo / "file.txt").write_text("old line\nline 2\nline 3\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create batch with replacement unit
        create_batch("atomic-batch")
        (temp_git_repo / "file.txt").write_text("new line\nline 2\nline 3\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "New state"], check=True, cwd=temp_git_repo, capture_output=True)

        ownership = BatchOwnership(
            claimed_lines=["1"],
            deletions=[DeletionClaim(anchor_line=None, content_lines=[b"old line\n"])]
        )
        add_file_to_batch("atomic-batch", "file.txt", ownership, "100644")

        # Reset to old state
        subprocess.run(["git", "reset", "--hard", "HEAD~1"], check=True, cwd=temp_git_repo, capture_output=True)

        # Try to apply only the deletion (line 1) without the claimed line (line 2)
        # This should fail because it's a partial selection of a replacement
        with pytest.raises(CommandError, match="must be selected together"):
            command_apply_from_batch("atomic-batch", line_ids="1", file="file.txt")
