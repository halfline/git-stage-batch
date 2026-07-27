"""Tests for shared batch-source candidate planning."""

from __future__ import annotations

from contextlib import AbstractContextManager

import pytest

from git_stage_batch.commands.batch_source import candidate_inputs
from git_stage_batch.commands.batch_source import candidate_planning as planning
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.replacement import ReplacementPayload
from git_stage_batch.core.text_lifecycle import TextFileChangeType


class _Context(AbstractContextManager):
    def __init__(self, value: object) -> None:
        self.value = value
        self.closed = False

    def __enter__(self) -> object:
        return self.value

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.closed = True
        return None


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


def _worktree_target() -> candidate_inputs.CandidateWorktreeTarget:
    return candidate_inputs.CandidateWorktreeTarget(
        exists=True,
        file_mode="100755",
        text_change_type=TextFileChangeType.MODIFIED,
    )


def test_apply_planning_owns_ownership_and_builder_arguments(monkeypatch):
    ownership = object()
    ownership_context = _Context(ownership)
    source = LineBuffer.from_bytes(b"source\n")
    worktree = LineBuffer.from_bytes(b"worktree\n")
    calls = {}

    monkeypatch.setattr(
        planning,
        "acquire_batch_ownership_for_display_ids_from_lines",
        lambda *args, **kwargs: ownership_context,
    )

    def build_apply_candidate_previews(**kwargs):
        calls["build"] = kwargs
        return ("preview",)

    monkeypatch.setattr(
        planning,
        "_build_apply_candidate_previews",
        build_apply_candidate_previews,
    )

    try:
        previews = planning.plan_apply_candidate_previews(
            batch_name="cleanup",
            file_path="notes.txt",
            file_meta={"mode": "100755"},
            batch_source_lines=source,
            batch_source_commit="commit",
            worktree_lines=worktree,
            worktree_target=_worktree_target(),
            selected_ids={3},
            selection_ids={9},
        )
    finally:
        source.close()
        worktree.close()

    assert previews == ("preview",)
    assert calls["build"]["source_lines"] is source
    assert calls["build"]["ownership"] is ownership
    assert calls["build"]["worktree_lines"] is worktree
    assert calls["build"]["selected_ids"] == {3}
    assert calls["build"]["selection_ids"] == {9}
    assert calls["build"]["worktree_file_mode"] == "100755"
    assert calls["build"]["worktree_exists"]
    assert ownership_context.closed


def test_include_planning_owns_replacement_view_and_builder_arguments(
    monkeypatch,
    tmp_path,
):
    ownership = object()
    replacement_ownership = object()
    ownership_context = _Context(ownership)
    source = LineBuffer.from_bytes(b"source\n")
    index = LineBuffer.from_bytes(b"index\n")
    worktree = LineBuffer.from_bytes(b"worktree\n")
    replacement_view = _ReplacementView(
        LineBuffer.from_bytes(b"replacement\n"),
        replacement_ownership,
    )
    payload = ReplacementPayload.from_text("new\n")
    calls = {}

    monkeypatch.setattr(
        planning,
        "acquire_batch_ownership_for_display_ids_from_lines",
        lambda *args, **kwargs: ownership_context,
    )

    def build_replacement(source_lines, view_ownership, replacement, **kwargs):
        calls["replacement"] = (
            source_lines,
            view_ownership,
            replacement,
            kwargs,
        )
        return replacement_view

    def build_include_candidate_previews(**kwargs):
        calls["build"] = {
            **kwargs,
            "source_bytes": kwargs["source_lines"].to_bytes(),
        }
        return ("preview",)

    monkeypatch.setattr(
        planning,
        "build_replacement_batch_view_from_lines",
        build_replacement,
    )
    monkeypatch.setattr(
        planning,
        "_build_include_candidate_previews",
        build_include_candidate_previews,
    )

    try:
        previews = planning.plan_include_candidate_previews(
            batch_name="cleanup",
            file_path="notes.txt",
            file_meta={"mode": "100755"},
            batch_source_lines=source,
            batch_source_commit="commit",
            index_lines=index,
            index_target=candidate_inputs.CandidateIndexTarget(
                exists=False,
                file_mode=None,
            ),
            worktree_lines=worktree,
            worktree_target=_worktree_target(),
            selected_ids={3},
            selection_ids={9},
            replacement_payload=payload,
            spool_dir=tmp_path,
        )
    finally:
        source.close()
        index.close()
        worktree.close()

    assert previews == ("preview",)
    assert calls["replacement"] == (
        source,
        ownership,
        payload,
        {"spool_dir": tmp_path},
    )
    assert calls["build"]["source_bytes"] == b"replacement\n"
    assert calls["build"]["ownership"] is replacement_ownership
    assert calls["build"]["index_lines"] is index
    assert calls["build"]["worktree_lines"] is worktree
    assert calls["build"]["index_exists"] is False
    assert calls["build"]["worktree_exists"] is True
    assert calls["build"]["spool_dir"] == tmp_path
    assert ownership_context.closed
    assert replacement_view.closed


def test_include_planning_identifies_replacement_failures(monkeypatch):
    source = LineBuffer.from_bytes(b"source\n")
    index = LineBuffer.from_bytes(b"index\n")
    worktree = LineBuffer.from_bytes(b"worktree\n")
    ownership_context = _Context(object())

    monkeypatch.setattr(
        planning,
        "acquire_batch_ownership_for_display_ids_from_lines",
        lambda *args, **kwargs: ownership_context,
    )
    monkeypatch.setattr(
        planning,
        "build_replacement_batch_view_from_lines",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad replacement")),
    )

    try:
        with pytest.raises(
            planning.CandidateReplacementError,
            match="bad replacement",
        ):
            planning.plan_include_candidate_previews(
                batch_name="cleanup",
                file_path="notes.txt",
                file_meta={},
                batch_source_lines=source,
                batch_source_commit="commit",
                index_lines=index,
                index_target=candidate_inputs.CandidateIndexTarget(
                    exists=True,
                    file_mode="100644",
                ),
                worktree_lines=worktree,
                worktree_target=_worktree_target(),
                selected_ids=None,
                selection_ids=None,
                replacement_payload=ReplacementPayload.from_text("new\n"),
            )
    finally:
        source.close()
        index.close()
        worktree.close()

    assert ownership_context.closed
