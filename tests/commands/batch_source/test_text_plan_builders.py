"""Tests for batch-source text action plan builders."""

from __future__ import annotations

from contextlib import AbstractContextManager

import pytest

from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.replacement import ReplacementPayload
from git_stage_batch.core.text_lifecycle import TextFileChangeType
from git_stage_batch.exceptions import MergeError
import git_stage_batch.commands.batch_source.text_plan_builders as builders


class _Ownership:
    def __init__(self, *, empty: bool = False) -> None:
        self._empty = empty

    def is_empty(self) -> bool:
        return self._empty


class _OwnershipContext(AbstractContextManager):
    def __init__(self, ownership: _Ownership) -> None:
        self.ownership = ownership

    def __enter__(self) -> _Ownership:
        return self.ownership

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _ReplacementView(AbstractContextManager):
    def __init__(self, source_buffer: LineBuffer, ownership: _Ownership) -> None:
        self.source_buffer = source_buffer
        self.ownership = ownership
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True
        self.source_buffer.close()


def _patch_apply_text_plan_io(monkeypatch, tmp_path, ownership: _Ownership):
    batch_buffer = LineBuffer.from_bytes(b"batch\n")
    worktree_buffer = LineBuffer.from_bytes(b"worktree\n")
    (tmp_path / "notes.txt").write_bytes(b"worktree\n")

    monkeypatch.setattr(
        builders,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        builders,
        "read_git_object_buffer_or_none",
        lambda spec: batch_buffer if spec == "commit:notes.txt" else None,
    )
    monkeypatch.setattr(
        builders,
        "load_working_tree_file_as_buffer",
        lambda file_path: worktree_buffer,
    )
    monkeypatch.setattr(
        builders,
        "acquire_batch_ownership_for_display_ids_from_lines",
        lambda file_meta, source_lines, selection_ids: _OwnershipContext(ownership),
    )
    return batch_buffer, worktree_buffer


def _patch_include_text_plan_io(monkeypatch, tmp_path, ownership: _Ownership):
    index_buffer = LineBuffer.from_bytes(b"index\n")
    batch_buffer = LineBuffer.from_bytes(b"batch\n")
    worktree_buffer = LineBuffer.from_bytes(b"worktree\n")
    (tmp_path / "notes.txt").write_bytes(b"worktree\n")

    monkeypatch.setattr(
        builders,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )

    def read_git_object_buffer_or_none(spec):
        if spec == ":notes.txt":
            return index_buffer
        if spec == "commit:notes.txt":
            return batch_buffer
        return None

    monkeypatch.setattr(
        builders,
        "read_git_object_buffer_or_none",
        read_git_object_buffer_or_none,
    )
    monkeypatch.setattr(
        builders,
        "load_working_tree_file_as_buffer",
        lambda file_path: worktree_buffer,
    )
    monkeypatch.setattr(
        builders,
        "acquire_batch_ownership_for_display_ids_from_lines",
        lambda file_meta, source_lines, selection_ids: _OwnershipContext(ownership),
    )
    return index_buffer, batch_buffer, worktree_buffer


def _patch_discard_text_plan_io(
    monkeypatch,
    tmp_path,
    ownership: _Ownership,
    *,
    baseline_exists: bool = True,
):
    batch_buffer = LineBuffer.from_bytes(b"batch\n")
    baseline_buffer = (
        LineBuffer.from_bytes(b"baseline\n")
        if baseline_exists
        else None
    )
    worktree_buffer = LineBuffer.from_bytes(b"worktree\n")
    (tmp_path / "notes.txt").write_bytes(b"worktree\n")

    monkeypatch.setattr(
        builders,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )

    def read_git_object_buffer_or_none(spec):
        if spec == "commit:notes.txt":
            return batch_buffer
        if spec == "baseline:notes.txt":
            return baseline_buffer
        return None

    monkeypatch.setattr(
        builders,
        "read_git_object_buffer_or_none",
        read_git_object_buffer_or_none,
    )
    monkeypatch.setattr(
        builders,
        "load_working_tree_file_as_buffer",
        lambda file_path: worktree_buffer,
    )
    monkeypatch.setattr(
        builders,
        "detect_file_mode_in_commit",
        lambda commit, file_path: "100755" if baseline_exists else None,
    )
    monkeypatch.setattr(
        builders,
        "acquire_batch_ownership_for_display_ids_from_lines",
        lambda file_meta, source_lines, selection_ids: _OwnershipContext(ownership),
    )
    return batch_buffer, baseline_buffer, worktree_buffer


def test_build_apply_text_file_action_plan_returns_merged_plan(
    monkeypatch,
    tmp_path,
):
    """Apply text planning should merge owned source lines into worktree lines."""
    ownership = _Ownership()
    batch_buffer, worktree_buffer = _patch_apply_text_plan_io(
        monkeypatch,
        tmp_path,
        ownership,
    )
    calls = {}

    def merge_batch_from_line_sequences_as_buffer(source_lines, line_ownership, target):
        calls["merge"] = (source_lines, line_ownership, target)
        return LineBuffer.from_bytes(b"merged\n")

    monkeypatch.setattr(
        builders,
        "merge_batch_from_line_sequences_as_buffer",
        merge_batch_from_line_sequences_as_buffer,
    )

    result = builders.build_apply_text_file_action_plan(
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "modified",
            "mode": "100755",
        },
        selected_ids={1},
        selection_ids_to_apply={7},
    )

    assert not result.missing_source
    assert result.plan is not None
    assert result.plan.file_path == "notes.txt"
    assert result.plan.buffer is not None
    assert result.plan.buffer.to_bytes() == b"merged\n"
    assert result.plan.file_mode is None
    assert result.plan.change_type == TextFileChangeType.MODIFIED
    assert calls["merge"] == (batch_buffer, ownership, worktree_buffer)

    result.plan.close()


def test_build_apply_text_file_action_plan_returns_deleted_plan(
    monkeypatch,
    tmp_path,
):
    """Whole-file deletion planning should not load batch source content."""
    monkeypatch.setattr(
        builders,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        builders,
        "read_git_object_buffer_or_none",
        lambda spec: (_ for _ in ()).throw(AssertionError("unexpected load")),
    )

    result = builders.build_apply_text_file_action_plan(
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "deleted",
            "mode": "100644",
        },
        selected_ids=None,
        selection_ids_to_apply=None,
    )

    assert not result.missing_source
    assert result.plan is not None
    assert result.plan.file_path == "notes.txt"
    assert result.plan.buffer is None
    assert result.plan.file_mode == "100644"
    assert result.plan.change_type == TextFileChangeType.DELETED


def test_build_apply_text_file_action_plan_reports_missing_source(
    monkeypatch,
    tmp_path,
):
    """Missing batch source content should stay visible to the command."""
    monkeypatch.setattr(
        builders,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        builders,
        "read_git_object_buffer_or_none",
        lambda spec: None,
    )

    result = builders.build_apply_text_file_action_plan(
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "modified",
            "mode": "100644",
        },
        selected_ids=None,
        selection_ids_to_apply=None,
    )

    assert result.missing_source
    assert result.plan is None


def test_build_apply_text_file_action_plan_skips_empty_partial_ownership(
    monkeypatch,
    tmp_path,
):
    """Empty partial ownership should produce no action plan."""
    _patch_apply_text_plan_io(monkeypatch, tmp_path, _Ownership(empty=True))

    result = builders.build_apply_text_file_action_plan(
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "modified",
            "mode": "100644",
        },
        selected_ids={1},
        selection_ids_to_apply=set(),
    )

    assert not result.missing_source
    assert result.plan is None


def test_build_discard_text_file_action_plan_returns_discarded_plan(
    monkeypatch,
    tmp_path,
):
    """Discard text planning should remove owned source lines from worktree."""
    ownership = _Ownership()
    batch_buffer, baseline_buffer, worktree_buffer = _patch_discard_text_plan_io(
        monkeypatch,
        tmp_path,
        ownership,
    )
    calls = {}

    def discard_batch_from_line_sequences_as_buffer(
        source_lines,
        line_ownership,
        working_lines,
        baseline_lines,
    ):
        calls["discard"] = (
            source_lines,
            line_ownership,
            working_lines,
            baseline_lines,
        )
        return LineBuffer.from_bytes(b"discarded\n")

    monkeypatch.setattr(
        builders,
        "discard_batch_from_line_sequences_as_buffer",
        discard_batch_from_line_sequences_as_buffer,
    )

    result = builders.build_discard_text_file_action_plan(
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "modified",
            "mode": "100644",
        },
        baseline_commit="baseline",
        selected_ids={1},
        selection_ids_to_discard={7},
    )

    assert not result.missing_source
    assert result.plan is not None
    assert result.plan.file_path == "notes.txt"
    assert result.plan.buffer is not None
    assert result.plan.buffer.to_bytes() == b"discarded\n"
    assert result.plan.file_mode is None
    assert result.plan.change_type == TextFileChangeType.MODIFIED
    assert calls["discard"] == (
        batch_buffer,
        ownership,
        worktree_buffer,
        baseline_buffer,
    )

    result.plan.close()


def test_build_discard_text_file_action_plan_restores_lifecycle_baseline(
    monkeypatch,
):
    """Whole-path discard planning should restore baseline content."""
    baseline_buffer = LineBuffer.from_bytes(b"baseline\n")

    def read_git_object_buffer_or_none(spec):
        if spec == "baseline:notes.txt":
            return baseline_buffer
        raise AssertionError("unexpected load")

    monkeypatch.setattr(
        builders,
        "read_git_object_buffer_or_none",
        read_git_object_buffer_or_none,
    )
    monkeypatch.setattr(
        builders,
        "detect_file_mode_in_commit",
        lambda commit, file_path: "100755",
    )

    result = builders.build_discard_text_file_action_plan(
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "deleted",
            "mode": "100644",
        },
        baseline_commit="baseline",
        selected_ids=None,
        selection_ids_to_discard=None,
    )

    assert not result.missing_source
    assert result.plan is not None
    assert result.plan.buffer is baseline_buffer
    assert result.plan.file_mode == "100755"
    assert result.plan.change_type == TextFileChangeType.MODIFIED

    result.plan.close()


def test_build_discard_text_file_action_plan_deletes_lifecycle_without_baseline(
    monkeypatch,
):
    """Whole-path discard planning should delete paths absent from baseline."""
    monkeypatch.setattr(
        builders,
        "read_git_object_buffer_or_none",
        lambda spec: None,
    )
    monkeypatch.setattr(
        builders,
        "detect_file_mode_in_commit",
        lambda commit, file_path: None,
    )

    result = builders.build_discard_text_file_action_plan(
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "added",
            "mode": "100644",
        },
        baseline_commit="baseline",
        selected_ids=None,
        selection_ids_to_discard=None,
    )

    assert not result.missing_source
    assert result.plan is not None
    assert result.plan.buffer is None
    assert result.plan.file_mode is None
    assert result.plan.change_type == TextFileChangeType.DELETED


def test_build_discard_text_file_action_plan_reports_missing_source(
    monkeypatch,
):
    """Missing batch source content should stay visible to the command."""
    monkeypatch.setattr(
        builders,
        "read_git_object_buffer_or_none",
        lambda spec: None,
    )

    result = builders.build_discard_text_file_action_plan(
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "modified",
            "mode": "100644",
        },
        baseline_commit="baseline",
        selected_ids=None,
        selection_ids_to_discard=None,
    )

    assert result.missing_source
    assert result.plan is None


def test_build_discard_text_file_action_plan_skips_empty_ownership(
    monkeypatch,
    tmp_path,
):
    """Empty ownership should produce no discard action plan."""
    _patch_discard_text_plan_io(monkeypatch, tmp_path, _Ownership(empty=True))
    monkeypatch.setattr(
        builders,
        "discard_batch_from_line_sequences_as_buffer",
        lambda *args: (_ for _ in ()).throw(AssertionError("unexpected merge")),
    )

    result = builders.build_discard_text_file_action_plan(
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "modified",
            "mode": "100644",
        },
        baseline_commit="baseline",
        selected_ids={1},
        selection_ids_to_discard=set(),
    )

    assert not result.missing_source
    assert result.plan is None


def test_build_discard_text_file_action_plan_closes_empty_deleted_buffer(
    monkeypatch,
    tmp_path,
):
    """Partial discard planning should not retain unused empty delete buffers."""
    _patch_discard_text_plan_io(
        monkeypatch,
        tmp_path,
        _Ownership(),
        baseline_exists=False,
    )
    discarded_buffer = LineBuffer.from_bytes(b"")
    monkeypatch.setattr(
        builders,
        "discard_batch_from_line_sequences_as_buffer",
        lambda source_lines, line_ownership, working_lines, baseline_lines: (
            discarded_buffer
        ),
    )

    result = builders.build_discard_text_file_action_plan(
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "added",
            "mode": "100644",
        },
        baseline_commit="baseline",
        selected_ids={1},
        selection_ids_to_discard={7},
    )

    assert result.plan is not None
    assert result.plan.buffer is None
    assert result.plan.change_type == TextFileChangeType.DELETED
    with pytest.raises(ValueError, match="buffer is closed"):
        discarded_buffer.to_bytes()


def test_build_include_text_file_action_plan_uses_replacement_view(
    monkeypatch,
    tmp_path,
):
    """Replacement include planning should merge generated source lines."""
    ownership = _Ownership()
    replacement_ownership = _Ownership()
    replacement_view = _ReplacementView(
        LineBuffer.from_bytes(b"replacement\n"),
        replacement_ownership,
    )
    index_buffer, batch_buffer, worktree_buffer = _patch_include_text_plan_io(
        monkeypatch,
        tmp_path,
        ownership,
    )
    payload = ReplacementPayload.from_text("new\n")
    calls = {"merge": []}

    def build_replacement_batch_view_from_lines(source_lines, line_ownership, value):
        calls["replacement"] = (source_lines, line_ownership, value)
        return replacement_view

    def merge_batch_from_line_sequences_as_buffer(source_lines, line_ownership, target):
        calls["merge"].append((source_lines, line_ownership, target))
        if target is index_buffer:
            return LineBuffer.from_bytes(b"replacement-index\n")
        return LineBuffer.from_bytes(b"replacement-worktree\n")

    monkeypatch.setattr(
        builders,
        "build_replacement_batch_view_from_lines",
        build_replacement_batch_view_from_lines,
    )
    monkeypatch.setattr(
        builders,
        "merge_batch_from_line_sequences_as_buffer",
        merge_batch_from_line_sequences_as_buffer,
    )

    result = builders.build_include_text_file_action_plan(
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "modified",
            "mode": "100644",
        },
        selected_ids={1},
        selection_ids_to_include={7},
        replacement_payload=payload,
    )

    assert result.plan is not None
    assert result.plan.index_buffer is not None
    assert result.plan.working_buffer is not None
    assert result.plan.index_buffer.to_bytes() == b"replacement-index\n"
    assert result.plan.working_buffer.to_bytes() == b"replacement-worktree\n"
    assert calls["replacement"] == (batch_buffer, ownership, payload)
    assert calls["merge"] == [
        (replacement_view.source_buffer, replacement_ownership, index_buffer),
        (replacement_view.source_buffer, replacement_ownership, worktree_buffer),
    ]
    assert replacement_view.closed

    result.plan.close()


def test_build_include_text_file_action_plan_closes_partial_merge_on_failure(
    monkeypatch,
    tmp_path,
):
    """A failed second merge should release the first merged target buffer."""
    ownership = _Ownership()
    index_buffer, _batch_buffer, _worktree_buffer = _patch_include_text_plan_io(
        monkeypatch,
        tmp_path,
        ownership,
    )
    merged_index_buffer = LineBuffer.from_bytes(b"merged-index\n")

    def merge_batch_from_line_sequences_as_buffer(source_lines, line_ownership, target):
        if target is index_buffer:
            return merged_index_buffer
        raise MergeError("worktree conflict")

    monkeypatch.setattr(
        builders,
        "merge_batch_from_line_sequences_as_buffer",
        merge_batch_from_line_sequences_as_buffer,
    )

    with pytest.raises(MergeError, match="worktree conflict"):
        builders.build_include_text_file_action_plan(
            file_path="notes.txt",
            file_meta={
                "batch_source_commit": "commit",
                "change_type": "modified",
                "mode": "100644",
            },
            selected_ids={1},
            selection_ids_to_include={7},
            replacement_payload=None,
        )

    with pytest.raises(ValueError, match="buffer is closed"):
        merged_index_buffer.to_bytes()


def test_build_include_text_file_action_plan_returns_merged_plan(
    monkeypatch,
    tmp_path,
):
    """Include text planning should merge owned source lines into both targets."""
    ownership = _Ownership()
    index_buffer, batch_buffer, worktree_buffer = _patch_include_text_plan_io(
        monkeypatch,
        tmp_path,
        ownership,
    )
    calls = []

    def merge_batch_from_line_sequences_as_buffer(source_lines, line_ownership, target):
        calls.append((source_lines, line_ownership, target))
        if target is index_buffer:
            return LineBuffer.from_bytes(b"merged-index\n")
        return LineBuffer.from_bytes(b"merged-worktree\n")

    monkeypatch.setattr(
        builders,
        "merge_batch_from_line_sequences_as_buffer",
        merge_batch_from_line_sequences_as_buffer,
    )

    result = builders.build_include_text_file_action_plan(
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "modified",
            "mode": "100755",
        },
        selected_ids={1},
        selection_ids_to_include={7},
        replacement_payload=None,
    )

    assert not result.missing_source
    assert result.plan is not None
    assert result.plan.file_path == "notes.txt"
    assert result.plan.index_buffer is not None
    assert result.plan.working_buffer is not None
    assert result.plan.index_buffer.to_bytes() == b"merged-index\n"
    assert result.plan.working_buffer.to_bytes() == b"merged-worktree\n"
    assert result.plan.index_file_mode is None
    assert result.plan.working_file_mode is None
    assert result.plan.index_change_type == TextFileChangeType.MODIFIED
    assert result.plan.working_change_type == TextFileChangeType.MODIFIED
    assert calls == [
        (batch_buffer, ownership, index_buffer),
        (batch_buffer, ownership, worktree_buffer),
    ]

    result.plan.close()


def test_build_include_text_file_action_plan_returns_deleted_plan(
    monkeypatch,
    tmp_path,
):
    """Whole-file deletion planning should not load batch source content."""
    index_buffer = LineBuffer.from_bytes(b"index\n")
    monkeypatch.setattr(
        builders,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )

    def read_git_object_buffer_or_none(spec):
        if spec == ":notes.txt":
            return index_buffer
        raise AssertionError("unexpected load")

    monkeypatch.setattr(
        builders,
        "read_git_object_buffer_or_none",
        read_git_object_buffer_or_none,
    )

    result = builders.build_include_text_file_action_plan(
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "deleted",
            "mode": "100644",
        },
        selected_ids=None,
        selection_ids_to_include=None,
        replacement_payload=None,
    )

    assert not result.missing_source
    assert result.plan is not None
    assert result.plan.file_path == "notes.txt"
    assert result.plan.index_buffer is None
    assert result.plan.working_buffer is None
    assert result.plan.index_file_mode == "100644"
    assert result.plan.working_file_mode == "100644"
    assert result.plan.index_change_type == TextFileChangeType.DELETED
    assert result.plan.working_change_type == TextFileChangeType.DELETED


def test_build_include_text_file_action_plan_reports_missing_source(
    monkeypatch,
    tmp_path,
):
    """Missing batch source content should stay visible to the command."""
    index_buffer = LineBuffer.from_bytes(b"index\n")
    monkeypatch.setattr(
        builders,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )

    def read_git_object_buffer_or_none(spec):
        if spec == ":notes.txt":
            return index_buffer
        return None

    monkeypatch.setattr(
        builders,
        "read_git_object_buffer_or_none",
        read_git_object_buffer_or_none,
    )

    result = builders.build_include_text_file_action_plan(
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "modified",
            "mode": "100644",
        },
        selected_ids=None,
        selection_ids_to_include=None,
        replacement_payload=None,
    )

    assert result.missing_source
    assert result.plan is None


def test_build_include_text_file_action_plan_skips_empty_partial_ownership(
    monkeypatch,
    tmp_path,
):
    """Empty partial ownership should produce no action plan."""
    _patch_include_text_plan_io(monkeypatch, tmp_path, _Ownership(empty=True))

    result = builders.build_include_text_file_action_plan(
        file_path="notes.txt",
        file_meta={
            "batch_source_commit": "commit",
            "change_type": "modified",
            "mode": "100644",
        },
        selected_ids={1},
        selection_ids_to_include=set(),
        replacement_payload=None,
    )

    assert not result.missing_source
    assert result.plan is None
