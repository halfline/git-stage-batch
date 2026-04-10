"""Test case to reproduce discard --file --to not removing file from working tree."""

import subprocess

import pytest

from git_stage_batch.commands.discard import command_discard_to_batch
from git_stage_batch.commands.start import command_start
from git_stage_batch.batch.operations import create_batch
from git_stage_batch.utils.git import get_git_repository_root_path


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, capture_output=True)

    # Create initial commit
    (tmp_path / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

    return tmp_path


class TestDiscardFileToBatchRemovesFromWorkingTree:
    """Test that discard --file --to removes file from working tree."""

    def test_discard_file_to_batch_removes_file_from_working_tree(self, temp_git_repo):
        """Test that discard --file --to removes the file from working tree after saving to batch."""
        # Create and commit a file
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line1\nline2\nline3\n")
        subprocess.run(["git", "add", "test.py"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, capture_output=True)

        # Modify the file
        test_file.write_text("line1\nmodified\nline3\nnew line\n")

        # Start session
        command_start()

        # Create a batch
        create_batch("test-batch", "Test batch")

        # Discard file to batch
        command_discard_to_batch("test-batch", line_ids=None, file_only=True)

        # Check if file exists in working tree
        repo_root = get_git_repository_root_path()
        file_path = repo_root / "test.py"

        # File should exist (reverted to HEAD state after modifications saved to batch)
        assert file_path.exists(), (
            f"File {file_path} should exist in working tree after discard --file --to batch. "
            f"It should be reverted to its HEAD state."
        )

        # Verify file content matches HEAD (original state before modifications)
        assert file_path.read_text() == "line1\nline2\nline3\n", (
            f"File should be reverted to HEAD state, got: {file_path.read_text()}"
        )

        # Verify git status shows clean (no changes)
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True
        )
        # Should show clean - file reverted to HEAD
        assert status_result.stdout.strip() == "", (
            f"Expected clean status after reverting to HEAD, got: {status_result.stdout}"
        )

    def test_discard_file_to_batch_then_again_does_not_create_intent_to_add(self, temp_git_repo):
        """Test that running 'again' after discard --file --to doesn't re-add file as intent-to-add."""
        from git_stage_batch.commands.again import command_again

        # Create and commit a file
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line1\nline2\nline3\n")
        subprocess.run(["git", "add", "test.py"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, capture_output=True)

        # Modify the file
        test_file.write_text("line1\nmodified\nline3\nnew line\n")

        # Start session
        command_start()

        # Create a batch
        create_batch("test-batch", "Test batch")

        # Discard file to batch
        command_discard_to_batch("test-batch", line_ids=None, file_only=True)

        # File should exist (reverted to HEAD state)
        repo_root = get_git_repository_root_path()
        file_path = repo_root / "test.py"
        assert file_path.exists()
        assert file_path.read_text() == "line1\nline2\nline3\n"

        # Run again - should find no changes (file is at HEAD state)
        command_again()

        # Check git status - should be clean (no changes to stage)
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True
        )

        # Should show clean - file is at HEAD state, no changes to process
        assert status_result.stdout.strip() == "", (
            f"Expected clean status since file is at HEAD state, got: {status_result.stdout}"
        )

    def test_discard_new_file_to_batch_removes_file(self, temp_git_repo):
        """Test that discard --file --to removes new files (not in HEAD) from working tree."""
        # Create a new file (not committed)
        test_file = temp_git_repo / "newfile.py"
        test_file.write_text("new content\nmore lines\n")

        # Start session
        command_start()

        # Create a batch
        create_batch("test-batch", "Test batch")

        # Discard new file to batch
        command_discard_to_batch("test-batch", line_ids=None, file_only=True)

        # New file should be deleted from working tree
        repo_root = get_git_repository_root_path()
        file_path = repo_root / "newfile.py"
        assert not file_path.exists(), (
            f"New file {file_path} should be deleted after discard --file --to batch"
        )

        # Verify git status is clean
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True
        )
        assert status_result.stdout.strip() == "", (
            f"Expected clean status after new file deleted, got: {status_result.stdout}"
        )

    def test_discard_new_file_then_again_does_not_recreate(self, temp_git_repo):
        """Test that running 'again' after discarding new file doesn't recreate it."""
        from git_stage_batch.commands.again import command_again

        # Create a new file (not committed)
        test_file = temp_git_repo / "newfile.py"
        test_file.write_text("new content\nmore lines\n")

        # Start session
        command_start()

        # Create a batch
        create_batch("test-batch", "Test batch")

        # Discard new file to batch
        command_discard_to_batch("test-batch", line_ids=None, file_only=True)

        # New file should be deleted
        repo_root = get_git_repository_root_path()
        file_path = repo_root / "newfile.py"
        assert not file_path.exists()

        # Run again - should find no changes
        command_again()

        # File should still not exist
        assert not file_path.exists(), (
            f"New file should not reappear after 'again' command"
        )

        # Verify git status is clean
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True
        )
        assert status_result.stdout.strip() == "", (
            f"Expected clean status, file should not be recreated, got: {status_result.stdout}"
        )
