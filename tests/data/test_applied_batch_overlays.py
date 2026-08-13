"""Tests for identity-bound applied-batch overlay state."""

from __future__ import annotations

from dataclasses import asdict
import json
import subprocess

import pytest

import git_stage_batch.data.applied_batch_overlays as overlays
from git_stage_batch.batch.state.lifecycle import create_batch, update_batch_note
from git_stage_batch.batch.state.query import read_batch_metadata
from git_stage_batch.data.applied_batch_overlays import (
    AppliedFileProvenance,
    fresh_applied_batch_overlay_for_path,
    load_applied_batch_overlay_snapshot,
    rebind_applied_batch_overlays_after_session,
    record_applied_batch_overlays,
)
from git_stage_batch.data.file_target_identity import capture_worktree_identity
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.paths import get_applied_batch_overlays_file_path


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a repository with one tracked file and one canonical batch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    (repo / "file.txt").write_text("before\n")
    subprocess.run(["git", "add", "file.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add file"],
        check=True,
        capture_output=True,
    )
    create_batch("saved")
    return repo


def _record_overlay(repo) -> None:
    before_identity = capture_worktree_identity("file.txt")
    (repo / "file.txt").write_text("after\n")
    revision = read_batch_metadata("saved")["revision"]
    assert isinstance(revision, str)
    record_applied_batch_overlays(
        batch_name="saved",
        batch_revision=revision,
        files={
            "file.txt": AppliedFileProvenance(
                file_metadata={
                    "batch_source_commit": "a" * 40,
                    "change_type": "modified",
                    "presence_claims": [{"source_lines": ["1"]}],
                },
                source_object_id="b" * 40,
            ),
        },
        before_worktree_identities={"file.txt": before_identity},
    )


def test_fresh_overlay_requires_exact_repository_and_batch_identity(temp_git_repo):
    """Exact worktree state should reveal provenance until its batch changes."""
    _record_overlay(temp_git_repo)

    view = fresh_applied_batch_overlay_for_path("file.txt")

    assert view.batch_names == frozenset({"saved"})
    assert view.revealed_owner_names
    owner = next(iter(view.revealed_owner_names))
    assert view.source_object_by_owner[owner] == "b" * 40

    update_batch_note("saved", "new revision")

    assert not fresh_applied_batch_overlay_for_path(
        "file.txt"
    ).revealed_owner_names


def test_overlay_treats_intent_to_add_as_absent_index_identity(temp_git_repo):
    """Fresh start's intent-to-add normalization must not stale an overlay."""
    untracked_path = temp_git_repo / "untracked.txt"
    untracked_path.write_text("new\n")
    revision = read_batch_metadata("saved")["revision"]
    assert isinstance(revision, str)
    identity = capture_worktree_identity("untracked.txt")
    record_applied_batch_overlays(
        batch_name="saved",
        batch_revision=revision,
        files={
            "untracked.txt": AppliedFileProvenance(
                file_metadata={
                    "batch_source_commit": "a" * 40,
                    "change_type": "added",
                    "presence_claims": [{"source_lines": ["1"]}],
                },
                source_object_id="b" * 40,
            ),
        },
        before_worktree_identities={"untracked.txt": identity},
    )
    subprocess.run(
        ["git", "add", "-N", "untracked.txt"],
        check=True,
        capture_output=True,
    )

    assert fresh_applied_batch_overlay_for_path(
        "untracked.txt"
    ).revealed_owner_names


def test_overlay_does_not_treat_staged_empty_blob_as_intent(temp_git_repo):
    """An ordinary empty index blob must invalidate absent-index provenance."""
    untracked_path = temp_git_repo / "untracked.txt"
    untracked_path.write_text("new\n")
    revision = read_batch_metadata("saved")["revision"]
    assert isinstance(revision, str)
    identity = capture_worktree_identity("untracked.txt")
    record_applied_batch_overlays(
        batch_name="saved",
        batch_revision=revision,
        files={
            "untracked.txt": AppliedFileProvenance(
                file_metadata={
                    "batch_source_commit": "a" * 40,
                    "change_type": "added",
                    "presence_claims": [{"source_lines": ["1"]}],
                },
                source_object_id="b" * 40,
            ),
        },
        before_worktree_identities={"untracked.txt": identity},
    )
    untracked_path.write_text("")
    subprocess.run(
        ["git", "add", "untracked.txt"],
        check=True,
        capture_output=True,
    )
    untracked_path.write_text("new\n")

    assert not fresh_applied_batch_overlay_for_path(
        "untracked.txt"
    ).revealed_owner_names


def test_session_allows_index_drift_only_after_fresh_start(temp_git_repo):
    """A pre-start index change must not acquire the session exemption."""
    _record_overlay(temp_git_repo)
    subprocess.run(["git", "add", "file.txt"], check=True, capture_output=True)

    initialize_abort_state()

    assert not fresh_applied_batch_overlay_for_path(
        "file.txt"
    ).revealed_owner_names


def test_session_rebinds_fresh_overlay_to_final_index(
    temp_git_repo,
    monkeypatch,
):
    """Session staging should preserve and then rebind fresh provenance."""
    _record_overlay(temp_git_repo)
    initialize_abort_state()
    subprocess.run(["git", "add", "file.txt"], check=True, capture_output=True)

    assert fresh_applied_batch_overlay_for_path(
        "file.txt"
    ).revealed_owner_names

    rebind_applied_batch_overlays_after_session()
    monkeypatch.setattr(overlays, "session_is_active", lambda: False)

    assert fresh_applied_batch_overlay_for_path(
        "file.txt"
    ).revealed_owner_names


def test_overlay_state_rejects_unknown_or_malformed_fields(temp_git_repo):
    """Advisory state must fail closed instead of accepting ambiguous data."""
    _record_overlay(temp_git_repo)
    state_path = get_applied_batch_overlays_file_path()
    state = json.loads(state_path.read_text())
    state["files"]["file.txt"]["unexpected"] = True
    state_path.write_text(json.dumps(state))

    with pytest.raises(CommandError, match="Applied-batch state is corrupt"):
        load_applied_batch_overlay_snapshot()


def test_recording_persists_only_compact_attribution_claims(temp_git_repo):
    """Advisory state must omit line-scale canonical merge bookkeeping."""
    before_identity = capture_worktree_identity("file.txt")
    (temp_git_repo / "file.txt").write_text("after\n")
    revision = read_batch_metadata("saved")["revision"]
    assert isinstance(revision, str)
    record_applied_batch_overlays(
        batch_name="saved",
        batch_revision=revision,
        files={
            "file.txt": AppliedFileProvenance(
                file_metadata={
                    "batch_source_commit": "a" * 40,
                    "change_type": "modified",
                    "presence_claims": [
                        {
                            "source_lines": ["1-500000"],
                            "baseline_references": {
                                "1": {"after_line": 1},
                            },
                        },
                        {
                            "source_lines": ["500001-1000000"],
                            "baseline_references": {
                                "500001": {"after_line": 500001},
                            },
                        },
                    ],
                    "deletions": [
                        {
                            "after_source_line": 1,
                            "blob": "c" * 40,
                            "baseline_reference": {"after_line": 1},
                        },
                    ],
                    "replacement_units": [
                        {
                            "presence_lines": ["1-1000000"],
                            "deletion_indices": [0],
                        },
                    ],
                },
                source_object_id="b" * 40,
            ),
        },
        before_worktree_identities={"file.txt": before_identity},
    )

    state = json.loads(get_applied_batch_overlays_file_path().read_text())

    assert state["files"]["file.txt"]["applications"][0][
        "file_metadata"
    ] == {
        "batch_source_commit": "a" * 40,
        "change_type": "modified",
        "presence_claims": [{"source_lines": ["1-1000000"]}],
        "deletions": [
            {
                "after_source_line": 1,
                "blob": "c" * 40,
            },
        ],
    }


def test_recording_reads_previous_batch_revisions_once(temp_git_repo, monkeypatch):
    """Appending many file overlays should use one bulk revision lookup."""
    _record_overlay(temp_git_repo)
    state_path = get_applied_batch_overlays_file_path()
    state = json.loads(state_path.read_text())
    first_entry = state["files"]["file.txt"]
    for file_path in ("second.txt", "third.txt"):
        (temp_git_repo / file_path).write_text("after\n")
        state["files"][file_path] = {
            **first_entry,
            "worktree": asdict(capture_worktree_identity(file_path)),
        }
    state_path.write_text(json.dumps(state))
    before_identities = {
        file_path: capture_worktree_identity(file_path)
        for file_path in ("file.txt", "second.txt", "third.txt")
    }
    revision = read_batch_metadata("saved")["revision"]
    assert isinstance(revision, str)
    calls = []
    real_read = overlays.read_batch_metadata_for_batches

    def read_once(names):
        calls.append(tuple(names))
        return real_read(names)

    monkeypatch.setattr(overlays, "read_batch_metadata_for_batches", read_once)
    provenance = AppliedFileProvenance(
        file_metadata={
            "batch_source_commit": "a" * 40,
            "change_type": "modified",
            "presence_claims": [{"source_lines": ["1"]}],
        },
        source_object_id="b" * 40,
    )

    record_applied_batch_overlays(
        batch_name="saved",
        batch_revision=revision,
        files={file_path: provenance for file_path in before_identities},
        before_worktree_identities=before_identities,
    )

    assert calls == [("saved",)]
