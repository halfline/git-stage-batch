"""Tests for operation candidate preview models."""

from __future__ import annotations

import pytest

from git_stage_batch.batch.operation_candidates import (
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
