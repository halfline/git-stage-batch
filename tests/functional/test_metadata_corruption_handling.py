"""Integration tests for batch metadata corruption error handling.

These tests verify that missing or corrupted batch metadata produces
clear top-level errors instead of confusing downstream failures.
"""

import json
import subprocess

import pytest

from git_stage_batch.batch.operations import create_batch
from git_stage_batch.batch.query import read_batch_metadata
from git_stage_batch.commands.show_from import command_show_from_batch
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.paths import get_state_directory_path


def _write_state_batch_json(batch_name: str, content: str) -> None:
    """Replace authoritative batch.json for a batch state ref."""
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        input=content,
        text=True,
        check=True,
        capture_output=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "mktree"],
        input=f"100644 blob {blob}\tbatch.json\n",
        text=True,
        check=True,
        capture_output=True,
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "commit-tree", tree, "-m", f"Corrupt batch state: {batch_name}"],
        text=True,
        check=True,
        capture_output=True,
    ).stdout.strip()
    subprocess.run(["git", "update-ref", f"refs/git-stage-batch/state/{batch_name}", commit], check=True)


def _write_state_metadata(batch_name: str, metadata: dict) -> None:
    state_metadata = {
        "batch": batch_name,
        "note": metadata.get("note", ""),
        "created_at": metadata.get("created_at", ""),
        "baseline_commit": metadata.get("baseline"),
        "content_ref": f"refs/git-stage-batch/batches/{batch_name}",
        "content_commit": subprocess.run(
            ["git", "rev-parse", f"refs/git-stage-batch/batches/{batch_name}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "files": metadata.get("files", {}),
    }
    _write_state_batch_json(batch_name, json.dumps(state_metadata))


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
    """Test missing compatibility metadata does not break authoritative state."""
    create_batch("test-batch", "Test")

    # Create stale legacy state alongside authoritative state.
    subprocess.run(["git", "update-ref", "refs/batches/test-batch", "HEAD"], check=True)

    command_show_from_batch("test-batch")


def test_corrupted_state_json_clear_error(functional_repo):
    """Test that corrupted JSON in authoritative state produces clear error."""
    # Create a batch
    create_batch("test-batch", "Test")

    _write_state_batch_json("test-batch", "{ corrupted json syntax")

    # Try to show batch - should get clear error about corrupted metadata
    with pytest.raises(CommandError) as exc_info:
        command_show_from_batch("test-batch")

    error_msg = str(exc_info.value.message)
    assert "failed to read batch metadata" in error_msg.lower()


def test_missing_baseline_clear_error(functional_repo):
    """Test that missing baseline in metadata produces clear error."""
    # Create a batch
    create_batch("test-batch", "Test")

    metadata = read_batch_metadata("test-batch")
    metadata["baseline"] = None
    _write_state_metadata("test-batch", metadata)

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

    metadata = read_batch_metadata("test-batch")
    metadata["files"] = {
        "test.txt": {
            "claimed_lines": ["1-3"],
            "mode": "100644"
            # Missing batch_source_commit
        }
    }
    _write_state_metadata("test-batch", metadata)

    # Try to show batch - should get clear error about missing batch_source_commit
    with pytest.raises(CommandError) as exc_info:
        command_show_from_batch("test-batch")

    error_msg = str(exc_info.value.message)
    assert "missing 'batch_source_commit'" in error_msg.lower()


def test_invalid_baseline_commit_clear_error(functional_repo):
    """Test that invalid baseline commit SHA produces clear error."""
    # Create a batch
    create_batch("test-batch", "Test")

    metadata = read_batch_metadata("test-batch")
    metadata["baseline"] = "0000000000000000000000000000000000000000"
    _write_state_metadata("test-batch", metadata)

    # Try to show batch - should get clear error about invalid baseline
    with pytest.raises(CommandError) as exc_info:
        command_show_from_batch("test-batch")

    error_msg = str(exc_info.value.message)
    assert "invalid baseline commit" in error_msg.lower()
    assert "0000000000000000000000000000000000000000" in error_msg
