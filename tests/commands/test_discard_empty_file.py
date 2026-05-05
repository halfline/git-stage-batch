"""Test case for discarding empty new files to batch."""

import subprocess

import pytest

from git_stage_batch.commands.discard import command_discard_to_batch
from git_stage_batch.commands.start import command_start
from git_stage_batch.batch import read_file_from_batch
from git_stage_batch.batch.operations import create_batch
from git_stage_batch.batch.query import read_batch_metadata
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.utils.git import get_git_repository_root_path
from git_stage_batch.utils.paths import ensure_state_directory_exists


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


class TestDiscardEmptyFile:
    """Test that discard --file --to works with empty new files."""

    def test_discard_empty_file_to_batch(self, temp_git_repo):
        """Test that discard --file --to works with empty new files (like __init__.py)."""
        # Create an empty new file
        empty_file = temp_git_repo / "__init__.py"
        empty_file.write_text("")

        # Start session
        command_start()

        # Create a batch
        create_batch("test-batch", "Test batch for empty file")

        # Discarding an empty file to batch should not fail with "corrupt patch".
        command_discard_to_batch("test-batch", line_ids=None, file="")

        # Verify file was removed from working tree
        repo_root = get_git_repository_root_path()
        file_path = repo_root / "__init__.py"
        assert not file_path.exists(), "Empty file should be removed from working tree"

        # Verify git status doesn't show the file
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True
        )
        # File should not appear in git status at all (not as deleted, not as untracked)
        assert "__init__.py" not in status_result.stdout, (
            f"File should not appear in git status, got: {status_result.stdout}"
        )

    def test_discard_empty_file_with_intent_to_add(self, temp_git_repo):
        """Test discarding an empty file that was added with git add -N."""
        # Create an empty new file and add with intent-to-add
        empty_file = temp_git_repo / "empty.py"
        empty_file.write_text("")
        subprocess.run(["git", "add", "-N", "empty.py"], check=True, capture_output=True)

        # Start session
        command_start()

        # Create a batch
        create_batch("intent-batch", "Test batch for intent-to-add empty file")

        # Discard empty file to batch
        command_discard_to_batch("intent-batch", line_ids=None, file="")

        # Verify file was removed from working tree
        repo_root = get_git_repository_root_path()
        file_path = repo_root / "empty.py"
        assert not file_path.exists(), "Empty file should be removed from working tree"

        # Verify file is not in index
        ls_result = subprocess.run(
            ["git", "ls-files", "--stage", "empty.py"],
            capture_output=True,
            text=True,
            check=True
        )
        assert not ls_result.stdout.strip(), "File should not be in index"

    def test_discard_empty_deleted_file_to_batch(self, temp_git_repo):
        """Discarding an empty text deletion to batch should restore the file locally."""
        empty_file = temp_git_repo / "empty.txt"
        empty_file.write_bytes(b"")
        subprocess.run(["git", "add", "empty.txt"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add empty file"], check=True, capture_output=True)
        empty_file.unlink()

        ensure_state_directory_exists()
        initialize_abort_state()
        create_batch("delete-batch", "Test batch for empty deletion")

        command_discard_to_batch("delete-batch", line_ids=None, file="empty.txt", quiet=True)

        file_meta = read_batch_metadata("delete-batch")["files"]["empty.txt"]
        assert file_meta["change_type"] == "deleted"
        assert read_file_from_batch("delete-batch", "empty.txt") is None
        assert empty_file.exists()
        assert empty_file.read_bytes() == b""
