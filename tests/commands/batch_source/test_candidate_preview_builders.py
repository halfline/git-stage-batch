"""Tests for batch-source candidate preview builders."""

from __future__ import annotations

from contextlib import AbstractContextManager

import pytest

from git_stage_batch.batch.source.selector import BatchSourceSelector
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.data.file_review.records import FileReviewAction
import git_stage_batch.commands.batch_source.candidate_preview_builders as builders
from git_stage_batch.exceptions import CommandError


class _OwnershipContext(AbstractContextManager):
    def __init__(self, ownership: object) -> None:
        self.ownership = ownership

    def __enter__(self) -> object:
        return self.ownership

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _ReplacementView(AbstractContextManager):
    def __init__(self, source_buffer: LineBuffer, ownership: object) -> None:
        self.source_buffer = source_buffer
        self.ownership = ownership

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.source_buffer.close()
        return None


def _patch_common_candidate_builder_io(monkeypatch, tmp_path):
    ownership = object()
    batch_buffer = LineBuffer.from_bytes(b"batch\n")
    worktree_buffer = LineBuffer.from_bytes(b"worktree\n")
    index_buffer = LineBuffer.from_bytes(b"index\n")
    (tmp_path / "notes.txt").write_bytes(b"worktree\n")

    def load_git_object(spec: str):
        if spec == "commit:notes.txt":
            return batch_buffer
        if spec == ":notes.txt":
            return index_buffer
        return None

    monkeypatch.setattr(
        builders._candidate_inputs,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    monkeypatch.setattr(builders, "load_git_object_as_buffer", load_git_object)
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
    return ownership


def test_build_batch_source_candidate_previews_builds_apply_candidates(
    monkeypatch,
    tmp_path,
):
    """Apply candidate construction should pass translated selection IDs."""
    ownership = _patch_common_candidate_builder_io(monkeypatch, tmp_path)
    calls = {}

    def translate_selection_ids(batch_name, file_path, selected_ids, action):
        calls["translation"] = (batch_name, file_path, selected_ids, action)
        return {10}, None

    def build_apply_candidate_previews(**kwargs):
        calls["build"] = kwargs
        return ("apply-preview",)

    monkeypatch.setattr(
        builders,
        "build_apply_candidate_previews",
        build_apply_candidate_previews,
    )

    previews = builders.build_batch_source_candidate_previews(
        selector=BatchSourceSelector("cleanup", "apply", 1),
        files={
            "notes.txt": {
                "batch_source_commit": "commit",
                "change_type": "modified",
                "mode": "100644",
            },
        },
        file_path="notes.txt",
        selected_ids={1},
        replacement_text=None,
        translate_selection_ids=translate_selection_ids,
    )

    assert previews == ("apply-preview",)
    assert calls["translation"] == (
        "cleanup",
        "notes.txt",
        {1},
        FileReviewAction.APPLY_FROM_BATCH,
    )
    assert calls["build"]["batch_name"] == "cleanup"
    assert calls["build"]["file_path"] == "notes.txt"
    assert calls["build"]["ownership"] is ownership
    assert calls["build"]["selected_ids"] == {1}
    assert calls["build"]["selection_ids"] == {10}
    assert calls["build"]["worktree_exists"]


def test_build_batch_source_candidate_previews_builds_include_replacement(
    monkeypatch,
    tmp_path,
):
    """Include candidate construction should pass replacement preview state."""
    replacement_ownership = object()
    _patch_common_candidate_builder_io(monkeypatch, tmp_path)
    calls = {}

    def translate_selection_ids(batch_name, file_path, selected_ids, action):
        calls["translation"] = (batch_name, file_path, selected_ids, action)
        return {20}, None

    def build_replacement_batch_view_from_lines(source_lines, ownership, payload):
        calls["replacement_payload"] = payload
        return _ReplacementView(
            LineBuffer.from_bytes(b"replacement\n"),
            replacement_ownership,
        )

    def build_include_candidate_previews(**kwargs):
        calls["build"] = kwargs
        return ("include-preview",)

    monkeypatch.setattr(
        builders,
        "build_replacement_batch_view_from_lines",
        build_replacement_batch_view_from_lines,
    )
    monkeypatch.setattr(
        builders.replacement_selection,
        "require_contiguous_display_selection",
        lambda selected_ids: calls.setdefault("contiguous", selected_ids),
    )
    monkeypatch.setattr(
        builders,
        "build_include_candidate_previews",
        build_include_candidate_previews,
    )

    previews = builders.build_batch_source_candidate_previews(
        selector=BatchSourceSelector("cleanup", "include", 2),
        files={
            "notes.txt": {
                "batch_source_commit": "commit",
                "change_type": "modified",
                "mode": "100644",
            },
        },
        file_path="notes.txt",
        selected_ids={1},
        replacement_text="new\n",
        translate_selection_ids=translate_selection_ids,
    )

    assert previews == ("include-preview",)
    assert calls["translation"] == (
        "cleanup",
        "notes.txt",
        {1},
        FileReviewAction.INCLUDE_FROM_BATCH,
    )
    assert calls["contiguous"] == {1}
    assert calls["build"]["ownership"] is replacement_ownership
    assert calls["build"]["replacement_payload"] is calls["replacement_payload"]
    assert calls["build"]["selection_ids"] == {20}
    assert calls["build"]["index_exists"]
    assert calls["build"]["worktree_exists"]


def test_build_batch_source_candidate_previews_rejects_apply_replacement(
    monkeypatch,
    tmp_path,
):
    """Replacement previews should belong to include candidates."""
    _patch_common_candidate_builder_io(monkeypatch, tmp_path)

    with pytest.raises(CommandError) as exc_info:
        builders.build_batch_source_candidate_previews(
            selector=BatchSourceSelector("cleanup", "apply", 1),
            files={
                "notes.txt": {
                    "batch_source_commit": "commit",
                    "change_type": "modified",
                    "mode": "100644",
                },
            },
            file_path="notes.txt",
            selected_ids={1},
            replacement_text="new\n",
        )

    assert "Replacement preview is not valid for apply candidates." in (
        exc_info.value.message
    )


def test_build_batch_source_candidate_previews_requires_candidate_selector():
    """Candidate construction should require a candidate operation."""
    with pytest.raises(ValueError) as exc_info:
        builders.build_batch_source_candidate_previews(
            selector=BatchSourceSelector("cleanup"),
            files={},
            file_path="notes.txt",
            selected_ids=None,
            replacement_text=None,
        )

    assert "candidate selector" in str(exc_info.value)
