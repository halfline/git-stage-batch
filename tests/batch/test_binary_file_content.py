"""Tests for stored binary batch content loading."""

import pytest

import git_stage_batch.batch.binary_file_content as binary_file_content
from git_stage_batch.core.buffer import LineBuffer


def test_read_binary_file_from_batch_returns_none_for_deletion(monkeypatch):
    """Stored binary deletions should not read a batch blob."""
    monkeypatch.setattr(
        binary_file_content,
        "get_batch_commit_sha",
        lambda batch_name: "batch-commit",
    )

    def fail_load(_spec):
        raise AssertionError("deleted binary entries should not load content")

    monkeypatch.setattr(binary_file_content, "read_git_object_buffer_or_none", fail_load)

    assert binary_file_content.read_binary_file_from_batch(
        "feature",
        "asset.bin",
        {"change_type": "deleted"},
    ) is None


def test_read_binary_file_from_batch_requires_batch_commit(monkeypatch):
    """Missing batch commits should report the batch name."""
    monkeypatch.setattr(
        binary_file_content,
        "get_batch_commit_sha",
        lambda batch_name: None,
    )

    with pytest.raises(RuntimeError) as exc_info:
        binary_file_content.read_binary_file_from_batch(
            "feature",
            "asset.bin",
            {"change_type": "modified"},
        )

    assert "Batch commit not found for batch 'feature'" in str(exc_info.value)


def test_read_binary_file_from_batch_reports_missing_content(monkeypatch):
    """Missing binary blobs should report the stored change metadata."""
    monkeypatch.setattr(
        binary_file_content,
        "get_batch_commit_sha",
        lambda batch_name: "batch-commit",
    )
    monkeypatch.setattr(
        binary_file_content,
        "read_git_object_buffer_or_none",
        lambda spec: None,
    )

    with pytest.raises(RuntimeError) as exc_info:
        binary_file_content.read_binary_file_from_batch(
            "feature",
            "asset.bin",
            {"change_type": "added"},
        )

    assert (
        "Binary file metadata for asset.bin says added, but the batch content is missing"
        in str(exc_info.value)
    )


def test_read_binary_file_from_batch_accepts_missing_content_message(monkeypatch):
    """Callers should be able to keep operation-specific missing-content text."""
    monkeypatch.setattr(
        binary_file_content,
        "get_batch_commit_sha",
        lambda batch_name: "batch-commit",
    )
    monkeypatch.setattr(
        binary_file_content,
        "read_git_object_buffer_or_none",
        lambda spec: None,
    )

    with pytest.raises(RuntimeError) as exc_info:
        binary_file_content.read_binary_file_from_batch(
            "feature",
            "asset.bin",
            {"change_type": "modified"},
            missing_content_message="binary payload missing",
        )

    assert str(exc_info.value) == "binary payload missing"


def test_read_binary_file_from_batch_loads_batch_blob(monkeypatch):
    """Non-deleted binary entries should load the blob from the batch commit."""
    loaded_specs = []
    buffer = LineBuffer.from_bytes(b"\0data")

    monkeypatch.setattr(
        binary_file_content,
        "get_batch_commit_sha",
        lambda batch_name: "batch-commit",
    )

    def load(spec):
        loaded_specs.append(spec)
        return buffer

    monkeypatch.setattr(binary_file_content, "read_git_object_buffer_or_none", load)

    try:
        assert binary_file_content.read_binary_file_from_batch(
            "feature",
            "asset.bin",
            {"change_type": "modified"},
        ) is buffer
        assert loaded_specs == ["batch-commit:asset.bin"]
    finally:
        buffer.close()
