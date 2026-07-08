"""Tests for batch-source text action plan builders."""

from __future__ import annotations

from contextlib import AbstractContextManager

from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.text_lifecycle import TextFileChangeType
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
        "load_git_object_as_buffer",
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

    def load_git_object_as_buffer(spec):
        if spec == ":notes.txt":
            return index_buffer
        if spec == "commit:notes.txt":
            return batch_buffer
        return None

    monkeypatch.setattr(
        builders,
        "load_git_object_as_buffer",
        load_git_object_as_buffer,
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
        "load_git_object_as_buffer",
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
        "load_git_object_as_buffer",
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

    def load_git_object_as_buffer(spec):
        if spec == ":notes.txt":
            return index_buffer
        raise AssertionError("unexpected load")

    monkeypatch.setattr(
        builders,
        "load_git_object_as_buffer",
        load_git_object_as_buffer,
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

    def load_git_object_as_buffer(spec):
        if spec == ":notes.txt":
            return index_buffer
        return None

    monkeypatch.setattr(
        builders,
        "load_git_object_as_buffer",
        load_git_object_as_buffer,
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
