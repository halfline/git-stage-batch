"""Tests for batch-transform sift result computation."""

from __future__ import annotations

import pytest

import git_stage_batch.commands.batch_transform.sift_results as sift_results
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
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
        "read_git_object_buffer_or_empty",
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
        "read_git_object_buffer_or_empty",
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


def test_compute_sifted_binary_file_uses_captured_worktree(
    monkeypatch,
    tmp_path,
):
    """Parent-local binary sift should compare its stable captured artifact."""
    batch_source_buffer = LineBuffer.from_bytes(b"target")
    (tmp_path / "data.bin").write_bytes(b"changed live content")
    artifact = tmp_path / "captured.bin"
    artifact.write_bytes(b"target")
    monkeypatch.setattr(
        sift_results,
        "read_git_object_buffer_or_empty",
        lambda spec: batch_source_buffer,
    )

    result = sift_results.compute_sifted_binary_file(
        "data.bin",
        {
            "batch_source_commit": "commit",
            "change_type": "modified",
        },
        tmp_path,
        working_tree_artifact_path=artifact,
        captured_working_tree_exists=True,
    )

    assert result is None


def test_compute_sifted_binary_file_removes_absent_deletion(
    monkeypatch,
    tmp_path,
):
    """Binary deletion sift should drop deletions already present at tip."""
    batch_source_buffer = LineBuffer.from_bytes(b"")
    monkeypatch.setattr(
        sift_results,
        "read_git_object_buffer_or_empty",
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
        "read_git_object_buffer_or_empty",
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


def test_sifted_text_result_closes_all_owned_buffers_once(monkeypatch):
    """Text results own their target and unique deletion content buffers."""
    target = LineBuffer.from_bytes(b"target\n")
    deletion = LineBuffer.from_bytes(b"old\n")
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [
            AbsenceClaim(anchor_line=None, content_lines=deletion),
            AbsenceClaim(anchor_line=1, content_lines=deletion),
        ],
    )
    result = sift_results.SiftedTextFileResult(
        ownership=ownership,
        target_buffer=target,
        change_type="modified",
    )
    close_counts: dict[int, int] = {}
    original_close = LineBuffer.close

    def count_close(buffer):
        close_counts[id(buffer)] = close_counts.get(id(buffer), 0) + 1
        original_close(buffer)

    monkeypatch.setattr(LineBuffer, "close", count_close)

    result.close()
    result.close()

    assert close_counts == {id(target): 1, id(deletion): 1}


def test_ownership_derivation_closes_deletions_on_late_failure(monkeypatch):
    """A failure after deletion construction must release its mapped content."""
    deletion = LineBuffer.from_bytes(b"old\n")

    class FakeBuilder:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def append_line_range(self, lines, start, end):
            return None

        def finish(self):
            return deletion

    def fail_ranges(ranges):
        raise RuntimeError("late range failure")

    monkeypatch.setattr(
        sift_results,
        "AbsenceContentBuilder",
        lambda **kwargs: FakeBuilder(),
    )
    monkeypatch.setattr(
        sift_results.LineRanges,
        "from_ranges",
        staticmethod(fail_ranges),
    )

    with pytest.raises(RuntimeError, match="late range failure"):
        sift_results.build_ownership_from_working_and_target_lines(
            [b"old\n"],
            [],
        )

    with pytest.raises(ValueError, match="buffer is closed"):
        deletion.to_bytes()
