"""Tests for batch-source candidate preview counts."""

from __future__ import annotations

from contextlib import AbstractContextManager

from git_stage_batch.batch.operation_candidates import (
    CandidateEnumerationLimitError,
    CandidatePreviewCount,
)
from git_stage_batch.core.buffer import LineBuffer
import git_stage_batch.commands.batch_source.candidate_preview_counts as counts


class _OwnershipContext(AbstractContextManager):
    def __init__(self, ownership: object) -> None:
        self.ownership = ownership

    def __enter__(self) -> object:
        return self.ownership

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _Preview:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _patch_apply_candidate_count_io(monkeypatch, tmp_path):
    ownership = object()
    batch_buffer = LineBuffer.from_bytes(b"batch\n")
    worktree_buffer = LineBuffer.from_bytes(b"worktree\n")
    (tmp_path / "notes.txt").write_bytes(b"worktree\n")

    monkeypatch.setattr(counts, "get_git_repository_root_path", lambda: tmp_path)
    monkeypatch.setattr(
        counts,
        "load_git_object_as_buffer",
        lambda spec: batch_buffer if spec == "commit:notes.txt" else None,
    )
    monkeypatch.setattr(
        counts,
        "load_working_tree_file_as_buffer",
        lambda file_path: worktree_buffer,
    )
    monkeypatch.setattr(
        counts,
        "acquire_batch_ownership_for_display_ids_from_lines",
        lambda file_meta, source_lines, selection_ids: _OwnershipContext(ownership),
    )
    return ownership


def test_count_apply_candidate_previews_for_file_counts_previews(
    monkeypatch,
    tmp_path,
):
    """Apply candidate counting should return closed preview counts."""
    ownership = _patch_apply_candidate_count_io(monkeypatch, tmp_path)
    previews = (_Preview(), _Preview())
    calls = {}

    def build_apply_candidate_previews(**kwargs):
        calls["build"] = kwargs
        return previews

    monkeypatch.setattr(
        counts,
        "build_apply_candidate_previews",
        build_apply_candidate_previews,
    )

    result = counts.count_apply_candidate_previews_for_file(
        batch_name="cleanup",
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "modified",
            "mode": "100644",
        },
        selection_ids_to_apply={7},
    )

    assert result == CandidatePreviewCount(count=2)
    assert calls["build"]["batch_name"] == "cleanup"
    assert calls["build"]["file_path"] == "notes.txt"
    assert calls["build"]["ownership"] is ownership
    assert calls["build"]["selected_ids"] == {7}
    assert calls["build"]["selection_ids"] == {7}
    assert calls["build"]["worktree_exists"]
    assert [preview.closed for preview in previews] == [True, True]


def test_count_apply_candidate_previews_for_file_skips_binary_entries(monkeypatch):
    """Apply candidate counting should ignore binary batch entries."""
    monkeypatch.setattr(
        counts,
        "load_git_object_as_buffer",
        lambda spec: (_ for _ in ()).throw(AssertionError("unexpected load")),
    )

    result = counts.count_apply_candidate_previews_for_file(
        batch_name="cleanup",
        file_path="notes.bin",
        file_meta={
            "file_type": "binary",
            "batch_source_commit": "commit",
            "change_type": "modified",
            "mode": "100644",
        },
        selection_ids_to_apply=None,
    )

    assert result == CandidatePreviewCount()


def test_count_apply_candidate_previews_for_file_reports_limit(
    monkeypatch,
    tmp_path,
):
    """Apply candidate counting should preserve enumeration limit messages."""
    _patch_apply_candidate_count_io(monkeypatch, tmp_path)

    def build_apply_candidate_previews(**kwargs):
        raise CandidateEnumerationLimitError("too many")

    monkeypatch.setattr(
        counts,
        "build_apply_candidate_previews",
        build_apply_candidate_previews,
    )

    result = counts.count_apply_candidate_previews_for_file(
        batch_name="cleanup",
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "modified",
            "mode": "100644",
        },
        selection_ids_to_apply={7},
    )

    assert result == CandidatePreviewCount(too_many=True, error="too many")
