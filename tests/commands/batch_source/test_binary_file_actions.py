"""Tests for batch-source binary file actions."""

import stat
from types import SimpleNamespace

import pytest

import git_stage_batch.commands.batch_source.binary_file_actions as binary_file_actions
from git_stage_batch.core.buffer import LineBuffer


def test_write_binary_file_to_worktree_writes_buffer(tmp_path, monkeypatch):
    """Modified binary batch targets should be written relative to the repository root."""
    monkeypatch.setattr(
        binary_file_actions,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    buffer = LineBuffer.from_bytes(b"\x89PNG\r\n\x1a\nBINARY")

    try:
        action = binary_file_actions.write_binary_file_to_worktree(
            "image.png",
            {"change_type": "modified", "mode": "100644"},
            buffer,
        )
    finally:
        buffer.close()

    assert action is binary_file_actions.BinaryWorktreeAction.REPLACED
    assert (tmp_path / "image.png").read_bytes() == b"\x89PNG\r\n\x1a\nBINARY"


def test_write_binary_file_to_worktree_restores_mode(tmp_path, monkeypatch):
    """Binary batch writes should apply the requested file mode."""
    monkeypatch.setattr(
        binary_file_actions,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    buffer = LineBuffer.from_bytes(b"binary\n")

    try:
        action = binary_file_actions.write_binary_file_to_worktree(
            "tool.bin",
            {"change_type": "added", "mode": "100755"},
            buffer,
        )
    finally:
        buffer.close()

    assert action is binary_file_actions.BinaryWorktreeAction.ADDED
    assert stat.S_IMODE((tmp_path / "tool.bin").stat().st_mode) & stat.S_IXUSR


def test_write_binary_file_to_worktree_deletes_existing_file(tmp_path, monkeypatch):
    """Deleted binary batch targets should remove existing working-tree paths."""
    monkeypatch.setattr(
        binary_file_actions,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    target = tmp_path / "old.bin"
    target.write_bytes(b"old")

    action = binary_file_actions.write_binary_file_to_worktree(
        "old.bin",
        {"change_type": "deleted"},
        None,
    )

    assert action is binary_file_actions.BinaryWorktreeAction.DELETED
    assert not target.exists()


def test_write_binary_file_to_worktree_ignores_missing_deleted_file(
    tmp_path,
    monkeypatch,
):
    """Deleted binary batch targets should allow absent working-tree paths."""
    monkeypatch.setattr(
        binary_file_actions,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )

    action = binary_file_actions.write_binary_file_to_worktree(
        "old.bin",
        {"change_type": "deleted"},
        None,
    )

    assert action is None
    assert not (tmp_path / "old.bin").exists()


def test_write_binary_file_to_worktree_requires_buffer(tmp_path, monkeypatch):
    """Non-deleted binary batch targets should require batch content."""
    monkeypatch.setattr(
        binary_file_actions,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )

    with pytest.raises(RuntimeError) as exc_info:
        binary_file_actions.write_binary_file_to_worktree(
            "image.png",
            {"change_type": "modified"},
            None,
        )

    assert "Binary file not found in batch commit: image.png" in str(exc_info.value)


def test_write_binary_file_to_worktree_allows_missing_content_message(
    tmp_path,
    monkeypatch,
):
    """Binary batch writes should allow command-specific missing content text."""
    monkeypatch.setattr(
        binary_file_actions,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )

    with pytest.raises(RuntimeError) as exc_info:
        binary_file_actions.write_binary_file_to_worktree(
            "image.png",
            {"change_type": "added"},
            None,
            missing_content_message="custom missing content",
        )

    assert "custom missing content" in str(exc_info.value)


def test_stage_binary_file_to_index_deletes_target(monkeypatch):
    """Deleted binary batch targets should remove index entries."""
    calls = []

    def fake_git_update_index(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(binary_file_actions, "git_update_index", fake_git_update_index)

    binary_file_actions.stage_binary_file_to_index(
        "image.png",
        {"change_type": "deleted"},
        None,
    )

    assert calls == [
        {
            "file_path": "image.png",
            "force_remove": True,
            "check": False,
        },
    ]


def test_stage_binary_file_to_index_reports_delete_failure(monkeypatch):
    """Failed binary deletion staging should include the git stderr."""

    def fake_git_update_index(**_kwargs):
        return SimpleNamespace(returncode=1, stderr="boom")

    monkeypatch.setattr(binary_file_actions, "git_update_index", fake_git_update_index)

    with pytest.raises(RuntimeError) as exc_info:
        binary_file_actions.stage_binary_file_to_index(
            "image.png",
            {"change_type": "deleted"},
            None,
        )

    assert "Failed to stage binary deletion for image.png: boom" in str(
        exc_info.value
    )


def test_stage_binary_file_to_index_requires_buffer():
    """Non-deleted binary batch targets should require batch content."""
    with pytest.raises(RuntimeError) as exc_info:
        binary_file_actions.stage_binary_file_to_index(
            "image.png",
            {"change_type": "modified"},
            None,
        )

    assert "Binary file not found in batch commit: image.png" in str(exc_info.value)


def test_stage_binary_file_to_index_uses_metadata_mode(monkeypatch):
    """Binary staging should create a blob using the requested file mode."""
    blob_chunks = []
    update_calls = []
    buffer = LineBuffer.from_bytes(b"\x89PNG\r\n\x1a\nBINARY")

    def fake_create_git_blob(chunks):
        blob_chunks.extend(chunks)
        return "abc123"

    def fake_git_update_index(**kwargs):
        update_calls.append(kwargs)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(binary_file_actions, "create_git_blob", fake_create_git_blob)
    monkeypatch.setattr(binary_file_actions, "git_update_index", fake_git_update_index)

    try:
        binary_file_actions.stage_binary_file_to_index(
            "image.png",
            {"change_type": "modified", "mode": "100755"},
            buffer,
        )
    finally:
        buffer.close()

    assert blob_chunks == [b"\x89PNG\r\n\x1a\nBINARY"]
    assert update_calls == [
        {
            "file_path": "image.png",
            "mode": "100755",
            "blob_sha": "abc123",
        },
    ]


def test_stage_binary_file_to_index_defaults_mode(monkeypatch):
    """Binary staging should default missing metadata mode to regular file."""
    update_calls = []
    buffer = LineBuffer.from_bytes(b"data")

    monkeypatch.setattr(binary_file_actions, "create_git_blob", lambda _chunks: "abc123")

    def fake_git_update_index(**kwargs):
        update_calls.append(kwargs)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(binary_file_actions, "git_update_index", fake_git_update_index)

    try:
        binary_file_actions.stage_binary_file_to_index(
            "image.png",
            {"change_type": "modified"},
            buffer,
        )
    finally:
        buffer.close()

    assert update_calls == [
        {
            "file_path": "image.png",
            "mode": "100644",
            "blob_sha": "abc123",
        },
    ]
