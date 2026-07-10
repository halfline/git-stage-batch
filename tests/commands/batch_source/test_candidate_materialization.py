"""Tests for batch-source candidate materialization."""

from __future__ import annotations

from contextlib import AbstractContextManager

import pytest

from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.replacement import ReplacementPayload
from git_stage_batch.exceptions import CommandError
import git_stage_batch.commands.batch_source.candidate_materialization as materialization


class _OwnershipContext(AbstractContextManager):
    def __init__(self, ownership: object) -> None:
        self.ownership = ownership

    def __enter__(self) -> object:
        return self.ownership

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _Target:
    def __init__(self, name: str = "worktree") -> None:
        self.target = name


class _Preview:
    def __init__(
        self,
        name: str = "preview",
        targets: tuple[_Target, ...] | None = None,
    ) -> None:
        self.name = name
        self.targets = targets or (_Target(),)
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def require_target(self, name: str) -> _Target:
        return next(target for target in self.targets if target.target == name)


class _ReplacementView(AbstractContextManager):
    def __init__(self, source_buffer: LineBuffer, ownership: object) -> None:
        self.source_buffer = source_buffer
        self.ownership = ownership
        self.closed = False

    def __enter__(self) -> _ReplacementView:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.source_buffer.close()
        self.closed = True
        return None


def _patch_apply_materialization_io(monkeypatch, tmp_path):
    ownership = object()
    batch_buffer = LineBuffer.from_bytes(b"batch\n")
    worktree_buffer = LineBuffer.from_bytes(b"worktree\n")
    (tmp_path / "notes.txt").write_bytes(b"worktree\n")

    monkeypatch.setattr(
        materialization._candidate_inputs,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        materialization,
        "load_git_object_as_buffer",
        lambda spec: batch_buffer if spec == "commit:notes.txt" else None,
    )
    monkeypatch.setattr(
        materialization,
        "load_working_tree_file_as_buffer",
        lambda file_path: worktree_buffer,
    )
    monkeypatch.setattr(
        materialization,
        "acquire_batch_ownership_for_display_ids_from_lines",
        lambda file_meta, source_lines, selection_ids: _OwnershipContext(ownership),
    )
    return ownership


def test_materialize_apply_candidate_returns_reviewed_preview(monkeypatch, tmp_path):
    """Apply materialization should return the reviewed preview and mode."""
    ownership = _patch_apply_materialization_io(monkeypatch, tmp_path)
    previews = (_Preview("first"), _Preview("second"))
    calls = {}

    def build_apply_candidate_previews(**kwargs):
        calls["build"] = kwargs
        return previews

    def require_preview(loaded_previews, ordinal, **kwargs):
        calls["require_preview"] = (loaded_previews, ordinal, kwargs)
        return loaded_previews[1]

    def require_state(preview, ordinal, **kwargs):
        calls["require_state"] = (preview, ordinal, kwargs)

    monkeypatch.setattr(
        materialization,
        "build_apply_candidate_previews",
        build_apply_candidate_previews,
    )
    monkeypatch.setattr(
        materialization._candidate_previews,
        "require_candidate_preview_for_ordinal",
        require_preview,
    )
    monkeypatch.setattr(
        materialization._candidate_previews,
        "require_candidate_preview_state",
        require_state,
    )

    result = materialization.materialize_apply_candidate(
        batch_name="cleanup",
        raw_selector="cleanup:apply:2",
        ordinal=2,
        files={
            "notes.txt": {
                "batch_source_commit": "commit",
                "change_type": "modified",
                "mode": "100644",
            },
        },
        selected_ids={3},
        selection_ids_to_apply={9},
    )

    assert result.preview is previews[1]
    assert result.previews is previews
    assert result.target is previews[1].targets[0]
    assert result.file_path == "notes.txt"
    assert result.file_mode is None
    assert calls["build"]["batch_name"] == "cleanup"
    assert calls["build"]["file_path"] == "notes.txt"
    assert calls["build"]["ownership"] is ownership
    assert calls["build"]["selected_ids"] == {3}
    assert calls["build"]["selection_ids"] == {9}
    assert calls["build"]["worktree_exists"]
    assert calls["require_preview"] == (
        previews,
        2,
        {
            "batch_name": "cleanup",
            "operation": "apply",
            "file_path": "notes.txt",
        },
    )
    assert calls["require_state"] == (
        previews[1],
        2,
        {
            "selector": "cleanup:apply:2",
            "file_path": "notes.txt",
        },
    )

    result.close()

    assert [preview.closed for preview in previews] == [True, True]


def test_materialize_apply_candidate_closes_previews_on_state_failure(
    monkeypatch,
    tmp_path,
):
    """Apply materialization should close previews when state validation fails."""
    _patch_apply_materialization_io(monkeypatch, tmp_path)
    previews = (_Preview("first"), _Preview("second"))

    monkeypatch.setattr(
        materialization,
        "build_apply_candidate_previews",
        lambda **kwargs: previews,
    )
    monkeypatch.setattr(
        materialization._candidate_previews,
        "require_candidate_preview_for_ordinal",
        lambda loaded_previews, ordinal, **kwargs: loaded_previews[0],
    )

    def reject_state(preview, ordinal, **kwargs):
        raise CommandError("stale")

    monkeypatch.setattr(
        materialization._candidate_previews,
        "require_candidate_preview_state",
        reject_state,
    )

    with pytest.raises(CommandError):
        materialization.materialize_apply_candidate(
            batch_name="cleanup",
            raw_selector="cleanup:apply:1",
            ordinal=1,
            files={
                "notes.txt": {
                    "batch_source_commit": "commit",
                    "change_type": "modified",
                    "mode": "100644",
                },
            },
            selected_ids={3},
            selection_ids_to_apply={9},
        )

    assert [preview.closed for preview in previews] == [True, True]


def test_materialize_apply_candidate_rejects_binary_entries():
    """Apply materialization should reject binary batch entries."""
    with pytest.raises(CommandError) as exc_info:
        materialization.materialize_apply_candidate(
            batch_name="cleanup",
            raw_selector="cleanup:apply:1",
            ordinal=1,
            files={
                "notes.bin": {
                    "file_type": "binary",
                    "batch_source_commit": "commit",
                    "change_type": "modified",
                    "mode": "100644",
                },
            },
            selected_ids=None,
            selection_ids_to_apply=None,
        )

    assert "text batch entries" in exc_info.value.message


def _patch_include_materialization_io(monkeypatch, tmp_path):
    ownership = object()
    batch_buffer = LineBuffer.from_bytes(b"batch\n")
    index_buffer = LineBuffer.from_bytes(b"index\n")
    worktree_buffer = LineBuffer.from_bytes(b"worktree\n")
    (tmp_path / "notes.txt").write_bytes(b"worktree\n")

    def load_git_object_as_buffer(spec):
        if spec == "commit:notes.txt":
            return batch_buffer
        if spec == ":notes.txt":
            return index_buffer
        return None

    monkeypatch.setattr(
        materialization._candidate_inputs,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        materialization,
        "load_git_object_as_buffer",
        load_git_object_as_buffer,
    )
    monkeypatch.setattr(
        materialization,
        "load_working_tree_file_as_buffer",
        lambda file_path: worktree_buffer,
    )
    monkeypatch.setattr(
        materialization,
        "acquire_batch_ownership_for_display_ids_from_lines",
        lambda file_meta, source_lines, selection_ids: _OwnershipContext(ownership),
    )
    return ownership


def test_materialize_include_candidate_returns_reviewed_preview(
    monkeypatch,
    tmp_path,
):
    """Include materialization should return the reviewed preview and modes."""
    ownership = _patch_include_materialization_io(monkeypatch, tmp_path)
    replacement_payload = ReplacementPayload.from_text("replacement\n")
    replacement_ownership = object()
    replacement_view = _ReplacementView(
        LineBuffer.from_bytes(b"replacement\n"),
        replacement_ownership,
    )
    targets = (_Target("index"), _Target("worktree"))
    previews = (_Preview("first"), _Preview("second", targets))
    calls = {}

    def build_replacement_batch_view_from_lines(source_lines, view_ownership, payload):
        calls["replacement"] = (source_lines, view_ownership, payload)
        return replacement_view

    def build_include_candidate_previews(**kwargs):
        calls["build"] = kwargs
        return previews

    def require_preview(loaded_previews, ordinal, **kwargs):
        calls["require_preview"] = (loaded_previews, ordinal, kwargs)
        return loaded_previews[1]

    def require_state(preview, ordinal, **kwargs):
        calls["require_state"] = (preview, ordinal, kwargs)

    monkeypatch.setattr(
        materialization,
        "build_replacement_batch_view_from_lines",
        build_replacement_batch_view_from_lines,
    )
    monkeypatch.setattr(
        materialization,
        "build_include_candidate_previews",
        build_include_candidate_previews,
    )
    monkeypatch.setattr(
        materialization._candidate_previews,
        "require_candidate_preview_for_ordinal",
        require_preview,
    )
    monkeypatch.setattr(
        materialization._candidate_previews,
        "require_candidate_preview_state",
        require_state,
    )

    result = materialization.materialize_include_candidate(
        batch_name="cleanup",
        raw_selector="cleanup:include:2",
        ordinal=2,
        files={
            "notes.txt": {
                "batch_source_commit": "commit",
                "change_type": "modified",
                "mode": "100644",
            },
        },
        selected_ids={3},
        selection_ids_to_include={9},
        replacement_payload=replacement_payload,
    )

    assert result.preview is previews[1]
    assert result.previews is previews
    assert result.index_target is targets[0]
    assert result.worktree_target is targets[1]
    assert result.file_path == "notes.txt"
    assert result.index_file_mode is None
    assert result.worktree_file_mode is None
    assert calls["replacement"][1] is ownership
    assert calls["replacement"][2] is replacement_payload
    assert calls["build"]["batch_name"] == "cleanup"
    assert calls["build"]["file_path"] == "notes.txt"
    assert calls["build"]["source_lines"] is replacement_view.source_buffer
    assert calls["build"]["ownership"] is replacement_ownership
    assert calls["build"]["selected_ids"] == {3}
    assert calls["build"]["selection_ids"] == {9}
    assert calls["build"]["replacement_payload"] is replacement_payload
    assert calls["build"]["index_exists"]
    assert calls["build"]["worktree_exists"]
    assert calls["require_preview"] == (
        previews,
        2,
        {
            "batch_name": "cleanup",
            "operation": "include",
            "file_path": "notes.txt",
        },
    )
    assert calls["require_state"] == (
        previews[1],
        2,
        {
            "selector": "cleanup:include:2",
            "file_path": "notes.txt",
        },
    )
    assert replacement_view.closed

    result.close()

    assert [preview.closed for preview in previews] == [True, True]


def test_materialize_include_candidate_closes_previews_on_state_failure(
    monkeypatch,
    tmp_path,
):
    """Include materialization should close previews when state validation fails."""
    _patch_include_materialization_io(monkeypatch, tmp_path)
    previews = (
        _Preview("first", (_Target("index"), _Target("worktree"))),
        _Preview("second"),
    )

    monkeypatch.setattr(
        materialization,
        "build_include_candidate_previews",
        lambda **kwargs: previews,
    )
    monkeypatch.setattr(
        materialization._candidate_previews,
        "require_candidate_preview_for_ordinal",
        lambda loaded_previews, ordinal, **kwargs: loaded_previews[0],
    )

    def reject_state(preview, ordinal, **kwargs):
        raise CommandError("stale")

    monkeypatch.setattr(
        materialization._candidate_previews,
        "require_candidate_preview_state",
        reject_state,
    )

    with pytest.raises(CommandError):
        materialization.materialize_include_candidate(
            batch_name="cleanup",
            raw_selector="cleanup:include:1",
            ordinal=1,
            files={
                "notes.txt": {
                    "batch_source_commit": "commit",
                    "change_type": "modified",
                    "mode": "100644",
                },
            },
            selected_ids={3},
            selection_ids_to_include={9},
            replacement_payload=None,
        )

    assert [preview.closed for preview in previews] == [True, True]
