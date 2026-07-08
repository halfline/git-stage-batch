"""Tests for batch-source candidate input metadata helpers."""

from __future__ import annotations

import pytest

from git_stage_batch.core.text_lifecycle import TextFileChangeType
import git_stage_batch.commands.batch_source.candidate_inputs as inputs


def test_is_text_candidate_entry_rejects_atomic_entries():
    """Text candidate input handling should skip binary and gitlink entries."""
    assert inputs.is_text_candidate_entry({"change_type": "modified"})
    assert not inputs.is_text_candidate_entry({"file_type": "binary"})
    assert not inputs.is_text_candidate_entry({"file_type": "gitlink"})


def test_candidate_batch_source_ref_returns_object_spec():
    """Batch source refs should carry both commit and object spec."""
    ref = inputs.candidate_batch_source_ref(
        "notes.txt",
        {"batch_source_commit": "abc123"},
    )

    assert ref == inputs.CandidateBatchSourceRef(
        commit="abc123",
        object_spec="abc123:notes.txt",
    )
    assert inputs.candidate_batch_source_ref("notes.txt", {}) is None


def test_require_candidate_batch_source_ref_uses_validated_metadata():
    """Required batch source refs should keep validated metadata failures visible."""
    ref = inputs.require_candidate_batch_source_ref(
        "notes.txt",
        {"batch_source_commit": "abc123"},
    )

    assert ref.object_spec == "abc123:notes.txt"
    with pytest.raises(KeyError):
        inputs.require_candidate_batch_source_ref("notes.txt", {})


def test_candidate_worktree_text_target_reports_existing_partial_path(
    monkeypatch,
    tmp_path,
):
    """Worktree target metadata should account for partial selections."""
    (tmp_path / "notes.txt").write_text("worktree\n")
    monkeypatch.setattr(
        inputs,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )

    target = inputs.candidate_worktree_text_target(
        file_path="notes.txt",
        file_meta={
            "change_type": "deleted",
            "mode": "100755",
        },
        selected_ids={1},
    )

    assert target == inputs.CandidateWorktreeTarget(
        exists=True,
        file_mode=None,
        text_change_type=TextFileChangeType.DELETED,
    )


def test_candidate_worktree_text_target_reports_missing_whole_path(
    monkeypatch,
    tmp_path,
):
    """Worktree target metadata should preserve modes for missing paths."""
    monkeypatch.setattr(
        inputs,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )

    target = inputs.candidate_worktree_text_target(
        file_path="notes.txt",
        file_meta={
            "change_type": "added",
            "mode": "100755",
        },
        selected_ids=None,
    )

    assert target == inputs.CandidateWorktreeTarget(
        exists=False,
        file_mode="100755",
        text_change_type=TextFileChangeType.ADDED,
    )


def test_candidate_index_text_target_reports_mode_for_index_state():
    """Index target metadata should account for path existence and selection."""
    assert inputs.candidate_index_text_target(
        file_meta={"mode": "100755"},
        selected_ids={1},
        index_exists=True,
    ) == inputs.CandidateIndexTarget(exists=True, file_mode=None)

    assert inputs.candidate_index_text_target(
        file_meta={"mode": "100755"},
        selected_ids={1},
        index_exists=False,
    ) == inputs.CandidateIndexTarget(exists=False, file_mode="100755")
