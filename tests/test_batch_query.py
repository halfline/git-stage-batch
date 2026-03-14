"""Tests for batch query and diff operations."""

import subprocess

import pytest

from git_stage_batch.batch import (
    add_file_to_batch,
    create_batch,
    get_batch_baseline_commit,
    get_batch_diff,
    get_batch_tree_sha,
    list_batch_files,
    read_file_from_batch,
)
from git_stage_batch.state import run_git_command


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


def test_read_file_from_batch(temp_git_repo):
    """Test reading file content from batch."""
    create_batch("test-batch", "Test")
    content = "Hello, world!\n"
    add_file_to_batch("test-batch", "file.txt", content)

    retrieved = read_file_from_batch("test-batch", "file.txt")
    assert retrieved == content


def test_read_file_from_nonexistent_batch(temp_git_repo):
    """Test reading from nonexistent batch returns None."""
    result = read_file_from_batch("nonexistent", "file.txt")
    assert result is None


def test_read_nonexistent_file_from_batch(temp_git_repo):
    """Test reading nonexistent file returns None."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "exists.txt", "Content\n")

    result = read_file_from_batch("test-batch", "nonexistent.txt")
    assert result is None


def test_get_batch_tree_sha(temp_git_repo):
    """Test getting tree SHA from batch commit."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file.txt", "Content\n")

    tree_sha = get_batch_tree_sha("test-batch")
    assert tree_sha is not None
    assert len(tree_sha) == 40


def test_get_tree_sha_nonexistent_batch(temp_git_repo):
    """Test getting tree SHA for nonexistent batch."""
    tree_sha = get_batch_tree_sha("nonexistent")
    assert tree_sha is None


def test_list_batch_files_empty(temp_git_repo):
    """Test listing files from empty batch."""
    create_batch("test-batch", "Test")

    files = list_batch_files("test-batch")
    assert files == []


def test_list_batch_files_single_file(temp_git_repo):
    """Test listing single file in batch."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file.txt", "Content\n")

    files = list_batch_files("test-batch")
    assert files == ["file.txt"]


def test_list_batch_files_multiple_files(temp_git_repo):
    """Test listing multiple files in batch."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file1.txt", "Content 1\n")
    add_file_to_batch("test-batch", "file2.txt", "Content 2\n")
    add_file_to_batch("test-batch", "dir/file3.txt", "Content 3\n")

    files = list_batch_files("test-batch")
    assert files == ["dir/file3.txt", "file1.txt", "file2.txt"]


def test_list_batch_files_nonexistent_batch(temp_git_repo):
    """Test listing files from nonexistent batch."""
    files = list_batch_files("nonexistent")
    assert files == []


def test_get_batch_baseline_commit(temp_git_repo):
    """Test getting baseline commit."""
    head_sha = run_git_command(["rev-parse", "HEAD"]).stdout.strip()

    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file.txt", "Content\n")

    baseline = get_batch_baseline_commit("test-batch")
    assert baseline == head_sha


def test_get_baseline_nonexistent_batch(temp_git_repo):
    """Test getting baseline for nonexistent batch."""
    baseline = get_batch_baseline_commit("nonexistent")
    assert baseline is None


def test_get_baseline_after_multiple_adds(temp_git_repo):
    """Test that baseline remains the same after multiple file additions."""
    head_sha = run_git_command(["rev-parse", "HEAD"]).stdout.strip()

    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file1.txt", "Content 1\n")
    add_file_to_batch("test-batch", "file2.txt", "Content 2\n")
    add_file_to_batch("test-batch", "file3.txt", "Content 3\n")

    baseline = get_batch_baseline_commit("test-batch")
    assert baseline == head_sha


def test_get_batch_diff_shows_additions(temp_git_repo):
    """Test that diff shows file additions."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "newfile.txt", "New content\n")

    diff = get_batch_diff("test-batch")
    assert "newfile.txt" in diff
    assert "+New content" in diff
    assert "diff --git" in diff


def test_get_batch_diff_empty_batch(temp_git_repo):
    """Test that empty batch shows deletion of baseline files."""
    create_batch("test-batch", "Test")

    diff = get_batch_diff("test-batch")
    # Empty batch has empty tree, so baseline files appear as deletions
    assert "README" in diff
    assert "deleted file" in diff or "-initial" in diff


def test_get_batch_diff_nonexistent_batch(temp_git_repo):
    """Test diff for nonexistent batch."""
    diff = get_batch_diff("nonexistent")
    assert diff == ""


def test_get_batch_diff_multiple_files(temp_git_repo):
    """Test diff with multiple files."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file1.txt", "Content 1\n")
    add_file_to_batch("test-batch", "file2.txt", "Content 2\n")

    diff = get_batch_diff("test-batch")
    assert "file1.txt" in diff
    assert "file2.txt" in diff
    assert "+Content 1" in diff
    assert "+Content 2" in diff


def test_get_batch_diff_with_context_lines(temp_git_repo):
    """Test diff with custom context lines."""
    # Create a baseline file first
    (temp_git_repo / "baseline.txt").write_text("Line A\nLine B\nLine C\nLine D\nLine E\n")
    subprocess.run(["git", "add", "baseline.txt"], check=True)
    subprocess.run(["git", "commit", "-m", "Add baseline"], check=True, capture_output=True)

    create_batch("test-batch", "Test")
    # Modify middle line
    add_file_to_batch("test-batch", "baseline.txt", "Line A\nLine B\nMODIFIED\nLine D\nLine E\n")

    diff_context_1 = get_batch_diff("test-batch", context_lines=1)
    diff_context_3 = get_batch_diff("test-batch", context_lines=3)

    # Both should contain the file
    assert "baseline.txt" in diff_context_1
    assert "baseline.txt" in diff_context_3
    # Different context should produce different diffs
    # Context 1 shows less surrounding lines than context 3
    assert len(diff_context_1) < len(diff_context_3)


def test_read_file_from_batch_with_nested_path(temp_git_repo):
    """Test reading file from nested directory in batch."""
    create_batch("test-batch", "Test")
    content = "Nested content\n"
    add_file_to_batch("test-batch", "a/b/c/deep.txt", content)

    retrieved = read_file_from_batch("test-batch", "a/b/c/deep.txt")
    assert retrieved == content


def test_read_file_from_batch_empty_content(temp_git_repo):
    """Test reading empty file from batch."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "empty.txt", "")

    retrieved = read_file_from_batch("test-batch", "empty.txt")
    assert retrieved == ""


def test_batch_diff_shows_file_updates(temp_git_repo):
    """Test that diff shows file modifications."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file.txt", "Version 1\n")
    add_file_to_batch("test-batch", "file.txt", "Version 2\n")

    diff = get_batch_diff("test-batch")
    assert "file.txt" in diff
    # Should show the final version
    assert "+Version 2" in diff
