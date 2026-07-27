"""Tests for shared batch-source candidate planning."""

from __future__ import annotations

from contextlib import AbstractContextManager

from git_stage_batch.commands.batch_source import candidate_inputs
from git_stage_batch.commands.batch_source import candidate_planning as planning
from git_stage_batch.core.buffer import LineBuffer
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
