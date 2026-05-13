"""Tests for binary file operations (include, discard, skip)."""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

import git_stage_batch.commands.apply_from as apply_from_module
import git_stage_batch.commands.include_from as include_from_module
from git_stage_batch.batch.query import read_batch_metadata
from git_stage_batch.batch.storage import add_binary_file_to_batch
from git_stage_batch.commands.apply_from import command_apply_from_batch
from git_stage_batch.commands.discard import command_discard, command_discard_file, command_discard_to_batch
from git_stage_batch.commands.discard_from import command_discard_from_batch
from git_stage_batch.commands.include_from import command_include_from_batch
from git_stage_batch.commands.include import command_include, command_include_file, command_include_to_batch
from git_stage_batch.commands.reset import command_reset_from_batch
from git_stage_batch.commands.show import command_show
from git_stage_batch.commands.show_from import command_show_from_batch
from git_stage_batch.commands.skip import command_skip, command_skip_file
from git_stage_batch.commands.status import command_status
from git_stage_batch.core.models import BinaryFileChange, LineLevelChange
from git_stage_batch.data.file_tracking import auto_add_untracked_files
from git_stage_batch.data.hunk_tracking import (
    SelectedChangeKind,
    fetch_next_change,
    get_selected_change_file_path,
    read_selected_change_kind,
)
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.exceptions import CommandError
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



def test_binary_apply_from_batch_refuses_missing_non_deleted_content(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Modified binary metadata with missing batch content must not apply as a deletion."""
    monkeypatch.chdir(binary_file_repo)

    new_path = binary_file_repo / "new.bin"
    new_content = b"\x00BATCHED"
    new_path.write_bytes(new_content)

    initialize_abort_state()
    auto_add_untracked_files()
    command_include_to_batch("bin-batch", file="new.bin", quiet=True)
    head_commit = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
    monkeypatch.setattr(apply_from_module, "get_batch_commit_sha", lambda name: head_commit)

    with pytest.raises(CommandError, match="incompatible"):
        command_apply_from_batch("bin-batch", file="new.bin")

    assert new_path.exists()
    assert new_path.read_bytes() == new_content


def test_binary_apply_from_batch_restores_executable_mode(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Applying a binary batch entry should honor the stored executable bit."""
    monkeypatch.chdir(binary_file_repo)

    tool_path = binary_file_repo / "tool.bin"
    modified_content = b"\x00BATCHED"
    tool_path.write_bytes(b"\x00BASE")
    tool_path.chmod(0o755)
    subprocess.run(["git", "add", "tool.bin"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add executable binary"], check=True, capture_output=True)

    initialize_abort_state()
    tool_path.write_bytes(modified_content)
    command_include_to_batch("bin-batch", file="tool.bin", quiet=True)
    subprocess.run(["git", "checkout", "HEAD", "--", "tool.bin"], check=True, capture_output=True)
    tool_path.chmod(0o644)

    command_apply_from_batch("bin-batch", file="tool.bin")

    assert tool_path.read_bytes() == modified_content
    assert stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR


def test_binary_discard_from_batch_restores_baseline_executable_mode(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """discard --from should restore baseline executable bits for binaries."""
    monkeypatch.chdir(binary_file_repo)

    tool_path = binary_file_repo / "tool.bin"
    tool_path.write_bytes(b"\x00BASE")
    tool_path.chmod(0o755)
    subprocess.run(["git", "add", "tool.bin"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add executable binary"], check=True, capture_output=True)

    initialize_abort_state()
    tool_path.write_bytes(b"\x00BATCHED")
    command_include_to_batch("bin-batch", file="tool.bin", quiet=True)
    tool_path.write_bytes(b"\x00WORKTREE")
    tool_path.chmod(0o644)

    command_discard_from_batch("bin-batch", file="tool.bin")

    assert tool_path.read_bytes() == b"\x00BASE"
    assert stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR

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


def test_selected_binary_include_to_batch(binary_file_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Selected binary include --to should not treat the binary as a text patch."""
    monkeypatch.chdir(binary_file_repo)

    (binary_file_repo / "image.png").write_bytes(b"\x89PNG\r\n\x1a\nBATCHED")

    initialize_abort_state()
    result = fetch_next_change()
    assert isinstance(result, BinaryFileChange)

    command_include_to_batch("bin-batch", quiet=True)

    metadata = read_batch_metadata("bin-batch")
    assert metadata["files"]["image.png"]["file_type"] == "binary"


def test_pathless_include_to_batch_uses_selected_binary_not_first_text_change(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pathless include --to should honor an explicitly selected binary file."""
    monkeypatch.chdir(binary_file_repo)

    (binary_file_repo / "README.md").write_text("# Test Repository\ntext change\n")
    (binary_file_repo / "image.png").write_bytes(b"\x89PNG\r\n\x1a\nBATCHED")

    initialize_abort_state()
    command_show(file="image.png", porcelain=True)

    command_include_to_batch("bin-batch", quiet=True)

    metadata = read_batch_metadata("bin-batch")
    assert list(metadata["files"].keys()) == ["image.png"]
    assert metadata["files"]["image.png"]["file_type"] == "binary"


def test_pathless_batch_binary_action_refuses_after_batch_bytes_change(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pathless batch binary action must not reuse stale reviewed bytes."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    original_content = image_path.read_bytes()

    initialize_abort_state()
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nREVIEWED")
    command_include_to_batch("bin-batch", file="image.png", quiet=True)
    command_show_from_batch("bin-batch", file="image.png")
    assert read_selected_change_kind() == SelectedChangeKind.BATCH_BINARY
    assert get_selected_change_file_path() == "image.png"

    image_path.write_bytes(b"\x89PNG\r\n\x1a\nUPDATED")
    add_binary_file_to_batch(
        "bin-batch",
        BinaryFileChange(
            old_path="image.png",
            new_path="image.png",
            change_type="modified",
        ),
    )
    subprocess.run(["git", "checkout", "HEAD", "--", "image.png"], check=True, capture_output=True)

    with pytest.raises(CommandError, match="selected batch binary no longer matches"):
        command_apply_from_batch("bin-batch")
    assert image_path.read_bytes() == original_content


def test_stale_batch_binary_refusal_survives_repeated_pathless_action(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a stale batch-binary refusal, a second bare action must not use the whole batch."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    other_path = binary_file_repo / "other.bin"
    other_path.write_bytes(b"\x00OTHER BASE")
    subprocess.run(["git", "add", "other.bin"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add other binary"], check=True, capture_output=True)

    initialize_abort_state()
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nREVIEWED")
    other_path.write_bytes(b"\x00OTHER BATCHED")
    command_include_to_batch("mixed-batch", file="image.png", quiet=True)
    command_include_to_batch("mixed-batch", file="other.bin", quiet=True)
    subprocess.run(["git", "checkout", "HEAD", "--", "."], check=True, capture_output=True)

    command_show_from_batch("mixed-batch", file="image.png")
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nUPDATED")
    add_binary_file_to_batch(
        "mixed-batch",
        BinaryFileChange(
            old_path="image.png",
            new_path="image.png",
            change_type="modified",
        ),
    )
    subprocess.run(["git", "checkout", "HEAD", "--", "image.png"], check=True, capture_output=True)

    with pytest.raises(CommandError, match="selected batch binary no longer matches"):
        command_include_from_batch("mixed-batch")
    with pytest.raises(CommandError, match="changed or removed from batch"):
        command_include_from_batch("mixed-batch")

    staged_files = run_git_command(["diff", "--cached", "--name-only"]).stdout.splitlines()
    assert staged_files == []


@pytest.mark.parametrize(
    "batch_binary_action",
    [
        lambda batch_name: command_include_from_batch(batch_name, file=""),
        lambda batch_name: command_discard_from_batch(batch_name, file=""),
        lambda batch_name: command_apply_from_batch(batch_name, file=""),
        lambda batch_name: command_reset_from_batch(batch_name, file=""),
    ],
)
def test_stale_batch_binary_empty_file_scope_refuses(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    batch_binary_action,
) -> None:
    """`--file` with no path must use the same batch-binary freshness check as bare actions."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    original_content = image_path.read_bytes()

    initialize_abort_state()
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nREVIEWED")
    command_include_to_batch("bin-batch", file="image.png", quiet=True)
    command_show_from_batch("bin-batch", file="image.png")

    image_path.write_bytes(b"\x89PNG\r\n\x1a\nUPDATED")
    add_binary_file_to_batch(
        "bin-batch",
        BinaryFileChange(
            old_path="image.png",
            new_path="image.png",
            change_type="modified",
        ),
    )
    subprocess.run(["git", "checkout", "HEAD", "--", "image.png"], check=True, capture_output=True)

    with pytest.raises(CommandError, match="selected batch binary no longer matches"):
        batch_binary_action("bin-batch")

    assert image_path.read_bytes() == original_content
    assert "image.png" in read_batch_metadata("bin-batch").get("files", {})
    staged_files = run_git_command(["diff", "--cached", "--name-only"]).stdout.splitlines()
    assert staged_files == []


def test_show_from_batch_binary_selects_file_for_empty_file_scope(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Showing one batch binary should let --file reuse that file path."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    modified_content = b"\x89PNG\r\n\x1a\nBATCHED"
    image_path.write_bytes(modified_content)
    (binary_file_repo / "README.md").write_text("# Test Repository\ntext change\n")

    initialize_abort_state()
    command_include_to_batch("mixed-batch", file="image.png", quiet=True)
    command_include_to_batch("mixed-batch", file="README.md", quiet=True)
    subprocess.run(["git", "checkout", "HEAD", "--", "."], check=True, capture_output=True)
    capsys.readouterr()

    command_show_from_batch("mixed-batch", file="image.png")
    capsys.readouterr()

    assert read_selected_change_kind() == SelectedChangeKind.BATCH_BINARY

    command_include_from_batch("mixed-batch", file="")

    staged_files = run_git_command(["diff", "--cached", "--name-only"]).stdout.splitlines()
    assert staged_files == ["image.png"]
    assert run_git_command(["show", ":image.png"], text_output=False).stdout == modified_content


def test_selected_batch_binary_bare_apply_from_batch_uses_selected_file(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bare apply --from should honor a selected batch binary in a multi-file batch."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    readme_path = binary_file_repo / "README.md"
    modified_content = b"\x89PNG\r\n\x1a\nBATCHED"
    image_path.write_bytes(modified_content)
    readme_path.write_text("# Test Repository\ntext change\n")

    initialize_abort_state()
    command_include_to_batch("mixed-batch", file="image.png", quiet=True)
    command_include_to_batch("mixed-batch", file="README.md", quiet=True)
    subprocess.run(
        ["git", "checkout", "HEAD", "--", "image.png", "README.md"],
        check=True,
        capture_output=True,
    )
    capsys.readouterr()

    command_show_from_batch("mixed-batch", file="image.png")
    capsys.readouterr()
    command_apply_from_batch("mixed-batch")

    assert image_path.read_bytes() == modified_content
    assert readme_path.read_text() == "# Test Repository\n"


def test_selected_batch_binary_bare_reset_from_batch_uses_selected_file(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bare reset --from should not clear an entire multi-file batch after binary selection."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    readme_path = binary_file_repo / "README.md"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nBATCHED")
    readme_path.write_text("# Test Repository\ntext change\n")

    initialize_abort_state()
    command_include_to_batch("mixed-batch", file="image.png", quiet=True)
    command_include_to_batch("mixed-batch", file="README.md", quiet=True)
    capsys.readouterr()

    command_show_from_batch("mixed-batch", file="image.png")
    capsys.readouterr()
    command_reset_from_batch("mixed-batch")

    metadata = read_batch_metadata("mixed-batch")
    assert list(metadata["files"].keys()) == ["README.md"]


def test_removed_selected_batch_binary_does_not_fall_back_to_whole_batch(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """After resetting a selected binary, bare include --from should not include remaining files."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    readme_path = binary_file_repo / "README.md"
    image_path.write_bytes(b"\x00BATCHED")
    readme_path.write_text("# Test Repository\ntext change\n")

    initialize_abort_state()
    command_include_to_batch("mixed-batch", file="image.png", quiet=True)
    command_include_to_batch("mixed-batch", file="README.md", quiet=True)
    subprocess.run(["git", "checkout", "HEAD", "--", "."], check=True, capture_output=True)
    capsys.readouterr()

    command_show_from_batch("mixed-batch", file="image.png")
    capsys.readouterr()
    command_reset_from_batch("mixed-batch")
    capsys.readouterr()

    with pytest.raises(CommandError, match="changed or removed from batch"):
        command_include_from_batch("mixed-batch")

    staged_files = run_git_command(["diff", "--cached", "--name-only"]).stdout.splitlines()
    assert staged_files == []


def test_show_from_batch_binary_selects_file_for_pathless_include_from(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Showing one batch binary should make bare include --from use that file."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    modified_content = b"\x89PNG\r\n\x1a\nBATCHED"
    image_path.write_bytes(modified_content)
    (binary_file_repo / "README.md").write_text("# Test Repository\ntext change\n")

    initialize_abort_state()
    command_include_to_batch("mixed-batch", file="image.png", quiet=True)
    command_include_to_batch("mixed-batch", file="README.md", quiet=True)
    subprocess.run(["git", "checkout", "HEAD", "--", "."], check=True, capture_output=True)
    capsys.readouterr()

    command_show_from_batch("mixed-batch", file="image.png")
    capsys.readouterr()

    command_include_from_batch("mixed-batch")

    staged_files = run_git_command(["diff", "--cached", "--name-only"]).stdout.splitlines()
    assert staged_files == ["image.png"]
    assert run_git_command(["show", ":image.png"], text_output=False).stdout == modified_content


def test_selected_batch_binary_does_not_narrow_other_batch_pathless_include_from(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A batch-binary selection should only narrow actions for its source batch."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    other_path = binary_file_repo / "other.bin"
    first_content = b"\x89PNG\r\n\x1a\nFIRST"
    second_content = b"\x89PNG\r\n\x1a\nSECOND"
    other_content = b"\x00OTHER SECOND"

    other_path.write_bytes(b"\x00OTHER BASE")
    subprocess.run(["git", "add", "other.bin"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add other binary"], check=True, capture_output=True)

    initialize_abort_state()
    image_path.write_bytes(first_content)
    command_include_to_batch("first", file="image.png", quiet=True)
    image_path.write_bytes(second_content)
    other_path.write_bytes(other_content)
    command_include_to_batch("second", file="image.png", quiet=True)
    command_include_to_batch("second", file="other.bin", quiet=True)
    subprocess.run(["git", "checkout", "HEAD", "--", "."], check=True, capture_output=True)
    capsys.readouterr()

    command_show_from_batch("first", file="image.png")
    capsys.readouterr()

    command_include_from_batch("second")

    staged_files = set(run_git_command(["diff", "--cached", "--name-only"]).stdout.splitlines())
    assert staged_files == {"image.png", "other.bin"}
    assert run_git_command(["show", ":image.png"], text_output=False).stdout == second_content
    assert run_git_command(["show", ":other.bin"], text_output=False).stdout == other_content


def test_show_from_batch_binary_preview_preserves_existing_selection(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-selectable batch binary previews should not replace the selected file."""
    monkeypatch.chdir(binary_file_repo)

    (binary_file_repo / "image.png").write_bytes(b"\x89PNG\r\n\x1a\nBATCHED")

    initialize_abort_state()
    command_include_to_batch("bin-batch", file="image.png", quiet=True)
    (binary_file_repo / "README.md").write_text("# Test Repository\ntext change\n")

    command_show(file="README.md", porcelain=True)
    assert read_selected_change_kind() == SelectedChangeKind.FILE
    assert get_selected_change_file_path() == "README.md"

    command_show_from_batch("bin-batch", file="image.png", selectable=False)
    capsys.readouterr()

    assert read_selected_change_kind() == SelectedChangeKind.FILE
    assert get_selected_change_file_path() == "README.md"


def test_show_from_batch_displays_binary_entries(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """show --from should display binary files directly and in matched-file lists."""
    monkeypatch.chdir(binary_file_repo)

    (binary_file_repo / "image.png").write_bytes(b"\x89PNG\r\n\x1a\nBATCHED")
    (binary_file_repo / "README.md").write_text("# Test Repository\ntext change\n")

    initialize_abort_state()
    command_include_to_batch("mixed-batch", file="image.png", quiet=True)
    command_include_to_batch("mixed-batch", file="README.md", quiet=True)
    capsys.readouterr()

    command_show_from_batch("mixed-batch", file="image.png")
    captured = capsys.readouterr()
    assert "image.png :: Binary file modified" in captured.out

    with pytest.raises(CommandError) as exc_info:
        command_show_from_batch("mixed-batch", file="image.png", line_ids="1")
    assert "binary change summary" in exc_info.value.message

    command_show_from_batch("mixed-batch")
    captured = capsys.readouterr()
    assert "image.png" in captured.out
    assert "binary modified" in captured.out
    assert "README.md" in captured.out


def test_binary_file_modified_discard_file(binary_file_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """File-scoped discard should remove binary files atomically like text files."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nMODIFIED")

    initialize_abort_state()

    command_discard_file("image.png")

    assert not image_path.exists()
    status_result = run_git_command(["status", "--porcelain"])
    assert "D  image.png" in status_result.stdout

def test_binary_file_modified_include_file(binary_file_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """File-scoped include should stage binary files atomically."""
    monkeypatch.chdir(binary_file_repo)

    (binary_file_repo / "image.png").write_bytes(b"\x89PNG\r\n\x1a\nMODIFIED")

    initialize_abort_state()

    staged = command_include_file("image.png", quiet=True)

    assert staged == 1
    status_result = run_git_command(["status", "--porcelain"])
    assert "M  image.png" in status_result.stdout


def test_binary_file_modified_include_to_batch_file(binary_file_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """File-scoped include --to should save binary files atomically."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    modified_content = b"\x89PNG\r\n\x1a\nBATCHED"
    image_path.write_bytes(modified_content)

    initialize_abort_state()

    command_include_to_batch("bin-batch", file="image.png", quiet=True)

    metadata = read_batch_metadata("bin-batch")
    assert metadata["files"]["image.png"]["file_type"] == "binary"
    assert metadata["files"]["image.png"]["change_type"] == "modified"
    assert image_path.read_bytes() == modified_content

    subprocess.run(["git", "checkout", "HEAD", "--", "image.png"], check=True, capture_output=True)
    command_apply_from_batch("bin-batch", file="image.png")
    assert image_path.read_bytes() == modified_content


def test_binary_include_to_batch_captures_current_bytes_after_session_start(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binary include --to should not save stale session-start bytes."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    modified_content = b"\x89PNG\r\n\x1a\nCHANGED AFTER START"

    initialize_abort_state()
    image_path.write_bytes(modified_content)

    command_include_to_batch("bin-batch", file="image.png", quiet=True)
    subprocess.run(["git", "checkout", "HEAD", "--", "image.png"], check=True, capture_output=True)

    command_apply_from_batch("bin-batch", file="image.png")

    assert image_path.read_bytes() == modified_content


def test_binary_include_to_batch_captures_worktree_executable_mode(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binary include --to should save the executable bit from the worktree."""
    monkeypatch.chdir(binary_file_repo)

    tool_path = binary_file_repo / "tool.bin"
    tool_path.write_bytes(b"\x00BASE")
    tool_path.chmod(0o644)
    subprocess.run(["git", "add", "tool.bin"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add non-executable binary"], check=True, capture_output=True)

    initialize_abort_state()
    tool_path.write_bytes(b"\x00BATCHED")
    tool_path.chmod(0o755)

    command_include_to_batch("bin-batch", file="tool.bin", quiet=True)

    metadata = read_batch_metadata("bin-batch")
    assert metadata["files"]["tool.bin"]["mode"] == "100755"

    subprocess.run(["git", "checkout", "HEAD", "--", "tool.bin"], check=True, capture_output=True)
    command_apply_from_batch("bin-batch", file="tool.bin")

    assert tool_path.read_bytes() == b"\x00BATCHED"
    assert stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR


def test_binary_file_modified_discard_to_batch_file(binary_file_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """File-scoped discard --to should save binary files before restoring them."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    original_content = image_path.read_bytes()
    modified_content = b"\x89PNG\r\n\x1a\nBATCHED"
    image_path.write_bytes(modified_content)

    initialize_abort_state()

    command_discard_to_batch("bin-batch", file="image.png", quiet=True)

    metadata = read_batch_metadata("bin-batch")
    assert metadata["files"]["image.png"]["file_type"] == "binary"
    assert metadata["files"]["image.png"]["change_type"] == "modified"
    assert image_path.read_bytes() == original_content

    command_apply_from_batch("bin-batch", file="image.png")
    assert image_path.read_bytes() == modified_content


def test_binary_discard_to_batch_captures_current_bytes_after_session_start(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binary discard --to should preserve bytes changed after session start."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    original_content = image_path.read_bytes()
    modified_content = b"\x89PNG\r\n\x1a\nDISCARDED AFTER START"

    initialize_abort_state()
    image_path.write_bytes(modified_content)

    command_discard_to_batch("bin-batch", file="image.png", quiet=True)

    assert image_path.read_bytes() == original_content

    command_apply_from_batch("bin-batch", file="image.png")
    assert image_path.read_bytes() == modified_content


def test_binary_discard_to_batch_captures_worktree_executable_mode(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binary discard --to should save the executable bit before restoring HEAD."""
    monkeypatch.chdir(binary_file_repo)

    tool_path = binary_file_repo / "tool.bin"
    tool_path.write_bytes(b"\x00BASE")
    tool_path.chmod(0o644)
    subprocess.run(["git", "add", "tool.bin"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add non-executable binary"], check=True, capture_output=True)

    initialize_abort_state()
    tool_path.write_bytes(b"\x00BATCHED")
    tool_path.chmod(0o755)

    command_discard_to_batch("bin-batch", file="tool.bin", quiet=True)

    metadata = read_batch_metadata("bin-batch")
    assert metadata["files"]["tool.bin"]["mode"] == "100755"
    assert tool_path.read_bytes() == b"\x00BASE"
    assert not (stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR)

    command_apply_from_batch("bin-batch", file="tool.bin")

    assert tool_path.read_bytes() == b"\x00BATCHED"
    assert stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR


def test_selected_binary_discard_to_batch(binary_file_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Selected binary discard --to should save then restore the binary atomically."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    original_content = image_path.read_bytes()
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nBATCHED")

    initialize_abort_state()
    result = fetch_next_change()
    assert isinstance(result, BinaryFileChange)

    command_discard_to_batch("bin-batch", quiet=True)

    metadata = read_batch_metadata("bin-batch")
    assert metadata["files"]["image.png"]["file_type"] == "binary"
    assert image_path.read_bytes() == original_content


def test_pathless_discard_to_batch_uses_selected_binary_not_first_text_change(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pathless discard --to should honor an explicitly selected binary file."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    original_content = image_path.read_bytes()
    (binary_file_repo / "README.md").write_text("# Test Repository\ntext change\n")
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nBATCHED")

    initialize_abort_state()
    command_show(file="image.png", porcelain=True)

    command_discard_to_batch("bin-batch", quiet=True)

    metadata = read_batch_metadata("bin-batch")
    assert list(metadata["files"].keys()) == ["image.png"]
    assert metadata["files"]["image.png"]["file_type"] == "binary"
    assert image_path.read_bytes() == original_content
    assert "text change" in (binary_file_repo / "README.md").read_text()


def test_binary_include_from_batch_stages_binary_file(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """include --from should stage and write binary batch entries atomically."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    modified_content = b"\x89PNG\r\n\x1a\nBATCHED"
    image_path.write_bytes(modified_content)

    initialize_abort_state()
    command_include_to_batch("bin-batch", file="image.png", quiet=True)
    subprocess.run(["git", "checkout", "HEAD", "--", "image.png"], check=True, capture_output=True)

    command_include_from_batch("bin-batch", file="image.png")

    status_result = run_git_command(["status", "--porcelain"])
    assert any(line.startswith("M") and line.endswith(" image.png") for line in status_result.stdout.splitlines())
    assert run_git_command(["show", ":image.png"], text_output=False).stdout == modified_content
    assert image_path.read_bytes() == modified_content


def test_binary_include_from_batch_stages_binary_deletion(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """include --from should stage and write binary deletions from batch entries."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    image_path.unlink()

    initialize_abort_state()
    command_include_to_batch("bin-batch", file="image.png", quiet=True)
    subprocess.run(["git", "checkout", "HEAD", "--", "image.png"], check=True, capture_output=True)

    command_include_from_batch("bin-batch", file="image.png")

    status_result = run_git_command(["status", "--porcelain"])
    assert any(line.startswith("D") and line.endswith(" image.png") for line in status_result.stdout.splitlines())
    assert run_git_command(["cat-file", "-e", ":image.png"], check=False).returncode != 0
    assert not image_path.exists()


def test_binary_include_from_batch_reports_failed_binary_deletion_staging(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """include --from should not report success when staging a binary deletion fails."""
    monkeypatch.chdir(binary_file_repo)

    image_path = binary_file_repo / "image.png"
    image_path.unlink()

    initialize_abort_state()
    command_include_to_batch("bin-batch", file="image.png", quiet=True)
    subprocess.run(["git", "checkout", "HEAD", "--", "image.png"], check=True, capture_output=True)

    original_git_update_index = include_from_module.git_update_index

    def fail_force_remove(*args, **kwargs):
        if kwargs.get("force_remove"):
            return subprocess.CompletedProcess(["git", "update-index"], 1, stdout="", stderr="boom")
        return original_git_update_index(*args, **kwargs)

    monkeypatch.setattr(include_from_module, "git_update_index", fail_force_remove)

    with pytest.raises(CommandError, match="incompatible"):
        command_include_from_batch("bin-batch", file="image.png")

    assert run_git_command(["cat-file", "-e", ":image.png"], check=False).returncode == 0
    assert image_path.exists()


def test_stale_selected_binary_include_refuses_without_fetching_next_change(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale selected binary must not make bare include act on a different file."""
    monkeypatch.chdir(binary_file_repo)

    (binary_file_repo / "README.md").write_text("# Test Repository\ntext change\n")
    (binary_file_repo / "image.png").write_bytes(b"\x89PNG\r\n\x1a\nBATCHED")

    initialize_abort_state()
    command_show(file="image.png", porcelain=True)
    subprocess.run(["git", "restore", "image.png"], check=True, capture_output=True)

    with pytest.raises(CommandError, match="Selected binary file no longer matches"):
        command_include(quiet=True)
    with pytest.raises(CommandError, match="Selected binary file no longer matches"):
        command_include(quiet=True)

    staged_files = run_git_command(["diff", "--cached", "--name-only"]).stdout.splitlines()
    assert staged_files == []
    assert (binary_file_repo / "README.md").read_text() == "# Test Repository\ntext change\n"


def test_status_does_not_clear_stale_selected_binary(
    binary_file_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Status should not turn a stale selected binary into a different bare action."""
    monkeypatch.chdir(binary_file_repo)

    readme_path = binary_file_repo / "README.md"
    image_path = binary_file_repo / "image.png"
    readme_path.write_text("# Test Repository\ntext change\n")
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nBATCHED")

    initialize_abort_state()
    command_show(file="image.png", porcelain=True)
    subprocess.run(["git", "restore", "image.png"], check=True, capture_output=True)

    command_status(porcelain=True)
    capsys.readouterr()

    with pytest.raises(CommandError, match="Selected binary file no longer matches"):
        command_include(quiet=True)

    staged_files = run_git_command(["diff", "--cached", "--name-only"]).stdout.splitlines()
    assert staged_files == []
    assert readme_path.read_text() == "# Test Repository\ntext change\n"

def test_binary_file_modified_skip_file(binary_file_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """File-scoped skip should mark binary files processed without staging them."""
    monkeypatch.chdir(binary_file_repo)

    (binary_file_repo / "image.png").write_bytes(b"\x89PNG\r\n\x1a\nMODIFIED")

    initialize_abort_state()

    skipped = command_skip_file("image.png", quiet=True)

    assert skipped == 1
    status_result = run_git_command(["status", "--porcelain"])
    assert " M image.png" in status_result.stdout

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
