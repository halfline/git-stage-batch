"""Tests for batch validation."""

import subprocess

import pytest

from git_stage_batch.batch.operations import create_batch
from git_stage_batch.batch.validation import batch_exists, validate_batch_name
from git_stage_batch.exceptions import CommandError


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


def test_validate_batch_name_valid(temp_git_repo):
    """Test that valid batch names pass validation."""
    # These should not raise
    validate_batch_name("test-batch")
    validate_batch_name("my_batch")
    validate_batch_name("batch123")
    validate_batch_name("UPPERCASE")


def test_validate_batch_name_empty(temp_git_repo):
    """Test that empty batch name fails validation."""
    with pytest.raises(CommandError) as exc_info:
        validate_batch_name("")
    assert "cannot be empty" in exc_info.value.message


def test_validate_batch_name_with_slash(temp_git_repo):
    """Test that batch name with slash fails validation."""
    with pytest.raises(CommandError) as exc_info:
        validate_batch_name("test/batch")
    assert "cannot contain" in exc_info.value.message


def test_validate_batch_name_with_backslash(temp_git_repo):
    """Test that batch name with backslash fails validation."""
    with pytest.raises(CommandError) as exc_info:
        validate_batch_name("test\\batch")
    assert "cannot contain" in exc_info.value.message


def test_validate_batch_name_with_dotdot(temp_git_repo):
    """Test that batch name with .. fails validation."""
    with pytest.raises(CommandError) as exc_info:
        validate_batch_name("test..batch")
    assert "cannot contain" in exc_info.value.message


def test_validate_batch_name_with_space(temp_git_repo):
    """Test that batch name with space fails validation."""
    with pytest.raises(CommandError) as exc_info:
        validate_batch_name("test batch")
    assert "cannot contain" in exc_info.value.message


def test_validate_batch_name_starting_with_dot(temp_git_repo):
    """Test that batch name starting with dot fails validation."""
    with pytest.raises(CommandError) as exc_info:
        validate_batch_name(".hidden")
    assert "cannot start with" in exc_info.value.message


def test_batch_exists_true(temp_git_repo):
    """Test that batch_exists returns True for existing batch."""
    create_batch("test-batch", "Test")
    assert batch_exists("test-batch") is True


def test_batch_exists_false(temp_git_repo):
    """Test that batch_exists returns False for nonexistent batch."""
    assert batch_exists("nonexistent") is False


def test_batch_exists_after_creation(temp_git_repo):
    """Test that batch_exists returns True immediately after creation."""
    assert batch_exists("new-batch") is False
    create_batch("new-batch", "Test")
    assert batch_exists("new-batch") is True
