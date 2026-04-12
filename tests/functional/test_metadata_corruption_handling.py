"""Integration tests for batch metadata corruption error handling.

These tests verify that missing or corrupted batch metadata produces
clear top-level errors instead of confusing downstream failures.
"""

import json
import subprocess

import pytest

from git_stage_batch.batch.operations import create_batch
from git_stage_batch.commands.show_from import command_show_from_batch
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.paths import (
    get_batch_metadata_file_path,
    get_state_directory_path,
)


def test_missing_metadata_directory_clear_error(functional_repo):
    """Test that missing metadata when batch ref exists produces clear error."""
    # Create batch ref without metadata (simulates corruption)
    subprocess.run(["git", "update-ref", "refs/batches/test-batch", "HEAD"], check=True)

    # Ensure state directory exists but metadata doesn't
    state_dir = get_state_directory_path()
    state_dir.mkdir(parents=True, exist_ok=True)

    # Try to use batch - should get clear error about missing metadata
    with pytest.raises(CommandError) as exc_info:
        command_show_from_batch("test-batch")

    error_msg = str(exc_info.value.message)
    assert "metadata is missing or corrupted" in error_msg.lower()
    assert "test-batch" in error_msg


def test_missing_metadata_file_clear_error(functional_repo):
    """Test that missing metadata file for existing batch produces clear error."""
    # Create a batch
    create_batch("test-batch", "Test")

    # Create refs/batches/test-batch ref to simulate batch existing
    subprocess.run(["git", "update-ref", "refs/batches/test-batch", "HEAD"], check=True)

    # Delete metadata file but keep ref
    metadata_path = get_batch_metadata_file_path("test-batch")
    metadata_path.unlink()

    # Try to show batch - should get clear error about missing metadata
    with pytest.raises(CommandError) as exc_info:
        command_show_from_batch("test-batch")

    error_msg = str(exc_info.value.message)
    assert "metadata is missing or corrupted" in error_msg.lower()
    assert "test-batch" in error_msg


def test_corrupted_metadata_json_clear_error(functional_repo):
    """Test that corrupted JSON in metadata produces clear error."""
    # Create a batch
    create_batch("test-batch", "Test")

    # Corrupt metadata
    metadata_path = get_batch_metadata_file_path("test-batch")
    metadata_path.write_text("{ corrupted json syntax")

    # Try to show batch - should get clear error about corrupted metadata
    with pytest.raises(CommandError) as exc_info:
        command_show_from_batch("test-batch")

    error_msg = str(exc_info.value.message)
    assert "corrupted" in error_msg.lower()
    assert "invalid json" in error_msg.lower()


def test_missing_baseline_clear_error(functional_repo):
    """Test that missing baseline in metadata produces clear error."""
    # Create a batch
    create_batch("test-batch", "Test")

    # Create refs/batches/test-batch ref to simulate batch existing
    subprocess.run(["git", "update-ref", "refs/batches/test-batch", "HEAD"], check=True)

    # Remove baseline from metadata
    metadata_path = get_batch_metadata_file_path("test-batch")
    metadata = json.loads(metadata_path.read_text())
    metadata["baseline"] = None
    metadata_path.write_text(json.dumps(metadata))

    # Try to show batch - should get clear error about missing baseline
    with pytest.raises(CommandError) as exc_info:
        command_show_from_batch("test-batch")

    error_msg = str(exc_info.value.message)
    assert "no baseline commit" in error_msg.lower()
    assert "test-batch" in error_msg
    assert "corrupted or incomplete" in error_msg.lower()


def test_missing_batch_source_commit_clear_error(functional_repo):
    """Test that missing batch_source_commit produces clear error."""
    # Create a batch with valid baseline
    create_batch("test-batch", "Test")

    # Create refs/batches/test-batch ref to simulate batch existing
    subprocess.run(["git", "update-ref", "refs/batches/test-batch", "HEAD"], check=True)

    # Add a file entry to metadata without batch_source_commit
    metadata_path = get_batch_metadata_file_path("test-batch")
    metadata = json.loads(metadata_path.read_text())
    metadata["files"] = {
        "test.txt": {
            "claimed_lines": ["1-3"],
            "mode": "100644"
            # Missing batch_source_commit
        }
    }
    metadata_path.write_text(json.dumps(metadata))

    # Try to show batch - should get clear error about missing batch_source_commit
    with pytest.raises(CommandError) as exc_info:
        command_show_from_batch("test-batch")

    error_msg = str(exc_info.value.message)
    assert "missing 'batch_source_commit'" in error_msg.lower()


def test_invalid_baseline_commit_clear_error(functional_repo):
    """Test that invalid baseline commit SHA produces clear error."""
    # Create a batch
    create_batch("test-batch", "Test")

    # Set invalid baseline commit
    metadata_path = get_batch_metadata_file_path("test-batch")
    metadata = json.loads(metadata_path.read_text())
    metadata["baseline"] = "0000000000000000000000000000000000000000"
    metadata_path.write_text(json.dumps(metadata))

    # Try to show batch - should get clear error about invalid baseline
    with pytest.raises(CommandError) as exc_info:
        command_show_from_batch("test-batch")

    error_msg = str(exc_info.value.message)
    assert "invalid baseline commit" in error_msg.lower()
    assert "0000000000000000000000000000000000000000" in error_msg
