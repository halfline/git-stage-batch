"""Tests for binary file batch storage."""

from git_stage_batch.batch.operations import create_batch

import subprocess

import pytest

from git_stage_batch.batch.storage import add_binary_file_to_batch
from git_stage_batch.batch.query import get_batch_commit_sha, read_batch_metadata
from git_stage_batch.commands.start import command_start
from git_stage_batch.core.models import BinaryFileChange
from git_stage_batch.utils.git import run_git_command


@pytest.fixture
def binary_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for binary file testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    # Initialize repo
    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestBinaryFileStorage:
    """Tests for binary file batch storage correctness."""

    def test_added_binary_stored_in_batch_tree(self, binary_repo):
        """Test that added binary file is stored in batch commit tree."""

        # Create binary file
        binary_file = binary_repo / "data.bin"
        binary_content = b"\x00\x01\x02\x03\xFF\xFE"
        binary_file.write_bytes(binary_content)

        # Start session to create batch source
        command_start()

        # Create batch and add binary file
        create_batch("test-batch", "Test binary")
        binary_change = BinaryFileChange(
            old_path="/dev/null",
            new_path="data.bin",
            change_type="added"
        )
        add_binary_file_to_batch("test-batch", binary_change)

        # Verify metadata marks file as binary
        metadata = read_batch_metadata("test-batch")
        assert "data.bin" in metadata["files"]
        assert metadata["files"]["data.bin"]["file_type"] == "binary"
        assert metadata["files"]["data.bin"]["change_type"] == "added"

        # Verify file exists in batch commit tree
        batch_commit = get_batch_commit_sha("test-batch")
        assert batch_commit is not None

        result = run_git_command(
            ["show", f"{batch_commit}:data.bin"],
            check=False,
            text_output=False
        )
        assert result.returncode == 0
        assert result.stdout == binary_content

    def test_modified_binary_updated_in_batch_tree(self, binary_repo):
        """Test that modified binary file updates batch commit tree."""

        # Create and commit initial binary file
        binary_file = binary_repo / "data.bin"
        original_content = b"\x00\x01\x02\x03"
        binary_file.write_bytes(original_content)
        subprocess.run(["git", "add", "data.bin"], check=True, cwd=binary_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add binary"], check=True, cwd=binary_repo, capture_output=True)

        # Modify binary file
        modified_content = b"\xFF\xFE\xFD\xFC"
        binary_file.write_bytes(modified_content)

        # Start session
        command_start()

        # Create batch and add modified binary
        create_batch("test-batch", "Test binary mod")
        binary_change = BinaryFileChange(
            old_path="data.bin",
            new_path="data.bin",
            change_type="modified"
        )
        add_binary_file_to_batch("test-batch", binary_change)

        # Verify file in batch tree has modified content
        batch_commit = get_batch_commit_sha("test-batch")
        result = run_git_command(
            ["show", f"{batch_commit}:data.bin"],
            check=False,
            text_output=False
        )
        assert result.returncode == 0
        assert result.stdout == modified_content

    def test_deleted_binary_removed_from_batch_tree(self, binary_repo):
        """Test that deleted binary file is removed from batch commit tree."""

        # Create and commit binary file
        binary_file = binary_repo / "data.bin"
        binary_content = b"\x00\x01\x02\x03"
        binary_file.write_bytes(binary_content)
        subprocess.run(["git", "add", "data.bin"], check=True, cwd=binary_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add binary"], check=True, cwd=binary_repo, capture_output=True)

        # Delete binary file
        binary_file.unlink()

        # Start session
        command_start()

        # Create batch and add deletion
        create_batch("test-batch", "Test binary del")
        binary_change = BinaryFileChange(
            old_path="data.bin",
            new_path="/dev/null",
            change_type="deleted"
        )
        add_binary_file_to_batch("test-batch", binary_change)

        # Verify metadata marks file as deleted
        metadata = read_batch_metadata("test-batch")
        assert "data.bin" in metadata["files"]
        assert metadata["files"]["data.bin"]["file_type"] == "binary"
        assert metadata["files"]["data.bin"]["change_type"] == "deleted"

        # Verify file is absent from the batch commit tree.
        batch_commit = get_batch_commit_sha("test-batch")
        result = run_git_command(
            ["show", f"{batch_commit}:data.bin"],
            check=False,
            text_output=False
        )
        assert result.returncode != 0  # File should not exist

    def test_multiple_binary_files_in_batch(self, binary_repo):
        """Test that multiple binary files can coexist in batch."""

        # Create multiple binary files
        file1 = binary_repo / "data1.bin"
        file2 = binary_repo / "data2.bin"
        file1.write_bytes(b"\x00\x01")
        file2.write_bytes(b"\xFF\xFE")

        # Start session
        command_start()

        # Create batch and add both files
        create_batch("test-batch", "Multiple binaries")

        change1 = BinaryFileChange(old_path="/dev/null", new_path="data1.bin", change_type="added")
        change2 = BinaryFileChange(old_path="/dev/null", new_path="data2.bin", change_type="added")

        add_binary_file_to_batch("test-batch", change1)
        add_binary_file_to_batch("test-batch", change2)

        # Verify both files exist in batch tree
        batch_commit = get_batch_commit_sha("test-batch")

        result1 = run_git_command(["show", f"{batch_commit}:data1.bin"], check=False, text_output=False)
        result2 = run_git_command(["show", f"{batch_commit}:data2.bin"], check=False, text_output=False)

        assert result1.returncode == 0
        assert result2.returncode == 0
        assert result1.stdout == b"\x00\x01"
        assert result2.stdout == b"\xFF\xFE"

    def test_binary_file_mode_preserved(self, binary_repo):
        """Test that binary file mode is correctly stored."""

        # Create executable binary file
        binary_file = binary_repo / "script.bin"
        binary_file.write_bytes(b"#!/bin/sh\necho test")
        binary_file.chmod(0o755)

        # Start session
        command_start()

        # Create batch and add executable binary
        create_batch("test-batch", "Executable binary")
        binary_change = BinaryFileChange(
            old_path="/dev/null",
            new_path="script.bin",
            change_type="added"
        )
        add_binary_file_to_batch("test-batch", binary_change, file_mode="100755")

        # Verify mode stored in metadata
        metadata = read_batch_metadata("test-batch")
        assert metadata["files"]["script.bin"]["mode"] == "100755"

        # Verify file in batch tree has correct mode
        batch_commit = get_batch_commit_sha("test-batch")
        result = run_git_command(
            ["ls-tree", batch_commit, "script.bin"],
            check=True
        )
        assert "100755" in result.stdout
