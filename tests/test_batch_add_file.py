"""Tests for add_file_to_batch git plumbing operations."""

import subprocess

import pytest

from git_stage_batch.batch import (
    add_file_to_batch,
    create_batch,
    get_batch_commit_sha,
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


def test_add_file_to_batch_creates_commit(temp_git_repo):
    """Test that add_file_to_batch creates a git commit."""
    create_batch("test-batch", "Test")

    add_file_to_batch("test-batch", "test.txt", "Hello, world!\n")

    # Verify commit exists
    commit_sha = get_batch_commit_sha("test-batch")
    assert commit_sha is not None
    assert len(commit_sha) == 40


def test_add_file_to_batch_creates_tree(temp_git_repo):
    """Test that add_file_to_batch creates a git tree."""
    create_batch("test-batch", "Test")

    add_file_to_batch("test-batch", "test.txt", "Content\n")

    commit_sha = get_batch_commit_sha("test-batch")

    # Get tree from commit
    tree_result = run_git_command(["rev-parse", f"{commit_sha}^{{tree}}"])
    tree_sha = tree_result.stdout.strip()
    assert len(tree_sha) == 40


def test_add_file_to_batch_stores_file_in_tree(temp_git_repo):
    """Test that file is stored in the tree."""
    create_batch("test-batch", "Test")

    content = "Test content\n"
    add_file_to_batch("test-batch", "myfile.txt", content)

    commit_sha = get_batch_commit_sha("test-batch")

    # Read file from commit
    result = run_git_command(["show", f"{commit_sha}:myfile.txt"])
    assert result.stdout == content


def test_add_file_to_batch_multiple_files(temp_git_repo):
    """Test adding multiple files creates tree with all files."""
    create_batch("test-batch", "Test")

    add_file_to_batch("test-batch", "file1.txt", "Content 1\n")
    add_file_to_batch("test-batch", "file2.txt", "Content 2\n")
    add_file_to_batch("test-batch", "dir/file3.txt", "Content 3\n")

    commit_sha = get_batch_commit_sha("test-batch")
    tree_result = run_git_command(["rev-parse", f"{commit_sha}^{{tree}}"])
    tree_sha = tree_result.stdout.strip()

    # List files in tree
    ls_result = run_git_command(["ls-tree", "-r", "--name-only", tree_sha])
    files = [line.strip() for line in ls_result.stdout.splitlines() if line.strip()]

    assert sorted(files) == ["dir/file3.txt", "file1.txt", "file2.txt"]


def test_add_file_to_batch_updates_existing_file(temp_git_repo):
    """Test that adding file again updates its content."""
    create_batch("test-batch", "Test")

    add_file_to_batch("test-batch", "file.txt", "Version 1\n")
    commit1 = get_batch_commit_sha("test-batch")

    add_file_to_batch("test-batch", "file.txt", "Version 2\n")
    commit2 = get_batch_commit_sha("test-batch")

    # Commits should be different
    assert commit1 != commit2

    # Latest commit should have Version 2
    result = run_git_command(["show", f"{commit2}:file.txt"])
    assert result.stdout == "Version 2\n"


def test_add_file_to_batch_creates_commit_chain(temp_git_repo):
    """Test that multiple adds create a commit chain."""
    create_batch("test-batch", "Test")

    add_file_to_batch("test-batch", "file1.txt", "Content 1\n")
    first_commit = get_batch_commit_sha("test-batch")

    add_file_to_batch("test-batch", "file2.txt", "Content 2\n")
    second_commit = get_batch_commit_sha("test-batch")

    # Second commit should have first as parent
    parent_result = run_git_command(["rev-parse", f"{second_commit}^"])
    assert parent_result.stdout.strip() == first_commit


def test_add_file_to_batch_baseline_is_head(temp_git_repo):
    """Test that batch chain traces back to HEAD."""
    head_sha = run_git_command(["rev-parse", "HEAD"]).stdout.strip()

    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file.txt", "Content\n")

    commit_sha = get_batch_commit_sha("test-batch")

    # Walk back the commit chain to find the root
    # create_batch creates an empty commit, then add_file_to_batch creates a file commit
    # So we need to walk back twice: file-commit → empty-commit → HEAD
    parent_result = run_git_command(["rev-parse", f"{commit_sha}^"])
    empty_commit_sha = parent_result.stdout.strip()

    root_parent_result = run_git_command(["rev-parse", f"{empty_commit_sha}^"])
    root_parent_sha = root_parent_result.stdout.strip()

    assert root_parent_sha == head_sha


def test_add_file_to_batch_auto_creates_batch(temp_git_repo):
    """Test that add_file_to_batch auto-creates batch if it doesn't exist."""
    # Don't call create_batch
    add_file_to_batch("auto-batch", "file.txt", "Content\n")

    # Verify batch ref exists
    result = run_git_command(["show-ref", "--verify", "refs/batches/auto-batch"], check=False)
    assert result.returncode == 0

    # Verify file is in tree
    commit_sha = get_batch_commit_sha("auto-batch")
    file_result = run_git_command(["show", f"{commit_sha}:file.txt"])
    assert file_result.stdout == "Content\n"


def test_add_file_to_batch_preserves_other_files(temp_git_repo):
    """Test that adding a new file preserves existing files."""
    create_batch("test-batch", "Test")

    add_file_to_batch("test-batch", "file1.txt", "Content 1\n")
    add_file_to_batch("test-batch", "file2.txt", "Content 2\n")

    commit_sha = get_batch_commit_sha("test-batch")

    # Both files should exist
    result1 = run_git_command(["show", f"{commit_sha}:file1.txt"])
    assert result1.stdout == "Content 1\n"

    result2 = run_git_command(["show", f"{commit_sha}:file2.txt"])
    assert result2.stdout == "Content 2\n"


def test_add_file_to_batch_with_executable_mode(temp_git_repo):
    """Test adding file with executable mode."""
    create_batch("test-batch", "Test")

    add_file_to_batch("test-batch", "script.sh", "#!/bin/bash\necho hello\n", file_mode="100755")

    commit_sha = get_batch_commit_sha("test-batch")
    tree_result = run_git_command(["rev-parse", f"{commit_sha}^{{tree}}"])
    tree_sha = tree_result.stdout.strip()

    # Check file mode in tree
    ls_result = run_git_command(["ls-tree", tree_sha, "script.sh"])
    # Output format: <mode> <type> <hash>\t<path>
    assert "100755" in ls_result.stdout or "755" in ls_result.stdout


def test_add_file_to_batch_empty_content(temp_git_repo):
    """Test adding file with empty content."""
    create_batch("test-batch", "Test")

    add_file_to_batch("test-batch", "empty.txt", "")

    commit_sha = get_batch_commit_sha("test-batch")
    result = run_git_command(["show", f"{commit_sha}:empty.txt"])
    assert result.stdout == ""


def test_add_file_to_batch_nested_path(temp_git_repo):
    """Test adding file in nested directory."""
    create_batch("test-batch", "Test")

    add_file_to_batch("test-batch", "a/b/c/deep.txt", "Deep content\n")

    commit_sha = get_batch_commit_sha("test-batch")
    result = run_git_command(["show", f"{commit_sha}:a/b/c/deep.txt"])
    assert result.stdout == "Deep content\n"
