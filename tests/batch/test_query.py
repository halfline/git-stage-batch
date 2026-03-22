"""Tests for batch query operations."""

import json
import subprocess

import pytest

from git_stage_batch.batch.query import (
    get_batch_baseline_commit,
    get_batch_commit_sha,
    get_batch_tree_sha,
    list_batch_files,
    list_batch_names,
    read_batch_metadata,
)
from git_stage_batch.batch.operations import create_batch
from git_stage_batch.batch.storage import add_file_to_batch
from git_stage_batch.utils.paths import get_batch_metadata_file_path


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


def test_read_batch_metadata_existing(temp_git_repo):
    """Test reading metadata from existing batch."""
    create_batch("test-batch", "Test note")

    metadata = read_batch_metadata("test-batch")
    assert metadata["note"] == "Test note"
    assert "created_at" in metadata
    assert metadata["created_at"] != ""


def test_read_batch_metadata_nonexistent(temp_git_repo):
    """Test reading metadata from nonexistent batch returns empty."""
    metadata = read_batch_metadata("nonexistent")
    assert metadata["note"] == ""
    assert metadata["created_at"] == ""


def test_read_batch_metadata_corrupted_file(temp_git_repo):
    """Test reading corrupted metadata file returns empty."""
    create_batch("test-batch", "Original")

    # Corrupt the metadata file
    metadata_path = get_batch_metadata_file_path("test-batch")
    metadata_path.write_text("invalid json{")

    metadata = read_batch_metadata("test-batch")
    assert metadata["note"] == ""
    assert metadata["created_at"] == ""


def test_get_batch_commit_sha_existing(temp_git_repo):
    """Test getting commit SHA from existing batch."""
    create_batch("test-batch", "Test")

    sha = get_batch_commit_sha("test-batch")
    assert sha is not None
    assert len(sha) == 40  # Git SHA is 40 hex characters


def test_get_batch_commit_sha_nonexistent(temp_git_repo):
    """Test getting commit SHA from nonexistent batch returns None."""
    sha = get_batch_commit_sha("nonexistent")
    assert sha is None


def test_list_batch_names_empty(temp_git_repo):
    """Test listing batch names when none exist."""
    names = list_batch_names()
    assert names == []


def test_list_batch_names_single(temp_git_repo):
    """Test listing batch names with single batch."""
    create_batch("test-batch", "Test")

    names = list_batch_names()
    assert names == ["test-batch"]


def test_list_batch_names_multiple(temp_git_repo):
    """Test listing batch names with multiple batches."""
    create_batch("batch-a", "A")
    create_batch("batch-b", "B")
    create_batch("batch-c", "C")

    names = list_batch_names()
    assert names == ["batch-a", "batch-b", "batch-c"]  # Should be sorted


def test_list_batch_names_sorted(temp_git_repo):
    """Test that batch names are returned in sorted order."""
    create_batch("zebra", "Z")
    create_batch("apple", "A")
    create_batch("middle", "M")

    names = list_batch_names()
    assert names == ["apple", "middle", "zebra"]


def test_get_batch_tree_sha_existing(temp_git_repo):
    """Test getting tree SHA from existing batch."""
    create_batch("test-batch", "Test")

    tree_sha = get_batch_tree_sha("test-batch")
    assert tree_sha is not None
    assert len(tree_sha) == 40  # Git SHA is 40 hex characters


def test_get_batch_tree_sha_nonexistent(temp_git_repo):
    """Test getting tree SHA from nonexistent batch returns None."""
    tree_sha = get_batch_tree_sha("nonexistent")
    assert tree_sha is None


def test_list_batch_files_empty(temp_git_repo):
    """Test listing files in batch with no files."""
    create_batch("test-batch", "Test")

    files = list_batch_files("test-batch")
    assert files == []


def test_list_batch_files_with_files(temp_git_repo):
    """Test listing files in batch with files."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file1.txt", "content1")
    add_file_to_batch("test-batch", "file2.txt", "content2")

    files = list_batch_files("test-batch")
    assert files == ["file1.txt", "file2.txt"]


def test_list_batch_files_sorted(temp_git_repo):
    """Test that batch files are returned in sorted order."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "zebra.txt", "z")
    add_file_to_batch("test-batch", "apple.txt", "a")
    add_file_to_batch("test-batch", "middle.txt", "m")

    files = list_batch_files("test-batch")
    assert files == ["apple.txt", "middle.txt", "zebra.txt"]


def test_get_batch_baseline_commit_with_parent(temp_git_repo):
    """Test getting baseline commit for batch."""
    create_batch("test-batch", "Test")

    baseline = get_batch_baseline_commit("test-batch")
    assert baseline is not None
    assert len(baseline) == 40  # Git SHA is 40 hex characters


def test_get_batch_baseline_commit_nonexistent(temp_git_repo):
    """Test getting baseline for nonexistent batch returns None."""
    baseline = get_batch_baseline_commit("nonexistent")
    assert baseline is None
