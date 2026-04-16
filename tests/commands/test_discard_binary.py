"""Tests for discarding binary files from batches."""

import shutil

import subprocess

import pytest

from git_stage_batch.batch.storage import add_binary_file_to_batch
from git_stage_batch.commands.discard_from import command_discard_from_batch
from git_stage_batch.commands.start import command_start
from git_stage_batch.batch.operations import create_batch
from git_stage_batch.core.models import BinaryFileChange
from git_stage_batch.exceptions import CommandError


@pytest.fixture
def binary_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for binary discard testing."""
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


class TestBinaryDiscard:
    """Tests for discarding binary files from batches."""

    def test_discard_added_binary_file(self, binary_repo):
        """Test discarding an added binary file restores absence (removes from working tree)."""
        # Create and commit baseline without binary file
        (binary_repo / "text.txt").write_text("baseline\n")
        subprocess.run(["git", "add", "text.txt"], check=True, cwd=binary_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Baseline"], check=True, cwd=binary_repo, capture_output=True)

        # Add new binary file
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

        # File exists in working tree
        assert binary_file.exists()

        # Discard batch (should remove file - it wasn't in baseline)
        command_discard_from_batch("test-batch")

        # Verify file removed
        assert not binary_file.exists()

    def test_discard_modified_binary_file(self, binary_repo):
        """Test discarding a modified binary file restores baseline content."""
        # Create and commit baseline binary file
        binary_file = binary_repo / "data.bin"
        baseline_content = b"\x00\x01\x02\x03"
        binary_file.write_bytes(baseline_content)
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

        # Working tree has modified content
        assert binary_file.read_bytes() == modified_content

        # Discard batch (should restore baseline)
        command_discard_from_batch("test-batch")

        # Verify file restored to baseline
        assert binary_file.exists()
        assert binary_file.read_bytes() == baseline_content

    def test_discard_deleted_binary_file(self, binary_repo):
        """Test discarding a deleted binary file restores it from baseline."""
        # Create and commit baseline binary file
        binary_file = binary_repo / "data.bin"
        baseline_content = b"\x00\x01\x02\x03"
        binary_file.write_bytes(baseline_content)
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

        # File doesn't exist in working tree
        assert not binary_file.exists()

        # Discard batch (should restore file from baseline)
        command_discard_from_batch("test-batch")

        # Verify file restored
        assert binary_file.exists()
        assert binary_file.read_bytes() == baseline_content

    def test_discard_binary_file_with_file_filter(self, binary_repo):
        """Test discarding specific binary file using --file filter."""
        # Create baseline
        subprocess.run(["git", "commit", "--allow-empty", "-m", "Baseline"], check=True, cwd=binary_repo, capture_output=True)

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

        # Both files exist
        assert file1.exists()
        assert file2.exists()

        # Discard only data1.bin
        command_discard_from_batch("test-batch", file="data1.bin")

        # Verify only data1.bin removed
        assert not file1.exists()
        assert file2.exists()

    def test_discard_binary_with_line_selection_fails(self, binary_repo):
        """Test that --lines flag fails for binary files with clear error."""
        # Create baseline and binary file
        subprocess.run(["git", "commit", "--allow-empty", "-m", "Baseline"], check=True, cwd=binary_repo, capture_output=True)
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

        # Try to discard with --lines (should fail)
        with pytest.raises(CommandError) as exc_info:
            command_discard_from_batch("test-batch", line_ids="1", file="data.bin")

        # Verify error message mentions binary files and atomicity
        error_msg = str(exc_info.value).lower()
        assert "binary" in error_msg
        assert ("complete units" in error_msg or "atomic" in error_msg)

    def test_discard_binary_creates_parent_directories(self, binary_repo):
        """Test that discarding binary file creates parent directories if needed."""
        # Create baseline with binary in subdirectory
        subdir = binary_repo / "subdir"
        subdir.mkdir()
        binary_file = subdir / "data.bin"
        baseline_content = b"\x00\x01\x02\x03"
        binary_file.write_bytes(baseline_content)
        subprocess.run(["git", "add", "subdir/data.bin"], check=True, cwd=binary_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add binary in subdir"], check=True, cwd=binary_repo, capture_output=True)

        # Modify and create batch
        modified_content = b"\xFF\xFE\xFD\xFC"
        binary_file.write_bytes(modified_content)

        command_start()
        create_batch("test-batch", "Nested binary")

        binary_change = BinaryFileChange(
            old_path="subdir/data.bin",
            new_path="subdir/data.bin",
            change_type="modified"
        )
        add_binary_file_to_batch("test-batch", binary_change)

        # Remove entire subdirectory
        shutil.rmtree(subdir)
        assert not subdir.exists()

        # Discard batch (should recreate directory structure)
        command_discard_from_batch("test-batch")

        # Verify directory and file restored
        assert subdir.exists()
        assert binary_file.exists()
        assert binary_file.read_bytes() == baseline_content

    def test_discard_already_at_baseline_is_noop(self, binary_repo):
        """Test discarding when file already at baseline state is a no-op."""
        # Create baseline binary file
        binary_file = binary_repo / "data.bin"
        baseline_content = b"\x00\x01\x02\x03"
        binary_file.write_bytes(baseline_content)
        subprocess.run(["git", "add", "data.bin"], check=True, cwd=binary_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add binary"], check=True, cwd=binary_repo, capture_output=True)

        # Modify and create batch
        modified_content = b"\xFF\xFE\xFD\xFC"
        binary_file.write_bytes(modified_content)

        command_start()
        create_batch("test-batch", "Binary mod")

        binary_change = BinaryFileChange(
            old_path="data.bin",
            new_path="data.bin",
            change_type="modified"
        )
        add_binary_file_to_batch("test-batch", binary_change)

        # Manually restore to baseline before discard
        binary_file.write_bytes(baseline_content)

        # Discard should be no-op
        command_discard_from_batch("test-batch")

        # File still at baseline
        assert binary_file.read_bytes() == baseline_content
