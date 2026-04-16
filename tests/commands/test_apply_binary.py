"""Tests for applying binary files from batches."""

import shutil

import subprocess

import pytest

from git_stage_batch.batch.storage import add_binary_file_to_batch
from git_stage_batch.commands.apply_from import command_apply_from_batch
from git_stage_batch.commands.start import command_start
from git_stage_batch.batch.operations import create_batch
from git_stage_batch.core.models import BinaryFileChange
from git_stage_batch.exceptions import CommandError


@pytest.fixture
def binary_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for binary apply testing."""
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


class TestBinaryApply:
    """Tests for applying binary files from batches."""

    def test_apply_added_binary_file(self, binary_repo):
        """Test applying an added binary file from batch to working tree."""
        # Create binary file
        binary_file = binary_repo / "data.bin"
        binary_content = b"\x00\x01\x02\x03\xFF\xFE"
        binary_file.write_bytes(binary_content)

        # Start session and create batch
        command_start()
        create_batch("test-batch", "Binary add")

        binary_change = BinaryFileChange(
            old_path="/dev/null",
            new_path="data.bin",
            change_type="added"
        )
        add_binary_file_to_batch("test-batch", binary_change)

        # Remove file from working tree
        binary_file.unlink()
        assert not binary_file.exists()

        # Apply batch
        command_apply_from_batch("test-batch")

        # Verify file restored with correct content
        assert binary_file.exists()
        assert binary_file.read_bytes() == binary_content

    def test_apply_modified_binary_file(self, binary_repo):
        """Test applying a modified binary file from batch."""
        # Create and commit initial binary file
        binary_file = binary_repo / "data.bin"
        original_content = b"\x00\x01\x02\x03"
        binary_file.write_bytes(original_content)
        subprocess.run(["git", "add", "data.bin"], check=True, cwd=binary_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add binary"], check=True, cwd=binary_repo, capture_output=True)

        # Modify binary file
        modified_content = b"\xFF\xFE\xFD\xFC"
        binary_file.write_bytes(modified_content)

        # Start session and create batch with modification
        command_start()
        create_batch("test-batch", "Binary mod")

        binary_change = BinaryFileChange(
            old_path="data.bin",
            new_path="data.bin",
            change_type="modified"
        )
        add_binary_file_to_batch("test-batch", binary_change)

        # Reset working tree to original
        binary_file.write_bytes(original_content)
        assert binary_file.read_bytes() == original_content

        # Apply batch
        command_apply_from_batch("test-batch")

        # Verify file has modified content
        assert binary_file.read_bytes() == modified_content

    def test_apply_deleted_binary_file(self, binary_repo):
        """Test applying a binary file deletion from batch."""
        # Create and commit binary file
        binary_file = binary_repo / "data.bin"
        binary_content = b"\x00\x01\x02\x03"
        binary_file.write_bytes(binary_content)
        subprocess.run(["git", "add", "data.bin"], check=True, cwd=binary_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add binary"], check=True, cwd=binary_repo, capture_output=True)

        # Delete binary file
        binary_file.unlink()

        # Start session and create batch with deletion
        command_start()
        create_batch("test-batch", "Binary del")

        binary_change = BinaryFileChange(
            old_path="data.bin",
            new_path="/dev/null",
            change_type="deleted"
        )
        add_binary_file_to_batch("test-batch", binary_change)

        # Restore file in working tree
        binary_file.write_bytes(binary_content)
        assert binary_file.exists()

        # Apply batch (should delete file)
        command_apply_from_batch("test-batch")

        # Verify file deleted
        assert not binary_file.exists()

    def test_apply_binary_file_with_file_filter(self, binary_repo):
        """Test applying specific binary file using --file filter."""
        # Create two binary files
        file1 = binary_repo / "data1.bin"
        file2 = binary_repo / "data2.bin"
        content1 = b"\x00\x01"
        content2 = b"\xFF\xFE"
        file1.write_bytes(content1)
        file2.write_bytes(content2)

        # Start session and create batch
        command_start()
        create_batch("test-batch", "Multiple binaries")

        change1 = BinaryFileChange(old_path="/dev/null", new_path="data1.bin", change_type="added")
        change2 = BinaryFileChange(old_path="/dev/null", new_path="data2.bin", change_type="added")
        add_binary_file_to_batch("test-batch", change1)
        add_binary_file_to_batch("test-batch", change2)

        # Remove both files
        file1.unlink()
        file2.unlink()

        # Apply only data1.bin
        command_apply_from_batch("test-batch", file="data1.bin")

        # Verify only data1.bin restored
        assert file1.exists()
        assert file1.read_bytes() == content1
        assert not file2.exists()

    def test_apply_binary_with_line_selection_fails(self, binary_repo):
        """Test that --lines flag fails for binary files with clear error."""
        # Create binary file
        binary_file = binary_repo / "data.bin"
        binary_file.write_bytes(b"\x00\x01\x02\x03")

        # Start session and create batch
        command_start()
        create_batch("test-batch", "Binary")

        binary_change = BinaryFileChange(
            old_path="/dev/null",
            new_path="data.bin",
            change_type="added"
        )
        add_binary_file_to_batch("test-batch", binary_change)

        # Try to apply with --lines (should fail)
        with pytest.raises(CommandError) as exc_info:
            command_apply_from_batch("test-batch", line_ids="1", file="data.bin")

        # Verify error message mentions binary files and atomicity
        error_msg = str(exc_info.value).lower()
        assert "binary" in error_msg
        assert ("complete units" in error_msg or "atomic" in error_msg)

    def test_apply_binary_creates_parent_directories(self, binary_repo):
        """Test that applying binary file creates parent directories if needed."""
        # Create binary file in subdirectory
        subdir = binary_repo / "subdir"
        subdir.mkdir()
        binary_file = subdir / "data.bin"
        binary_content = b"\x00\x01\x02\x03"
        binary_file.write_bytes(binary_content)

        # Start session and create batch
        command_start()
        create_batch("test-batch", "Nested binary")

        binary_change = BinaryFileChange(
            old_path="/dev/null",
            new_path="subdir/data.bin",
            change_type="added"
        )
        add_binary_file_to_batch("test-batch", binary_change)

        # Remove entire subdirectory
        shutil.rmtree(subdir)
        assert not subdir.exists()

        # Apply batch (should recreate directory structure)
        command_apply_from_batch("test-batch")

        # Verify directory and file created
        assert subdir.exists()
        assert binary_file.exists()
        assert binary_file.read_bytes() == binary_content

    def test_apply_deleted_binary_when_already_missing(self, binary_repo):
        """Test applying deletion when binary file already doesn't exist (no-op)."""
        # Create and commit binary file
        binary_file = binary_repo / "data.bin"
        binary_file.write_bytes(b"\x00\x01\x02\x03")
        subprocess.run(["git", "add", "data.bin"], check=True, cwd=binary_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add binary"], check=True, cwd=binary_repo, capture_output=True)

        # Delete and create batch
        binary_file.unlink()
        command_start()
        create_batch("test-batch", "Binary del")

        binary_change = BinaryFileChange(
            old_path="data.bin",
            new_path="/dev/null",
            change_type="deleted"
        )
        add_binary_file_to_batch("test-batch", binary_change)

        # File already doesn't exist - apply should be no-op
        assert not binary_file.exists()
        command_apply_from_batch("test-batch")
        assert not binary_file.exists()  # Still doesn't exist
