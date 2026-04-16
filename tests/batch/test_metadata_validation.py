"""Tests for batch metadata sanity checking and validation."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from git_stage_batch.batch.metadata_validation import (
    get_validated_baseline_commit,
    load_and_validate_batch_metadata,
    read_validated_batch_metadata,
    require_batch_metadata_sane,
    validate_batch_metadata_file_exists,
    validate_batch_metadata_structure,
    validate_state_directory_exists,
)
from git_stage_batch.batch.operations import create_batch
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.exceptions import BatchMetadataError
from git_stage_batch.utils.file_io import write_text_file_contents
from git_stage_batch.utils.git import run_git_command
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_batch_metadata_file_path,
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

    # Initialize session (needed for batch source creation)
    ensure_state_directory_exists()
    initialize_abort_state()

    return tmp_path


def test_validate_state_directory_exists_success(temp_git_repo):
    """State directory validation succeeds when directory exists."""
    # State directory should be created by test setup
    validate_state_directory_exists()  # Should not raise


def test_validate_state_directory_missing(temp_git_repo):
    """State directory validation fails with clear error when missing."""
    state_dir = get_state_directory_path()

    # Remove state directory
    if state_dir.exists():
        shutil.rmtree(state_dir)

    with pytest.raises(BatchMetadataError) as exc_info:
        validate_state_directory_exists()

    error_msg = str(exc_info.value)
    assert "git-stage-batch metadata directory is missing" in error_msg
    assert str(state_dir) in error_msg


def test_validate_state_directory_is_file(temp_git_repo):
    """State directory validation fails when path is a file."""
    state_dir = get_state_directory_path()

    # Remove directory and create file in its place
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.parent.mkdir(parents=True, exist_ok=True)
    state_dir.write_text("not a directory")

    with pytest.raises(BatchMetadataError) as exc_info:
        validate_state_directory_exists()

    error_msg = str(exc_info.value)
    assert "not a directory" in error_msg
    assert str(state_dir) in error_msg


def test_load_and_validate_without_batch_ref(temp_git_repo):
    """Loading metadata for non-existent batch returns empty structure."""
    # Batch ref doesn't exist, so should return minimal structure
    metadata = load_and_validate_batch_metadata("nonexistent")

    assert metadata["note"] == ""
    assert metadata["baseline"] is None
    assert metadata["files"] == {}


def test_validate_batch_metadata_file_missing_for_existing_ref(temp_git_repo):
    """Metadata file validation fails when ref exists but metadata missing."""
    # Create a batch normally
    create_batch("test-batch", "Test note")

    # Delete metadata file but keep ref
    metadata_path = get_batch_metadata_file_path("test-batch")
    metadata_path.unlink()

    with pytest.raises(BatchMetadataError) as exc_info:
        validate_batch_metadata_file_exists("test-batch")

    error_msg = str(exc_info.value)
    assert "Batch metadata is missing or corrupted for 'test-batch'" in error_msg
    assert "refs/batches/test-batch" in error_msg
    assert str(metadata_path) in error_msg
    assert "not be recoverable automatically" in error_msg


def test_validate_batch_metadata_file_orphaned_metadata(temp_git_repo):
    """Metadata file validation doesn't fail for orphaned metadata (ref missing)."""
    # Create metadata file without ref
    batch_name = "orphan-batch"
    metadata_path = get_batch_metadata_file_path(batch_name)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)

    metadata = {
        "note": "Orphaned",
        "created_at": "2024-01-01T00:00:00",
        "baseline": "HEAD",
        "files": {}
    }
    write_text_file_contents(metadata_path, json.dumps(metadata))

    # Should not raise - batch effectively doesn't exist
    validate_batch_metadata_file_exists(batch_name)


def test_validate_batch_metadata_structure_missing_baseline_field(temp_git_repo):
    """Metadata structure validation fails when baseline field is missing."""
    metadata = {
        "note": "Test",
        "created_at": "2024-01-01T00:00:00",
        # Missing "baseline"
        "files": {}
    }

    with pytest.raises(BatchMetadataError) as exc_info:
        validate_batch_metadata_structure(metadata, "test-batch")

    error_msg = str(exc_info.value)
    assert "missing required field: 'baseline'" in error_msg
    assert "test-batch" in error_msg


def test_validate_batch_metadata_structure_null_baseline(temp_git_repo):
    """Metadata structure validation fails when baseline is null."""
    metadata = {
        "note": "Test",
        "created_at": "2024-01-01T00:00:00",
        "baseline": None,
        "files": {}
    }

    with pytest.raises(BatchMetadataError) as exc_info:
        validate_batch_metadata_structure(metadata, "test-batch")

    error_msg = str(exc_info.value)
    assert "has no baseline commit" in error_msg
    assert "test-batch" in error_msg
    assert "corrupted or incomplete" in error_msg


def test_validate_batch_metadata_structure_invalid_baseline_commit(temp_git_repo):
    """Metadata structure validation fails when baseline commit doesn't exist."""
    metadata = {
        "note": "Test",
        "created_at": "2024-01-01T00:00:00",
        "baseline": "0000000000000000000000000000000000000000",
        "files": {}
    }

    with pytest.raises(BatchMetadataError) as exc_info:
        validate_batch_metadata_structure(metadata, "test-batch")

    error_msg = str(exc_info.value)
    assert "invalid baseline commit" in error_msg
    assert "0000000000000000000000000000000000000000" in error_msg
    assert "does not exist in the repository" in error_msg


def test_validate_batch_metadata_structure_files_not_dict(temp_git_repo):
    """Metadata structure validation fails when files field is not a dict."""
    # Get a valid commit for baseline
    result = run_git_command(["rev-parse", "HEAD"])
    baseline = result.stdout.strip()

    metadata = {
        "note": "Test",
        "created_at": "2024-01-01T00:00:00",
        "baseline": baseline,
        "files": "not a dict"
    }

    with pytest.raises(BatchMetadataError) as exc_info:
        validate_batch_metadata_structure(metadata, "test-batch")

    error_msg = str(exc_info.value)
    assert "invalid 'files' field" in error_msg
    assert "expected object" in error_msg


def test_validate_batch_metadata_structure_file_entry_not_dict(temp_git_repo):
    """Metadata structure validation fails when file entry is not a dict."""
    result = run_git_command(["rev-parse", "HEAD"])
    baseline = result.stdout.strip()

    metadata = {
        "note": "Test",
        "created_at": "2024-01-01T00:00:00",
        "baseline": baseline,
        "files": {
            "test.txt": "not a dict"
        }
    }

    with pytest.raises(BatchMetadataError) as exc_info:
        validate_batch_metadata_structure(metadata, "test-batch")

    error_msg = str(exc_info.value)
    assert "invalid file entry for 'test.txt'" in error_msg


def test_validate_batch_metadata_structure_missing_batch_source_commit(temp_git_repo):
    """Metadata structure validation fails when batch_source_commit is missing for text file."""
    result = run_git_command(["rev-parse", "HEAD"])
    baseline = result.stdout.strip()

    metadata = {
        "note": "Test",
        "created_at": "2024-01-01T00:00:00",
        "baseline": baseline,
        "files": {
            "test.txt": {
                # Missing batch_source_commit
                "claimed_lines": [],
                "mode": "100644"
            }
        }
    }

    with pytest.raises(BatchMetadataError) as exc_info:
        validate_batch_metadata_structure(metadata, "test-batch")

    error_msg = str(exc_info.value)
    assert "missing 'batch_source_commit'" in error_msg
    assert "test.txt" in error_msg


def test_validate_batch_metadata_structure_binary_file_without_source_commit(temp_git_repo):
    """Metadata structure validation allows binary files without batch_source_commit."""
    result = run_git_command(["rev-parse", "HEAD"])
    baseline = result.stdout.strip()

    metadata = {
        "note": "Test",
        "created_at": "2024-01-01T00:00:00",
        "baseline": baseline,
        "files": {
            "image.png": {
                "file_type": "binary",
                "change_type": "modified",
                "mode": "100644"
            }
        }
    }

    # Should not raise for binary files
    validate_batch_metadata_structure(metadata, "test-batch")


def test_validate_batch_metadata_structure_invalid_batch_source_commit(temp_git_repo):
    """Metadata structure validation fails when batch_source_commit doesn't exist."""
    result = run_git_command(["rev-parse", "HEAD"])
    baseline = result.stdout.strip()

    metadata = {
        "note": "Test",
        "created_at": "2024-01-01T00:00:00",
        "baseline": baseline,
        "files": {
            "test.txt": {
                "batch_source_commit": "0000000000000000000000000000000000000000",
                "claimed_lines": [],
                "mode": "100644"
            }
        }
    }

    with pytest.raises(BatchMetadataError) as exc_info:
        validate_batch_metadata_structure(metadata, "test-batch")

    error_msg = str(exc_info.value)
    assert "invalid batch_source_commit" in error_msg
    assert "test.txt" in error_msg
    assert "0000000000000000000000000000000000000000" in error_msg


def test_load_and_validate_batch_metadata_success(temp_git_repo):
    """Loading and validating batch metadata succeeds for valid batch."""
    create_batch("test-batch", "Test note")

    metadata = load_and_validate_batch_metadata("test-batch")

    assert metadata["note"] == "Test note"
    assert metadata["baseline"] is not None
    assert isinstance(metadata["files"], dict)


def test_load_and_validate_batch_metadata_malformed_json(temp_git_repo):
    """Loading batch metadata fails with clear error for malformed JSON."""
    create_batch("test-batch", "Test note")

    # Write malformed JSON
    metadata_path = get_batch_metadata_file_path("test-batch")
    write_text_file_contents(metadata_path, "{ invalid json")

    with pytest.raises(BatchMetadataError) as exc_info:
        load_and_validate_batch_metadata("test-batch")

    error_msg = str(exc_info.value)
    assert "corrupted (invalid JSON)" in error_msg
    assert str(metadata_path) in error_msg


def test_load_and_validate_batch_metadata_missing_file(temp_git_repo):
    """Loading batch metadata returns minimal structure for missing file."""
    # Don't create batch, just try to load
    metadata = load_and_validate_batch_metadata("nonexistent")

    assert metadata["note"] == ""
    assert metadata["baseline"] is None
    assert metadata["files"] == {}


def test_get_validated_baseline_commit_success(temp_git_repo):
    """Getting validated baseline commit succeeds for valid batch."""
    create_batch("test-batch", "Test note")

    baseline = get_validated_baseline_commit("test-batch")

    assert baseline is not None
    assert len(baseline) == 40  # SHA1 hash length


def test_get_validated_baseline_commit_missing(temp_git_repo):
    """Getting validated baseline commit fails when baseline is missing."""
    create_batch("test-batch", "Test note")

    # Corrupt metadata by removing baseline
    metadata_path = get_batch_metadata_file_path("test-batch")
    metadata = json.loads(metadata_path.read_text())
    metadata["baseline"] = None
    write_text_file_contents(metadata_path, json.dumps(metadata))

    with pytest.raises(BatchMetadataError) as exc_info:
        get_validated_baseline_commit("test-batch")

    error_msg = str(exc_info.value)
    assert "has no baseline commit" in error_msg
    assert "test-batch" in error_msg


def test_read_validated_batch_metadata_success(temp_git_repo):
    """Reading validated batch metadata succeeds for valid batch."""
    create_batch("test-batch", "Test note")

    metadata = read_validated_batch_metadata("test-batch")

    assert metadata["note"] == "Test note"
    assert metadata["baseline"] is not None


def test_read_validated_batch_metadata_corrupted(temp_git_repo):
    """Reading validated batch metadata fails for corrupted metadata."""
    create_batch("test-batch", "Test note")

    # Corrupt metadata
    metadata_path = get_batch_metadata_file_path("test-batch")
    write_text_file_contents(metadata_path, "corrupted")

    with pytest.raises(BatchMetadataError) as exc_info:
        read_validated_batch_metadata("test-batch")

    error_msg = str(exc_info.value)
    assert "corrupted" in error_msg.lower()


def test_require_batch_metadata_sane_success(temp_git_repo):
    """Requiring sane metadata succeeds for valid batch."""
    create_batch("test-batch", "Test note")

    # Should not raise
    require_batch_metadata_sane("test-batch")


def test_require_batch_metadata_sane_corrupted(temp_git_repo):
    """Requiring sane metadata fails for corrupted batch."""
    create_batch("test-batch", "Test note")

    # Delete metadata file but keep ref
    metadata_path = get_batch_metadata_file_path("test-batch")
    metadata_path.unlink()

    with pytest.raises(BatchMetadataError) as exc_info:
        require_batch_metadata_sane("test-batch")

    error_msg = str(exc_info.value)
    assert "missing or corrupted" in error_msg


def test_validate_batch_metadata_structure_valid_structure(temp_git_repo):
    """Metadata structure validation succeeds for valid structure."""
    result = run_git_command(["rev-parse", "HEAD"])
    baseline = result.stdout.strip()

    # Create a commit for batch source
    Path("test.txt").write_text("content")
    run_git_command(["add", "test.txt"])
    run_git_command(["commit", "-m", "Test commit"])
    source_commit = run_git_command(["rev-parse", "HEAD"]).stdout.strip()

    metadata = {
        "note": "Test",
        "created_at": "2024-01-01T00:00:00",
        "baseline": baseline,
        "files": {
            "test.txt": {
                "batch_source_commit": source_commit,
                "claimed_lines": ["1-5"],
                "mode": "100644"
            }
        }
    }

    # Should not raise
    validate_batch_metadata_structure(metadata, "test-batch")
