"""Tests for batch-transform sift result computation."""

from __future__ import annotations

import pytest

import git_stage_batch.commands.batch_transform.sift_results as sift_results
from git_stage_batch.core.buffer import LineBuffer


def test_compute_sifted_binary_file_removes_matching_content(
    monkeypatch,
    tmp_path,
):
    """Binary sift should drop content already present in the working tree."""
    batch_source_buffer = LineBuffer.from_bytes(b"target")
    (tmp_path / "data.bin").write_bytes(b"target")
    monkeypatch.setattr(
        sift_results,
        "load_git_object_as_buffer_or_empty",
        lambda spec: batch_source_buffer,
    )

    result = sift_results.compute_sifted_binary_file(
        "data.bin",
        {
            "batch_source_commit": "commit",
            "change_type": "modified",
        },
        tmp_path,
    )

    assert result is None
    with pytest.raises(ValueError, match="buffer is closed"):
        batch_source_buffer.to_bytes()


def test_compute_sifted_binary_file_retains_changed_content(
    monkeypatch,
    tmp_path,
):
    """Binary sift should retain target content that differs from working tree."""
    batch_source_buffer = LineBuffer.from_bytes(b"target")
    (tmp_path / "data.bin").write_bytes(b"working")
    monkeypatch.setattr(
        sift_results,
        "load_git_object_as_buffer_or_empty",
        lambda spec: batch_source_buffer,
    )

    result = sift_results.compute_sifted_binary_file(
        "data.bin",
        {
            "batch_source_commit": "commit",
            "change_type": "modified",
        },
        tmp_path,
    )

    assert result is not None
    assert isinstance(result, sift_results.SiftedBinaryFileResult)
    assert result.binary_change.old_path == "data.bin"
    assert result.binary_change.new_path == "data.bin"
    assert result.binary_change.change_type == "modified"
    assert result.target_buffer is batch_source_buffer
    assert result.target_buffer.to_bytes() == b"target"

    result.target_buffer.close()


def test_compute_sifted_binary_file_removes_absent_deletion(
    monkeypatch,
    tmp_path,
):
    """Binary deletion sift should drop deletions already present at tip."""
    batch_source_buffer = LineBuffer.from_bytes(b"")
    monkeypatch.setattr(
        sift_results,
        "load_git_object_as_buffer_or_empty",
        lambda spec: batch_source_buffer,
    )

    result = sift_results.compute_sifted_binary_file(
        "data.bin",
        {
            "batch_source_commit": "commit",
            "change_type": "deleted",
        },
        tmp_path,
    )

    assert result is None
    with pytest.raises(ValueError, match="buffer is closed"):
        batch_source_buffer.to_bytes()


def test_compute_sifted_binary_file_retains_existing_deletion(
    monkeypatch,
    tmp_path,
):
    """Binary deletion sift should retain deletion when the path still exists."""
    batch_source_buffer = LineBuffer.from_bytes(b"")
    (tmp_path / "data.bin").write_bytes(b"working")
    monkeypatch.setattr(
        sift_results,
        "load_git_object_as_buffer_or_empty",
        lambda spec: batch_source_buffer,
    )

    result = sift_results.compute_sifted_binary_file(
        "data.bin",
        {
            "batch_source_commit": "commit",
            "change_type": "deleted",
        },
        tmp_path,
    )

    assert result is not None
    assert isinstance(result, sift_results.SiftedBinaryFileResult)
    assert result.binary_change.old_path == "data.bin"
    assert result.binary_change.new_path == "/dev/null"
    assert result.binary_change.change_type == "deleted"
    assert result.target_buffer is None
    with pytest.raises(ValueError, match="buffer is closed"):
        batch_source_buffer.to_bytes()
