"""Tests for batch state management functions."""

import subprocess

import pytest

from git_stage_batch.state import (
    CommandError,
    batch_exists,
    get_batch_directory_path,
    get_batch_metadata_file_path,
    get_batch_refs_snapshot_file_path,
    get_batches_directory_path,
    get_state_directory_path,
    run_git_command,
    validate_batch_name,
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


def test_get_batches_directory_path(temp_git_repo):
    """Test that batches directory path is under state directory."""
    batches_dir = get_batches_directory_path()
    state_dir = get_state_directory_path()
    assert batches_dir.parent == state_dir
    assert batches_dir.name == "batches"


def test_get_batch_directory_path(temp_git_repo):
    """Test that batch directory path includes batch name."""
    batch_dir = get_batch_directory_path("my-batch")
    batches_dir = get_batches_directory_path()
    assert batch_dir.parent == batches_dir
    assert batch_dir.name == "my-batch"


def test_get_batch_metadata_file_path(temp_git_repo):
    """Test that metadata file path is under batch directory."""
    metadata_path = get_batch_metadata_file_path("my-batch")
    batch_dir = get_batch_directory_path("my-batch")
    assert metadata_path.parent == batch_dir
    assert metadata_path.name == "metadata.json"


def test_get_batch_refs_snapshot_file_path(temp_git_repo):
    """Test that snapshot file path is under state directory."""
    snapshot_path = get_batch_refs_snapshot_file_path()
    state_dir = get_state_directory_path()
    assert snapshot_path.parent == state_dir
    assert snapshot_path.name == "batch-refs-snapshot.json"


def test_validate_batch_name_valid(temp_git_repo):
    """Test that valid batch names pass validation."""
    # These should not raise
    validate_batch_name("valid")
    validate_batch_name("valid-name")
    validate_batch_name("valid_name")
    validate_batch_name("valid123")


def test_validate_batch_name_empty(temp_git_repo):
    """Test that empty name fails validation."""
    with pytest.raises(CommandError):
        validate_batch_name("")


def test_validate_batch_name_with_slash(temp_git_repo):
    """Test that name with slash fails validation."""
    with pytest.raises(CommandError):
        validate_batch_name("invalid/name")


def test_validate_batch_name_with_backslash(temp_git_repo):
    """Test that name with backslash fails validation."""
    with pytest.raises(CommandError):
        validate_batch_name("invalid\\name")


def test_validate_batch_name_with_dotdot(temp_git_repo):
    """Test that name with .. fails validation."""
    with pytest.raises(CommandError):
        validate_batch_name("invalid..name")


def test_validate_batch_name_with_space(temp_git_repo):
    """Test that name with space fails validation."""
    with pytest.raises(CommandError):
        validate_batch_name("invalid name")


def test_validate_batch_name_leading_dot(temp_git_repo):
    """Test that name starting with dot fails validation."""
    with pytest.raises(CommandError):
        validate_batch_name(".hidden")


def test_batch_exists_nonexistent(temp_git_repo):
    """Test that nonexistent batch returns False."""
    assert not batch_exists("nonexistent")


def test_batch_exists_after_creation(temp_git_repo):
    """Test that batch exists after creating its ref."""
    # Create a batch ref
    run_git_command(["update-ref", "refs/batches/test-batch", "HEAD"])
    assert batch_exists("test-batch")


def test_batch_exists_after_deletion(temp_git_repo):
    """Test that batch doesn't exist after deleting its ref."""
    # Create then delete
    run_git_command(["update-ref", "refs/batches/test-batch", "HEAD"])
    assert batch_exists("test-batch")

    run_git_command(["update-ref", "-d", "refs/batches/test-batch"])
    assert not batch_exists("test-batch")
