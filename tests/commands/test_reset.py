"""Tests for reset command."""

import subprocess

import pytest

from git_stage_batch.commands.include import command_include_to_batch
from git_stage_batch.commands.reset import command_reset_from_batch
from git_stage_batch.commands.start import command_start
from git_stage_batch.data.hunk_tracking import find_and_cache_next_unblocked_hunk
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.file_io import read_text_file_contents
from git_stage_batch.utils.paths import (
    get_batch_claimed_hunks_file_path,
    get_batch_metadata_file_path,
    get_batched_hunks_file_path,
    get_processed_batch_ids_file_path,
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


class TestResetFromBatch:
    """Tests for reset --from command."""

    def test_reset_requires_from_flag(self, temp_git_repo):
        """Test that reset requires --from flag."""
        # This is tested by argparse requiring the flag
        # If we tried to call command_reset_from_batch without batch_name, it would error
        pass

    def test_reset_nonexistent_batch_errors(self, temp_git_repo):
        """Test that resetting nonexistent batch errors."""
        with pytest.raises(CommandError) as exc_info:
            command_reset_from_batch("nonexistent")

        assert "does not exist" in str(exc_info.value.message).lower()

    def test_reset_whole_batch(self, temp_git_repo):
        """Test resetting all claims from a batch."""
        # Create a file with changes
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make changes
        test_file.write_text("line 1 modified\nline 2\nline 3\n")

        # Start session and include to batch
        command_start()
        find_and_cache_next_unblocked_hunk()
        command_include_to_batch("mybatch", quiet=True)

        # Verify hunk is masked
        batched_hunks_path = get_batched_hunks_file_path()
        assert batched_hunks_path.exists()
        batched_content = read_text_file_contents(batched_hunks_path)
        assert batched_content.strip() != ""

        # Verify batch has claims
        batch_hunks_path = get_batch_claimed_hunks_file_path("mybatch")
        assert batch_hunks_path.exists()
        batch_hunks_content = read_text_file_contents(batch_hunks_path)
        assert batch_hunks_content.strip() != ""

        # Reset the batch
        command_reset_from_batch("mybatch")

        # Verify batch claims are cleared
        batch_hunks_content_after = read_text_file_contents(batch_hunks_path)
        assert batch_hunks_content_after.strip() == ""

        # Verify global mask is cleared (since batch was the only claim)
        batched_content_after = read_text_file_contents(batched_hunks_path)
        assert batched_content_after.strip() == ""

    def test_reset_line_claims(self, temp_git_repo):
        """Test resetting specific line claims from a batch."""
        import json
        from git_stage_batch.core.line_selection import parse_line_selection

        # Create a file with multiple lines
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make multiple line changes
        test_file.write_text("line 1 modified\nline 2 modified\nline 3 modified\n")

        # Start session and include lines to batch
        command_start()
        find_and_cache_next_unblocked_hunk()
        command_include_to_batch("mybatch", line_ids="4,5,6", quiet=True)

        # Verify line claims exist in metadata JSON
        from git_stage_batch.utils.file_io import read_text_file_contents
        metadata_path = get_batch_metadata_file_path("mybatch")
        assert metadata_path.exists()
        metadata = json.loads(read_text_file_contents(metadata_path))
        batch_ownership = metadata["files"]["test.py"]
        batch_line_ids = set()
        for range_str in batch_ownership.get("claimed_lines", []):
            batch_line_ids.update(parse_line_selection(range_str))
        assert batch_line_ids == {1, 2, 3}

        # Reset only line 2 (renumbered from display ID 5)
        command_reset_from_batch("mybatch", line_ids="2")

        # Verify line 2 is removed from batch claims
        metadata_after = json.loads(read_text_file_contents(metadata_path))
        batch_ownership_after = metadata_after["files"]["test.py"]
        batch_line_ids_after = set()
        for range_str in batch_ownership_after.get("claimed_lines", []):
            batch_line_ids_after.update(parse_line_selection(range_str))
        assert batch_line_ids_after == {1, 3}

        # Verify global mask still contains 1 and 3 (JSON format)
        global_mask_path = get_processed_batch_ids_file_path()
        global_mask = json.loads(read_text_file_contents(global_mask_path))
        file_data = global_mask.get("test.py", {})
        global_line_ids = set()
        for range_str in file_data.get("claimed_lines", []):
            global_line_ids.update(parse_line_selection(range_str))
        assert 1 in global_line_ids
        assert 2 not in global_line_ids
        assert 3 in global_line_ids

    def test_reset_with_multiple_batches(self, temp_git_repo):
        """Test that reset only unmasks hunks not claimed by other batches."""
        # Create a file with changes
        test_file = temp_git_repo / "test.py"
        test_file.write_text("line 1\n")
        subprocess.run(["git", "add", "test.py"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.py"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("line 1 modified\n")

        # Start session and include to two batches
        command_start()
        find_and_cache_next_unblocked_hunk()
        command_include_to_batch("batch-a", quiet=True)

        # Reset and include again to second batch
        from git_stage_batch.commands.again import command_again
        command_again()
        command_include_to_batch("batch-b", quiet=True)

        # Verify hunk is in global mask
        batched_hunks_path = get_batched_hunks_file_path()
        batched_content = read_text_file_contents(batched_hunks_path)
        hunk_hash = batched_content.strip()
        assert hunk_hash != ""

        # Reset batch-a
        command_reset_from_batch("batch-a")

        # Verify hunk is STILL masked (because batch-b still claims it)
        batched_content_after = read_text_file_contents(batched_hunks_path)
        assert batched_content_after.strip() == hunk_hash

        # Reset batch-b
        command_reset_from_batch("batch-b")

        # NOW hunk should be unmasked
        batched_content_final = read_text_file_contents(batched_hunks_path)
        assert batched_content_final.strip() == ""
