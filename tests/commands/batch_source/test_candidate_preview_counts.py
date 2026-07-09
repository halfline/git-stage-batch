"""Tests for batch-source candidate preview counts."""

from __future__ import annotations

from contextlib import AbstractContextManager

try:
    from git_stage_batch.batch.operation_candidate_types import (
        CandidateEnumerationLimitError,
        CandidatePreviewCount,
    )
except ModuleNotFoundError:
    from git_stage_batch.batch.operation_candidates import (
        CandidateEnumerationLimitError,
        CandidatePreviewCount,
    )
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.replacement import ReplacementPayload
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


class _ReplacementView(AbstractContextManager):
    def __init__(self, source_buffer: LineBuffer, ownership: object) -> None:
        self.source_buffer = source_buffer
        self.ownership = ownership

    def __enter__(self) -> "_ReplacementView":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.source_buffer.close()
        return None


def _patch_apply_candidate_count_io(monkeypatch, tmp_path):
    ownership = object()
    batch_buffer = LineBuffer.from_bytes(b"batch\n")
    worktree_buffer = LineBuffer.from_bytes(b"worktree\n")
    (tmp_path / "notes.txt").write_bytes(b"worktree\n")

    monkeypatch.setattr(
        counts._candidate_inputs,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
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


def _patch_include_candidate_count_io(monkeypatch, tmp_path):
    ownership = object()
    batch_buffer = LineBuffer.from_bytes(b"batch\n")
    index_buffer = LineBuffer.from_bytes(b"index\n")
    worktree_buffer = LineBuffer.from_bytes(b"worktree\n")
    (tmp_path / "notes.txt").write_bytes(b"worktree\n")

    def load_git_object(spec: str):
        if spec == "commit:notes.txt":
            return batch_buffer
        if spec == ":notes.txt":
            return index_buffer
        return None

    monkeypatch.setattr(
        counts._candidate_inputs,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    monkeypatch.setattr(counts, "load_git_object_as_buffer", load_git_object)
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


def test_count_include_candidate_previews_for_file_counts_replacements(
    monkeypatch,
    tmp_path,
):
    """Include candidate counting should count replacement previews."""
    base_ownership = _patch_include_candidate_count_io(monkeypatch, tmp_path)
    replacement_ownership = object()
    payload = ReplacementPayload.from_text("new\n")
    previews = (_Preview(),)
    calls = {}

    def build_replacement_view(source_lines, ownership, replacement_payload):
        calls["replacement"] = (source_lines.to_bytes(), ownership, replacement_payload)
        return _ReplacementView(
            LineBuffer.from_bytes(b"replacement\n"),
            replacement_ownership,
        )

    def build_include_candidate_previews(**kwargs):
        calls["build"] = {
            **kwargs,
            "source_bytes": kwargs["source_lines"].to_bytes(),
        }
        return previews

    monkeypatch.setattr(
        counts,
        "build_replacement_batch_view_from_lines",
        build_replacement_view,
    )
    monkeypatch.setattr(
        counts,
        "build_include_candidate_previews",
        build_include_candidate_previews,
    )

    result = counts.count_include_candidate_previews_for_file(
        batch_name="cleanup",
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "modified",
            "mode": "100644",
        },
        selection_ids_to_include={9},
        replacement_payload=payload,
    )

    assert result == CandidatePreviewCount(count=1)
    assert calls["replacement"] == (b"batch\n", base_ownership, payload)
    assert calls["build"]["batch_name"] == "cleanup"
    assert calls["build"]["file_path"] == "notes.txt"
    assert calls["build"]["source_bytes"] == b"replacement\n"
    assert calls["build"]["ownership"] is replacement_ownership
    assert calls["build"]["selected_ids"] == {9}
    assert calls["build"]["selection_ids"] == {9}
    assert calls["build"]["replacement_payload"] is payload
    assert calls["build"]["index_exists"]
    assert calls["build"]["worktree_exists"]
    assert previews[0].closed


def test_count_include_candidate_previews_for_file_reports_limit(
    monkeypatch,
    tmp_path,
):
    """Include candidate counting should preserve enumeration limit messages."""
    _patch_include_candidate_count_io(monkeypatch, tmp_path)

    def build_include_candidate_previews(**kwargs):
        raise CandidateEnumerationLimitError("too many")

    monkeypatch.setattr(
        counts,
        "build_include_candidate_previews",
        build_include_candidate_previews,
    )

    result = counts.count_include_candidate_previews_for_file(
        batch_name="cleanup",
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "modified",
            "mode": "100644",
        },
        selection_ids_to_include={9},
        replacement_payload=None,
    )

    assert result == CandidatePreviewCount(too_many=True, error="too many")
