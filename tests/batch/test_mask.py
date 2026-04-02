"""Tests for batch mask operations."""

import subprocess

import pytest

from git_stage_batch.batch.mask import recompute_global_batch_mask
from git_stage_batch.batch.operations import create_batch
from git_stage_batch.core.line_selection import read_line_ids_file, write_line_ids_file
from git_stage_batch.utils.file_io import read_text_file_contents, write_text_file_contents
from git_stage_batch.utils.paths import (
    get_batch_directory_path,
    get_state_directory_path,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)

    # Create initial commit
    (tmp_path / "README").write_text("initial\n")
    subprocess.run(["git", "add", "README"], check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

    return tmp_path


class TestRecomputeGlobalBatchMask:
    """Tests for recompute_global_batch_mask function."""

    def test_empty_batches_creates_empty_masks(self, temp_git_repo):
        """Test that recompute creates empty mask files when no batches exist."""
        recompute_global_batch_mask()

        # Global mask files should exist but be empty
        state_dir = get_state_directory_path()
        batched_hunks_path = state_dir / "batched-hunks"
        assert batched_hunks_path.exists()
        content = read_text_file_contents(batched_hunks_path)
        assert content == ""

        processed_batch_ids_path = state_dir / "processed.batch"
        assert processed_batch_ids_path.exists()
        line_ids = read_line_ids_file(processed_batch_ids_path)
        assert line_ids == []

    def test_single_batch_with_claimed_hunks(self, temp_git_repo):
        """Test that single batch's claimed hunks appear in global mask."""
        create_batch("batch1", "Test batch")

        # Add claimed hunks to batch
        batch_dir = get_batch_directory_path("batch1")
        claimed_hunks_path = batch_dir / "claimed_hunks"
        write_text_file_contents(claimed_hunks_path, "hash1\nhash2\nhash3\n")

        recompute_global_batch_mask()

        # Global mask should contain all hashes
        state_dir = get_state_directory_path()
        batched_hunks_path = state_dir / "batched-hunks"
        content = read_text_file_contents(batched_hunks_path)
        hashes = content.strip().split("\n") if content.strip() else []
        assert sorted(hashes) == ["hash1", "hash2", "hash3"]

    def test_single_batch_with_claimed_line_ids(self, temp_git_repo):
        """Test that single batch's claimed line IDs appear in global mask."""
        create_batch("batch1", "Test batch")

        # Add claimed line IDs to batch
        batch_dir = get_batch_directory_path("batch1")
        claimed_line_ids_path = batch_dir / "claimed_line_ids"
        write_line_ids_file(claimed_line_ids_path, {1, 3, 5})

        recompute_global_batch_mask()

        # Global mask should contain all line IDs
        state_dir = get_state_directory_path()
        processed_batch_ids_path = state_dir / "processed.batch"
        line_ids = read_line_ids_file(processed_batch_ids_path)
        assert set(line_ids) == {1, 3, 5}

    def test_multiple_batches_with_overlapping_hunks(self, temp_git_repo):
        """Test that overlapping hunk claims from multiple batches are unioned."""
        create_batch("batch1", "First batch")
        create_batch("batch2", "Second batch")

        # Batch1 claims hash1, hash2
        batch_dir1 = get_batch_directory_path("batch1")
        claimed_hunks_path1 = batch_dir1 / "claimed_hunks"
        write_text_file_contents(claimed_hunks_path1, "hash1\nhash2\n")

        # Batch2 claims hash2, hash3 (hash2 overlaps)
        batch_dir2 = get_batch_directory_path("batch2")
        claimed_hunks_path2 = batch_dir2 / "claimed_hunks"
        write_text_file_contents(claimed_hunks_path2, "hash2\nhash3\n")

        recompute_global_batch_mask()

        # Global mask should be union: hash1, hash2, hash3
        state_dir = get_state_directory_path()
        batched_hunks_path = state_dir / "batched-hunks"
        content = read_text_file_contents(batched_hunks_path)
        hashes = content.strip().split("\n") if content.strip() else []
        assert sorted(hashes) == ["hash1", "hash2", "hash3"]

    def test_multiple_batches_with_overlapping_line_ids(self, temp_git_repo):
        """Test that overlapping line ID claims from multiple batches are unioned."""
        create_batch("batch1", "First batch")
        create_batch("batch2", "Second batch")

        # Batch1 claims IDs 1, 2, 3
        batch_dir1 = get_batch_directory_path("batch1")
        claimed_line_ids_path1 = batch_dir1 / "claimed_line_ids"
        write_line_ids_file(claimed_line_ids_path1, {1, 2, 3})

        # Batch2 claims IDs 3, 4, 5 (3 overlaps)
        batch_dir2 = get_batch_directory_path("batch2")
        claimed_line_ids_path2 = batch_dir2 / "claimed_line_ids"
        write_line_ids_file(claimed_line_ids_path2, {3, 4, 5})

        recompute_global_batch_mask()

        # Global mask should be union: 1, 2, 3, 4, 5
        state_dir = get_state_directory_path()
        processed_batch_ids_path = state_dir / "processed.batch"
        line_ids = read_line_ids_file(processed_batch_ids_path)
        assert set(line_ids) == {1, 2, 3, 4, 5}

    def test_multiple_batches_with_disjoint_claims(self, temp_git_repo):
        """Test that disjoint claims from multiple batches are unioned."""
        create_batch("batch1", "First batch")
        create_batch("batch2", "Second batch")

        # Batch1 claims hash1
        batch_dir1 = get_batch_directory_path("batch1")
        claimed_hunks_path1 = batch_dir1 / "claimed_hunks"
        write_text_file_contents(claimed_hunks_path1, "hash1\n")

        # Batch2 claims hash2
        batch_dir2 = get_batch_directory_path("batch2")
        claimed_hunks_path2 = batch_dir2 / "claimed_hunks"
        write_text_file_contents(claimed_hunks_path2, "hash2\n")

        # Batch1 claims line IDs 1, 2
        claimed_line_ids_path1 = batch_dir1 / "claimed_line_ids"
        write_line_ids_file(claimed_line_ids_path1, {1, 2})

        # Batch2 claims line IDs 3, 4
        claimed_line_ids_path2 = batch_dir2 / "claimed_line_ids"
        write_line_ids_file(claimed_line_ids_path2, {3, 4})

        recompute_global_batch_mask()

        # Global hunks mask should contain both hashes
        state_dir = get_state_directory_path()
        batched_hunks_path = state_dir / "batched-hunks"
        content = read_text_file_contents(batched_hunks_path)
        hashes = content.strip().split("\n") if content.strip() else []
        assert sorted(hashes) == ["hash1", "hash2"]

        # Global line IDs mask should contain all IDs
        processed_batch_ids_path = state_dir / "processed.batch"
        line_ids = read_line_ids_file(processed_batch_ids_path)
        assert set(line_ids) == {1, 2, 3, 4}

    def test_batch_with_both_hunks_and_line_ids(self, temp_git_repo):
        """Test that batch with both hunks and line IDs is handled correctly."""
        create_batch("batch1", "Test batch")

        # Add both claimed hunks and line IDs
        batch_dir = get_batch_directory_path("batch1")
        claimed_hunks_path = batch_dir / "claimed_hunks"
        write_text_file_contents(claimed_hunks_path, "hash1\nhash2\n")

        claimed_line_ids_path = batch_dir / "claimed_line_ids"
        write_line_ids_file(claimed_line_ids_path, {1, 2, 3})

        recompute_global_batch_mask()

        # Both global masks should be populated
        state_dir = get_state_directory_path()
        batched_hunks_path = state_dir / "batched-hunks"
        content = read_text_file_contents(batched_hunks_path)
        hashes = content.strip().split("\n") if content.strip() else []
        assert sorted(hashes) == ["hash1", "hash2"]

        processed_batch_ids_path = state_dir / "processed.batch"
        line_ids = read_line_ids_file(processed_batch_ids_path)
        assert set(line_ids) == {1, 2, 3}

    def test_batch_with_no_claims(self, temp_git_repo):
        """Test that batch with no claim files doesn't cause errors."""
        create_batch("batch1", "Test batch")
        # Don't create any claim files

        recompute_global_batch_mask()

        # Global masks should be empty
        state_dir = get_state_directory_path()
        batched_hunks_path = state_dir / "batched-hunks"
        content = read_text_file_contents(batched_hunks_path)
        assert content == ""

        processed_batch_ids_path = state_dir / "processed.batch"
        line_ids = read_line_ids_file(processed_batch_ids_path)
        assert line_ids == []

    def test_hashes_are_sorted_in_output(self, temp_git_repo):
        """Test that hunk hashes are sorted in the output file."""
        create_batch("batch1", "Test batch")

        # Add hashes in non-sorted order
        batch_dir = get_batch_directory_path("batch1")
        claimed_hunks_path = batch_dir / "claimed_hunks"
        write_text_file_contents(claimed_hunks_path, "hash3\nhash1\nhash2\n")

        recompute_global_batch_mask()

        # Hashes should be sorted
        state_dir = get_state_directory_path()
        batched_hunks_path = state_dir / "batched-hunks"
        content = read_text_file_contents(batched_hunks_path)
        hashes = content.strip().split("\n")
        assert hashes == ["hash1", "hash2", "hash3"]
