"""Tests for batch validation."""

import subprocess

import pytest

from git_stage_batch.batch.state.lifecycle import create_batch
from git_stage_batch.batch.state.query import list_batch_names
from git_stage_batch.batch.state.batch_names import (
    MAX_BATCH_NAME_BYTES,
    batch_exists,
    invalid_file_backed_batch_names,
    validate_batch_name,
)
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.paths import (
    get_batch_directory_path,
    get_batch_metadata_file_path,
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


def test_validate_batch_name_valid(temp_git_repo):
    """Test that valid batch names pass validation."""
    # These should not raise
    validate_batch_name("test-batch")
    validate_batch_name("my_batch")
    validate_batch_name("batch123")
    validate_batch_name("UPPERCASE")
    validate_batch_name("café")


@pytest.mark.parametrize(
    "name",
    [
        "has~tilde",
        "has^caret",
        "has?question",
        "has*asterisk",
        "has[bracket",
        "has@{sequence",
        "trailing.",
        "reserved.lock",
        "control\x01byte",
    ],
)
def test_validate_batch_name_rejects_names_git_cannot_use(temp_git_repo, name):
    with pytest.raises(CommandError) as exc_info:
        validate_batch_name(name)

    assert name in exc_info.value.message
    assert "Git ref naming rules" in exc_info.value.message


def test_validate_batch_name_enforces_storage_safe_byte_limit(temp_git_repo):
    validate_batch_name("a" * MAX_BATCH_NAME_BYTES)

    with pytest.raises(CommandError) as exc_info:
        validate_batch_name("a" * (MAX_BATCH_NAME_BYTES + 1))

    assert f"{MAX_BATCH_NAME_BYTES} UTF-8 bytes" in exc_info.value.message


def test_create_batch_accepts_maximum_length_name(temp_git_repo):
    name = "a" * MAX_BATCH_NAME_BYTES

    create_batch(name)

    assert batch_exists(name)


def test_create_batch_rejects_git_invalid_name_without_metadata(temp_git_repo):
    name = "invalid^name"

    with pytest.raises(CommandError):
        create_batch(name)

    assert not get_batch_directory_path(name).exists()


def test_batch_discovery_reports_invalid_legacy_metadata(temp_git_repo):
    metadata_path = get_batch_metadata_file_path("legacy^name")
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text("{}")

    with pytest.raises(CommandError) as exc_info:
        list_batch_names()

    assert "Legacy batch metadata" in exc_info.value.message
    assert "legacy^name" in exc_info.value.message
    assert "refs/batches" in exc_info.value.message


def test_trusted_ref_name_still_obeys_product_constraints(temp_git_repo):
    """Ref discovery should bypass only Git's already-satisfied name checks."""
    metadata_path = get_batch_metadata_file_path("nested/name")
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text("{}")

    assert invalid_file_backed_batch_names(
        trusted_batch_names={"nested/name"},
    ) == ["nested/name"]


@pytest.mark.parametrize("name", ["ordinary", "café", "release-2026.07"])
def test_validate_batch_name_matches_git_check_ref_format(temp_git_repo, name):
    validate_batch_name(name)

    result = subprocess.run(
        ["git", "check-ref-format", f"refs/git-stage-batch/batches/{name}"],
        check=False,
    )
    assert result.returncode == 0


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


def test_validate_batch_name_with_colon(temp_git_repo):
    """Test that batch name with colon fails validation."""
    with pytest.raises(CommandError) as exc_info:
        validate_batch_name("cleanup:apply")
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


def test_batch_exists_rejects_invalid_name_before_ref_lookup(temp_git_repo):
    with pytest.raises(CommandError):
        batch_exists("invalid^name")


def test_batch_exists_after_creation(temp_git_repo):
    """Test that batch_exists returns True immediately after creation."""
    assert batch_exists("new-batch") is False
    create_batch("new-batch", "Test")
    assert batch_exists("new-batch") is True
