"""Tests for operation candidate preview models."""

from __future__ import annotations

import pytest

from git_stage_batch.batch import operation_candidates
from git_stage_batch.batch.operation_candidate_types import (
    OperationCandidatePreview,
    TargetCandidatePreview,
)
from git_stage_batch.core.buffer import LineBuffer


def _target(name: str) -> TargetCandidatePreview:
    return TargetCandidatePreview(
        target=name,
        file_path="notes.txt",
        before_buffer=LineBuffer.from_bytes(b"before\n"),
        after_buffer=LineBuffer.from_bytes(b"after\n"),
        file_mode=None,
        change_type="modified",
        destination_exists=True,
        resolution=None,
        resolution_ordinal=1,
        resolution_count=1,
        summary="",
        explanation="",
        ambiguity_target_line_range=None,
    )


def _preview(
    *targets: TargetCandidatePreview,
) -> OperationCandidatePreview:
    return OperationCandidatePreview(
        operation="include",
        batch_name="cleanup",
        file_path="notes.txt",
        ordinal=1,
        count=1,
        candidate_id="candidate-1",
        targets=targets,
        batch_fingerprint="batch",
        target_fingerprints={},
        target_result_fingerprints={},
        scope_fingerprint="scope",
    )


def test_operation_candidate_preview_requires_named_target():
    """Named target lookup should return the matching target preview."""
    worktree_target = _target("worktree")
    index_target = _target("index")
    preview = _preview(worktree_target, index_target)

    try:
        assert preview.require_target("index") is index_target
        assert preview.require_target("worktree") is worktree_target
    finally:
        preview.close()


def test_operation_candidate_preview_rejects_missing_target():
    """Named target lookup should reject an invalid candidate shape."""
    preview = _preview(_target("worktree"))

    try:
        with pytest.raises(KeyError) as exc_info:
            preview.require_target("index")
    finally:
        preview.close()

    assert exc_info.value.args == ("index",)


def test_target_candidate_materialization_clones_before_buffer(monkeypatch):
    """Candidate previews share immutable before-storage without copying it."""
    before = LineBuffer.from_bytes(b"before\n")

    monkeypatch.setattr(
        operation_candidates,
        "merge_batch_from_line_sequences_as_buffer",
        lambda *args, **kwargs: LineBuffer.from_bytes(b"after\n"),
    )

    class _ChangeType:
        value = "modified"

    monkeypatch.setattr(
        operation_candidates,
        "selected_text_target_change_type",
        lambda *args, **kwargs: _ChangeType(),
    )

    preview = operation_candidates._materialize_target_candidate(
        target="worktree",
        file_path="notes.txt",
        source_lines=LineBuffer.from_bytes(b"source\n"),
        ownership=object(),
        before_lines=before,
        candidate=None,
        file_mode=None,
        text_change_type=object(),
        destination_exists=True,
        selected_ids=None,
    )

    try:
        assert preview.before_buffer._backing is before._backing
        before.close()
        assert preview.before_buffer[0] == b"before\n"
    finally:
        before.close()
        preview.close()


def test_target_candidate_materialization_closes_clone_when_merge_fails(
    monkeypatch,
):
    """A refused candidate merge should release its cloned before-buffer."""
    before = LineBuffer.from_bytes(b"before\n")
    source = LineBuffer.from_bytes(b"source\n")
    clones = []
    real_clone = LineBuffer.clone

    def record_clone(buffer, *, spool_dir=None):
        clone = real_clone(buffer, spool_dir=spool_dir)
        clones.append(clone)
        return clone

    monkeypatch.setattr(LineBuffer, "clone", record_clone)
    monkeypatch.setattr(
        operation_candidates,
        "merge_batch_from_line_sequences_as_buffer",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("merge failed")
        ),
    )

    try:
        with pytest.raises(RuntimeError, match="merge failed"):
            operation_candidates._materialize_target_candidate(
                target="worktree",
                file_path="notes.txt",
                source_lines=source,
                ownership=object(),
                before_lines=before,
                candidate=None,
                file_mode=None,
                text_change_type=object(),
                destination_exists=True,
                selected_ids=None,
            )

        assert len(clones) == 1
        with pytest.raises(ValueError, match="buffer is closed"):
            _ = clones[0].byte_count
    finally:
        source.close()
        before.close()


def test_apply_candidate_build_closes_prior_previews_on_later_failure(
    monkeypatch,
):
    """Partial candidate construction should not leak earlier previews."""

    class _Target:
        def __init__(self):
            self.before_buffer = object()
            self.closed = False

        def close(self):
            self.closed = True

    first_target = _Target()
    calls = 0

    def materialize(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return first_target
        raise RuntimeError("second candidate failed")

    monkeypatch.setattr(
        operation_candidates,
        "_merge_candidates_or_unambiguous",
        lambda *args, **kwargs: (object(), object()),
    )
    monkeypatch.setattr(
        operation_candidates,
        "_materialize_target_candidate",
        materialize,
    )
    monkeypatch.setattr(
        operation_candidates,
        "_fingerprint_batch",
        lambda **kwargs: "batch",
    )
    monkeypatch.setattr(
        operation_candidates,
        "_fingerprint_scope",
        lambda **kwargs: "scope",
    )
    monkeypatch.setattr(
        operation_candidates,
        "_fingerprint_target",
        lambda *args: "target",
    )
    monkeypatch.setattr(
        operation_candidates,
        "_fingerprint_target_result",
        lambda target: "result",
    )
    monkeypatch.setattr(
        operation_candidates,
        "_fingerprint_candidate_id",
        lambda **kwargs: "candidate",
    )

    with pytest.raises(RuntimeError, match="second candidate failed"):
        operation_candidates.build_apply_candidate_previews(
            batch_name="batch",
            file_path="notes.txt",
            source_lines=object(),
            ownership=object(),
            worktree_lines=object(),
            batch_source_commit="commit",
            file_meta={},
            text_change_type=object(),
            worktree_file_mode=None,
            worktree_exists=True,
            selected_ids=None,
            selection_ids=None,
        )

    assert first_target.closed
