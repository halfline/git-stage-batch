"""Tests for batch-source text file actions."""

import stat
from types import SimpleNamespace

import pytest

import git_stage_batch.commands.batch_source.text_file_actions as text_file_actions
from git_stage_batch.core.buffer import LineBuffer


def test_stage_text_file_to_index_deletes_target(monkeypatch):
    """Deleted text batch targets should remove index entries."""
    calls = []

    def fake_git_update_index(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(text_file_actions, "git_update_index", fake_git_update_index)

    text_file_actions.stage_text_file_to_index(
        "old.txt",
        None,
        None,
        change_type="deleted",
    )

    assert calls == [
        {
            "file_path": "old.txt",
            "force_remove": True,
            "check": False,
        },
    ]


def test_stage_text_file_to_index_reports_delete_failure(monkeypatch):
    """Failed text deletion staging should include the git stderr."""

    def fake_git_update_index(**_kwargs):
        return SimpleNamespace(returncode=1, stderr="fatal: no entry")

    monkeypatch.setattr(text_file_actions, "git_update_index", fake_git_update_index)

    with pytest.raises(RuntimeError) as exc_info:
        text_file_actions.stage_text_file_to_index(
            "old.txt",
            None,
            None,
            change_type="deleted",
        )

    assert "Failed to stage text deletion for old.txt: fatal: no entry" in str(
        exc_info.value
    )


def test_stage_text_file_to_index_requires_buffer():
    """Non-deleted text batch targets should require materialized content."""
    with pytest.raises(RuntimeError) as exc_info:
        text_file_actions.stage_text_file_to_index(
            "notes.txt",
            None,
            "100644",
        )

    assert "Text file not found in batch content: notes.txt" in str(exc_info.value)


def test_stage_text_file_to_index_uses_blob_buffer_without_mode(monkeypatch):
    """Default-mode text staging should use the index buffer helper."""
    calls = []
    buffer = LineBuffer.from_bytes(b"batched\ncontent\n")

    def fake_update_index_with_blob_buffer(path, staged_buffer):
        calls.append((path, staged_buffer))

    monkeypatch.setattr(
        text_file_actions,
        "update_index_with_blob_buffer",
        fake_update_index_with_blob_buffer,
    )

    try:
        text_file_actions.stage_text_file_to_index(
            "notes.txt",
            buffer,
            None,
        )
    finally:
        buffer.close()

    assert len(calls) == 1
    assert calls[0][0] == "notes.txt"
    assert calls[0][1] is buffer


def test_stage_text_file_to_index_uses_explicit_mode_blob(monkeypatch):
    """Explicit-mode text staging should create a blob before index update."""
    blob_chunks = []
    update_calls = []
    buffer = LineBuffer.from_bytes(b"tool\n")

    def fake_create_git_blob(chunks):
        blob_chunks.extend(chunks)
        return "abc123"

    def fake_git_update_index(**kwargs):
        update_calls.append(kwargs)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(text_file_actions, "create_git_blob", fake_create_git_blob)
    monkeypatch.setattr(text_file_actions, "git_update_index", fake_git_update_index)

    try:
        text_file_actions.stage_text_file_to_index(
            "tool.sh",
            buffer,
            "100755",
        )
    finally:
        buffer.close()

    assert blob_chunks == [b"tool\n"]
    assert update_calls == [
        {
            "file_path": "tool.sh",
            "mode": "100755",
            "blob_sha": "abc123",
        },
    ]


def test_write_text_file_to_worktree_writes_buffer(tmp_path, monkeypatch):
    """Text batch content should be written relative to the repository root."""
    monkeypatch.setattr(
        text_file_actions,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    buffer = LineBuffer.from_bytes(b"batched\ncontent\n")

    try:
        text_file_actions.write_text_file_to_worktree(
            "notes.txt",
            buffer,
            "100644",
        )
    finally:
        buffer.close()

    assert (tmp_path / "notes.txt").read_bytes() == b"batched\ncontent\n"


def test_write_text_file_to_worktree_restores_mode(tmp_path, monkeypatch):
    """Text batch writes should apply the requested file mode."""
    monkeypatch.setattr(
        text_file_actions,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    buffer = LineBuffer.from_bytes(b"tool\n")

    try:
        text_file_actions.write_text_file_to_worktree(
            "tool.sh",
            buffer,
            "100755",
        )
    finally:
        buffer.close()

    assert stat.S_IMODE((tmp_path / "tool.sh").stat().st_mode) & stat.S_IXUSR


def test_write_text_file_to_worktree_deletes_existing_file(tmp_path, monkeypatch):
    """Deleted text batch targets should remove existing working-tree paths."""
    monkeypatch.setattr(
        text_file_actions,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    target = tmp_path / "old.txt"
    target.write_text("old\n")

    text_file_actions.write_text_file_to_worktree(
        "old.txt",
        None,
        None,
        change_type="deleted",
    )

    assert not target.exists()


def test_write_text_file_to_worktree_ignores_missing_deleted_file(
    tmp_path,
    monkeypatch,
):
    """Deleted text batch targets should allow absent working-tree paths."""
    monkeypatch.setattr(
        text_file_actions,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )

    text_file_actions.write_text_file_to_worktree(
        "old.txt",
        None,
        None,
        change_type="deleted",
    )

    assert not (tmp_path / "old.txt").exists()


def test_write_text_file_to_worktree_requires_buffer(tmp_path, monkeypatch):
    """Non-deleted text batch targets should require materialized content."""
    monkeypatch.setattr(
        text_file_actions,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )

    with pytest.raises(RuntimeError) as exc_info:
        text_file_actions.write_text_file_to_worktree(
            "notes.txt",
            None,
            "100644",
        )

    assert "Text file not found in batch content: notes.txt" in str(exc_info.value)
