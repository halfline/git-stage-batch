"""Tests for batch-source text file actions."""

import stat

import pytest

import git_stage_batch.commands.batch_source.text_file_actions as text_file_actions
from git_stage_batch.core.buffer import LineBuffer


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
