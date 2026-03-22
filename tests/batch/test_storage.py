"""Tests for batch storage operations."""

import subprocess

import pytest

from git_stage_batch.batch.operations import create_batch
from git_stage_batch.batch.storage import add_file_to_batch, get_batch_diff, read_file_from_batch


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


def test_add_file_to_batch_creates_batch(temp_git_repo):
    """Test that add_file_to_batch auto-creates batch if needed."""
    add_file_to_batch("test-batch", "file.txt", "content")

    content = read_file_from_batch("test-batch", "file.txt")
    assert content == "content"


def test_add_file_to_batch_existing_batch(temp_git_repo):
    """Test adding file to existing batch."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file.txt", "content")

    content = read_file_from_batch("test-batch", "file.txt")
    assert content == "content"


def test_add_file_to_batch_update_file(temp_git_repo):
    """Test updating existing file in batch."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file.txt", "original")
    add_file_to_batch("test-batch", "file.txt", "updated")

    content = read_file_from_batch("test-batch", "file.txt")
    assert content == "updated"


def test_add_file_to_batch_multiple_files(temp_git_repo):
    """Test adding multiple files to batch."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file1.txt", "content1")
    add_file_to_batch("test-batch", "file2.txt", "content2")

    assert read_file_from_batch("test-batch", "file1.txt") == "content1"
    assert read_file_from_batch("test-batch", "file2.txt") == "content2"


def test_read_file_from_batch_nonexistent_batch(temp_git_repo):
    """Test reading file from nonexistent batch returns None."""
    content = read_file_from_batch("nonexistent", "file.txt")
    assert content is None


def test_read_file_from_batch_nonexistent_file(temp_git_repo):
    """Test reading nonexistent file from batch returns None."""
    create_batch("test-batch", "Test")

    content = read_file_from_batch("test-batch", "nonexistent.txt")
    assert content is None


def test_get_batch_diff_empty_batch(temp_git_repo):
    """Test getting diff for empty batch shows removal of baseline files."""
    create_batch("test-batch", "Test")

    diff = get_batch_diff("test-batch")
    # Empty batch has empty tree, so diff shows removal of baseline README
    assert "README" in diff
    assert "-initial" in diff


def test_get_batch_diff_with_file(temp_git_repo):
    """Test getting diff for batch with file."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file.txt", "new content\n")

    diff = get_batch_diff("test-batch")
    assert "file.txt" in diff
    assert "+new content" in diff


def test_get_batch_diff_nonexistent_batch(temp_git_repo):
    """Test getting diff for nonexistent batch returns empty string."""
    diff = get_batch_diff("nonexistent")
    assert diff == ""


def test_get_batch_diff_custom_context(temp_git_repo):
    """Test getting diff with custom context lines."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file.txt", "line1\nline2\nline3\n")

    diff = get_batch_diff("test-batch", context_lines=1)
    assert "file.txt" in diff
