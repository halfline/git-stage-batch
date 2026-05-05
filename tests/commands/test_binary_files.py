"""Tests for binary file operations (include, discard, skip)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from git_stage_batch.commands.discard import command_discard
from git_stage_batch.commands.include import command_include, command_include_to_batch
from git_stage_batch.commands.skip import command_skip
from git_stage_batch.commands.status import command_status
from git_stage_batch.core.models import BinaryFileChange, LineLevelChange
from git_stage_batch.data.file_tracking import auto_add_untracked_files
from git_stage_batch.data.hunk_tracking import fetch_next_change
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.utils.git import run_git_command


@pytest.fixture
def binary_file_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository with binary files."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)

    # Create initial commit
    (repo_path / "README.md").write_text("# Test Repository\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True)

    # Add a binary file (a simple PNG - 1x1 transparent pixel)
    binary_content = (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
        b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
        b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    (repo_path / "image.png").write_bytes(binary_content)
    subprocess.run(["git", "add", "image.png"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "Add image"], cwd=repo_path, check=True)

    return repo_path


def test_binary_file_added_include(binary_file_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test including (staging) a newly added binary file."""
    monkeypatch.chdir(binary_file_repo)

    # Add a new binary file to working tree
    new_binary = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
    (binary_file_repo / "new_image.png").write_bytes(new_binary)

    # Initialize session
    initialize_abort_state()
    auto_add_untracked_files()

    # Find and cache the binary file
    result = fetch_next_change()
    assert isinstance(result, BinaryFileChange)  # Binary files return BinaryFileChange from find_and_cache

    # Include (stage) the binary file
    command_include(quiet=True)

    # Verify file was staged (A  means fully staged, not just intent-to-add)
    status_result = run_git_command(["status", "--porcelain"])
    assert "A  new_image.png" in status_result.stdout


def test_binary_file_modified_include(binary_file_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test including a modified binary file."""
    monkeypatch.chdir(binary_file_repo)

    # Modify existing binary file
    modified_binary = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rMODIFIED'
    (binary_file_repo / "image.png").write_bytes(modified_binary)

    # Initialize session
    initialize_abort_state()
    auto_add_untracked_files()

    # Find and cache the binary file
    result = fetch_next_change()
    assert isinstance(result, BinaryFileChange)  # Binary files return BinaryFileChange

    # Include (stage) the binary file
    command_include(quiet=True)

    # Verify file was staged
    status_result = run_git_command(["status", "--porcelain"])
    assert "M  image.png" in status_result.stdout or "MM image.png" in status_result.stdout


def test_binary_file_deleted_include(binary_file_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test including a deleted binary file."""
    monkeypatch.chdir(binary_file_repo)

    # Delete existing binary file
    (binary_file_repo / "image.png").unlink()

    # Initialize session
    initialize_abort_state()
    auto_add_untracked_files()

    # Find and cache the binary file
    result = fetch_next_change()
    assert isinstance(result, BinaryFileChange)  # Binary files return BinaryFileChange

    # Include (stage) the deletion
    command_include(quiet=True)

    # Verify deletion was staged (D  means fully staged deletion)
    status_result = run_git_command(["status", "--porcelain"])
    assert "D  image.png" in status_result.stdout


def test_selected_binary_include_to_batch_updates_skipped_progress(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Selected binary include --to should count as processed in status."""
    monkeypatch.chdir(binary_file_repo)

    (binary_file_repo / "image.png").write_bytes(b"\x89PNG\r\n\x1a\nBATCHED")

    initialize_abort_state()
    auto_add_untracked_files()

    result = fetch_next_change()
    assert isinstance(result, BinaryFileChange)

    command_include_to_batch("bin-batch", quiet=True)

    command_status(porcelain=True)
    status_output = json.loads(capsys.readouterr().out)

    assert status_output["progress"]["skipped"] == 1
    assert len(status_output["skipped_hunks"]) == 1
    skipped_hunk = status_output["skipped_hunks"][0]
    assert skipped_hunk["file"] == "image.png"
    assert skipped_hunk["line"] is None
    assert skipped_hunk["ids"] == []
    assert skipped_hunk["change_type"] == "modified"


def test_binary_file_added_discard(binary_file_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test discarding a newly added binary file."""
    monkeypatch.chdir(binary_file_repo)

    # Add a new binary file to working tree
    new_binary = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
    new_file = binary_file_repo / "new_image.png"
    new_file.write_bytes(new_binary)

    # Initialize session
    initialize_abort_state()
    auto_add_untracked_files()

    # Find and cache the binary file
    result = fetch_next_change()
    assert isinstance(result, BinaryFileChange)  # Binary files return BinaryFileChange

    # Discard the binary file
    command_discard(quiet=True)

    # Verify file was deleted from working tree
    assert not new_file.exists()


def test_binary_file_modified_discard(binary_file_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test discarding modifications to a binary file."""
    monkeypatch.chdir(binary_file_repo)

    # Get original content
    original_content = (binary_file_repo / "image.png").read_bytes()

    # Modify existing binary file
    modified_binary = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rMODIFIED'
    (binary_file_repo / "image.png").write_bytes(modified_binary)

    # Initialize session
    initialize_abort_state()
    auto_add_untracked_files()

    # Find and cache the binary file
    result = fetch_next_change()
    assert isinstance(result, BinaryFileChange)  # Binary files return BinaryFileChange

    # Discard the modifications
    command_discard(quiet=True)

    # Verify file was restored to original
    assert (binary_file_repo / "image.png").read_bytes() == original_content


def test_binary_file_deleted_discard(binary_file_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test discarding deletion of a binary file (restore it)."""
    monkeypatch.chdir(binary_file_repo)

    # Get original content
    original_content = (binary_file_repo / "image.png").read_bytes()

    # Delete existing binary file
    (binary_file_repo / "image.png").unlink()

    # Initialize session
    initialize_abort_state()
    auto_add_untracked_files()

    # Find and cache the binary file
    result = fetch_next_change()
    assert isinstance(result, BinaryFileChange)  # Binary files return BinaryFileChange

    # Discard the deletion (restore file)
    command_discard(quiet=True)

    # Verify file was restored
    assert (binary_file_repo / "image.png").exists()
    assert (binary_file_repo / "image.png").read_bytes() == original_content


def test_binary_file_skip(binary_file_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test skipping a binary file."""
    monkeypatch.chdir(binary_file_repo)

    # Add a new binary file to working tree
    new_binary = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
    new_file = binary_file_repo / "new_image.png"
    new_file.write_bytes(new_binary)

    # Initialize session
    initialize_abort_state()
    auto_add_untracked_files()

    # Find and cache the binary file
    result = fetch_next_change()
    assert isinstance(result, BinaryFileChange)  # Binary files return BinaryFileChange

    # Skip the binary file
    command_skip(quiet=True)

    # Verify file still exists in working tree
    assert new_file.exists()
    assert new_file.read_bytes() == new_binary

    # Verify file remains intent-to-add rather than fully staged.
    # The important thing is it's not fully staged "A  "
    status_result = run_git_command(["status", "--porcelain"])
    assert "A  new_image.png" not in status_result.stdout  # Not fully staged


def test_binary_and_text_mixed(binary_file_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test iteration through mixed binary and text files."""
    monkeypatch.chdir(binary_file_repo)

    # Add a text file
    (binary_file_repo / "text.txt").write_text("Hello\nWorld\n")

    # Add a binary file
    new_binary = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
    (binary_file_repo / "new_image.png").write_bytes(new_binary)

    # Initialize session
    initialize_abort_state()
    auto_add_untracked_files()

    # Find first item (could be text or binary)
    first = fetch_next_change()

    # Skip first item
    command_skip(quiet=True)

    # Find second item
    second = fetch_next_change()

    # One should be a text file (LineLevelChange), one should be binary (BinaryFileChange)
    assert (isinstance(first, BinaryFileChange) and isinstance(second, LineLevelChange)) or \
           (isinstance(first, LineLevelChange) and isinstance(second, BinaryFileChange))
