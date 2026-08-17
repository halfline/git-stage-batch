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
from git_stage_batch.data.file_target_identity import (
    IndexIdentity,
    capture_worktree_identity,
)
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


def _record_overlay(
    repo,
    *,
    introduced_selected_presence: bool = False,
    index_preimage_source_ranges: tuple[tuple[int, int], ...] = (),
    expected_index_identities: dict[str, IndexIdentity] | None = None,
) -> None:
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
                introduced_selected_presence=introduced_selected_presence,
                index_preimage_source_ranges=index_preimage_source_ranges,
            ),
        },
        before_worktree_identities={"file.txt": before_identity},
        expected_index_identities=expected_index_identities,
    )


def test_fresh_overlay_requires_exact_repository_and_batch_identity(temp_git_repo):
    """Exact worktree state should reveal provenance until its batch changes."""
    _record_overlay(temp_git_repo)

    view = fresh_applied_batch_overlay_for_path("file.txt")

    assert view.batch_names == frozenset({"saved"})
    assert view.revealed_owner_names
    owner = next(iter(view.revealed_owner_names))
    assert view.source_object_by_owner[owner] == "b" * 40
    assert view.applied_source_line_ranges_by_batch == {"saved": ((1, 1),)}
    assert view.source_line_ranges_by_batch == {}
    state = json.loads(get_applied_batch_overlays_file_path().read_text())
    assert (
        state["files"]["file.txt"]["applications"][0]["index_target_is_original"]
        is True
    )

    update_batch_note("saved", "new revision")

    assert not fresh_applied_batch_overlay_for_path("file.txt").revealed_owner_names


def test_fresh_overlay_proves_only_exact_applied_file_provenance(temp_git_repo):
    """A reapply no-op needs exact ownership and source-blob authority."""
    _record_overlay(temp_git_repo)
    view = fresh_applied_batch_overlay_for_path("file.txt")
    applied_metadata = {
        "batch_source_commit": "a" * 40,
        "change_type": "modified",
        "presence_claims": [{"source_lines": ["1"]}],
    }

    assert view.contains_equivalent_file_provenance(
        "file.txt",
        applied_metadata,
        "b" * 40,
    )
    assert not view.contains_equivalent_file_provenance(
        "file.txt",
        applied_metadata,
        "c" * 40,
    )
    assert not view.contains_equivalent_file_provenance(
        "file.txt",
        {
            **applied_metadata,
            "presence_claims": [{"source_lines": ["2"]}],
        },
        "b" * 40,
    )


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

    assert fresh_applied_batch_overlay_for_path("untracked.txt").revealed_owner_names


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


def test_overlay_never_treats_unmerged_index_as_absent(temp_git_repo):
    """Conflict state must not gain or later reactivate absent-index authority."""
    file_path = temp_git_repo / "untracked.txt"
    file_path.write_text("new\n")
    object_ids = []
    for content in ("base\n", "ours\n", "theirs\n"):
        object_ids.append(
            subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                input=content,
                cwd=temp_git_repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    subprocess.run(
        ["git", "update-index", "--index-info"],
        input="".join(
            f"100644 {object_id} {stage}\tuntracked.txt\n"
            for stage, object_id in enumerate(object_ids, start=1)
        ),
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
        text=True,
    )
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

    state = json.loads(get_applied_batch_overlays_file_path().read_text())
    assert "untracked.txt" not in state["files"]
    assert not fresh_applied_batch_overlay_for_path(
        "untracked.txt"
    ).revealed_owner_names

    subprocess.run(
        ["git", "update-index", "--force-remove", "--", "untracked.txt"],
        cwd=temp_git_repo,
        check=True,
        capture_output=True,
    )

    assert not fresh_applied_batch_overlay_for_path(
        "untracked.txt"
    ).revealed_owner_names


def test_session_allows_index_drift_only_after_fresh_start(temp_git_repo):
    """A pre-start index change must not acquire the session exemption."""
    _record_overlay(temp_git_repo)
    subprocess.run(["git", "add", "file.txt"], check=True, capture_output=True)

    initialize_abort_state()

    assert not fresh_applied_batch_overlay_for_path("file.txt").revealed_owner_names


def test_session_rebinds_fresh_overlay_to_final_index(
    temp_git_repo,
    monkeypatch,
):
    """Session staging should preserve and then rebind fresh provenance."""
    _record_overlay(temp_git_repo)
    initialize_abort_state()
    subprocess.run(["git", "add", "file.txt"], check=True, capture_output=True)

    assert fresh_applied_batch_overlay_for_path("file.txt").revealed_owner_names

    rebind_applied_batch_overlays_after_session()
    monkeypatch.setattr(overlays, "session_is_active", lambda: False)

    assert fresh_applied_batch_overlay_for_path("file.txt").revealed_owner_names


def test_session_index_drift_disables_index_preimage_authority(
    temp_git_repo,
    monkeypatch,
):
    """Session freshness cannot turn a changed index into an apply preimage."""
    _record_overlay(
        temp_git_repo,
        introduced_selected_presence=True,
        index_preimage_source_ranges=((1, 1),),
    )
    initialize_abort_state()
    subprocess.run(["git", "add", "file.txt"], check=True, capture_output=True)

    view = fresh_applied_batch_overlay_for_path("file.txt")

    assert view.applied_source_line_ranges_by_batch == {}
    assert view.source_line_ranges_by_batch == {"saved": ((1, 1),)}
    assert view.index_preimage_source_line_ranges_by_batch == {}

    rebind_applied_batch_overlays_after_session()
    monkeypatch.setattr(overlays, "session_is_active", lambda: False)

    rebound_view = fresh_applied_batch_overlay_for_path("file.txt")
    assert rebound_view.revealed_owner_names
    assert rebound_view.applied_source_line_ranges_by_batch == {}
    assert rebound_view.index_preimage_source_line_ranges_by_batch == {}


def test_recording_omits_preimage_when_planned_index_went_stale(
    temp_git_repo,
):
    """A late index change must not acquire exact-preimage authority."""
    _record_overlay(
        temp_git_repo,
        introduced_selected_presence=True,
        index_preimage_source_ranges=((1, 1),),
        expected_index_identities={
            "file.txt": IndexIdentity("100644", "f" * 40),
        },
    )

    state = json.loads(get_applied_batch_overlays_file_path().read_text())
    application = state["files"]["file.txt"]["applications"][0]
    assert "index_target_is_original" not in application
    assert "index_preimage_source_lines" not in application
    assert (
        fresh_applied_batch_overlay_for_path(
            "file.txt"
        ).index_preimage_source_line_ranges_by_batch
        == {}
    )


def test_exact_preimage_authorizes_its_range_in_a_mixed_selection(
    temp_git_repo,
):
    """A replacement preimage remains usable when another claim preexisted."""
    _record_overlay(
        temp_git_repo,
        introduced_selected_presence=False,
        index_preimage_source_ranges=((1, 1),),
    )

    state = json.loads(get_applied_batch_overlays_file_path().read_text())
    application = state["files"]["file.txt"]["applications"][0]
    assert "introduced_selected_presence" not in application
    assert application["index_preimage_source_lines"] == ["1"]

    view = fresh_applied_batch_overlay_for_path("file.txt")
    assert view.source_line_ranges_by_batch == {"saved": ((1, 1),)}
    assert view.index_preimage_source_line_ranges_by_batch == {"saved": ((1, 1),)}


def test_reapplying_identical_ownership_preserves_one_strongest_record(
    temp_git_repo,
):
    """A no-op reapply must not duplicate or weaken fresh provenance."""
    _record_overlay(
        temp_git_repo,
        introduced_selected_presence=True,
        index_preimage_source_ranges=((1, 1),),
    )
    _record_overlay(temp_git_repo)

    state = json.loads(get_applied_batch_overlays_file_path().read_text())
    applications = state["files"]["file.txt"]["applications"]
    assert len(applications) == 1
    assert applications[0]["introduced_selected_presence"] is True
    assert applications[0]["index_target_is_original"] is True
    assert applications[0]["index_preimage_source_lines"] == ["1"]


def test_overlay_state_rejects_unknown_or_malformed_fields(temp_git_repo):
    """Advisory state must fail closed instead of accepting ambiguous data."""
    _record_overlay(temp_git_repo)
    state_path = get_applied_batch_overlays_file_path()
    state = json.loads(state_path.read_text())
    state["files"]["file.txt"]["unexpected"] = True
    state_path.write_text(json.dumps(state))

    with pytest.raises(CommandError, match="Applied-batch state is corrupt"):
        load_applied_batch_overlay_snapshot()


def test_overlay_without_original_index_marker_withholds_preexisting_proof(
    temp_git_repo,
):
    """Older advisory entries must not treat a rebound index as apply input."""
    _record_overlay(temp_git_repo)
    state_path = get_applied_batch_overlays_file_path()
    state = json.loads(state_path.read_text())
    application = state["files"]["file.txt"]["applications"][0]
    application.pop("index_target_is_original")
    state_path.write_text(json.dumps(state))

    view = fresh_applied_batch_overlay_for_path("file.txt")

    assert view.revealed_owner_names
    assert view.applied_source_line_ranges_by_batch == {}


def test_overlay_rejects_preimage_outside_selected_presence(temp_git_repo):
    """Index-preimage authority cannot exceed the recorded application."""
    _record_overlay(temp_git_repo)
    state_path = get_applied_batch_overlays_file_path()
    state = json.loads(state_path.read_text())
    application = state["files"]["file.txt"]["applications"][0]
    application["introduced_selected_presence"] = True
    application["index_preimage_source_lines"] = ["2"]
    state_path.write_text(json.dumps(state))

    with pytest.raises(CommandError, match="Applied-batch state is corrupt"):
        load_applied_batch_overlay_snapshot()


def test_legacy_preimage_without_original_index_marker_is_fail_closed(
    temp_git_repo,
):
    """Legacy unbound preimages must load without granting index authority."""
    _record_overlay(
        temp_git_repo,
        index_preimage_source_ranges=((1, 1),),
    )
    state_path = get_applied_batch_overlays_file_path()
    state = json.loads(state_path.read_text())
    application = state["files"]["file.txt"]["applications"][0]
    application.pop("index_target_is_original")
    state_path.write_text(json.dumps(state))

    snapshot = load_applied_batch_overlay_snapshot()
    loaded_application = snapshot.state["files"]["file.txt"]["applications"][0]
    view = fresh_applied_batch_overlay_for_path(
        "file.txt",
        snapshot=snapshot,
    )

    assert "index_preimage_source_lines" not in loaded_application
    assert view.revealed_owner_names
    assert view.source_line_ranges_by_batch == {}
    assert view.index_preimage_source_line_ranges_by_batch == {}


def test_reapplying_legacy_unbound_preimage_does_not_promote_it(
    temp_git_repo,
):
    """Fresh identity proof must not become attached to an old unbound range."""
    _record_overlay(
        temp_git_repo,
        index_preimage_source_ranges=((1, 1),),
    )
    state_path = get_applied_batch_overlays_file_path()
    state = json.loads(state_path.read_text())
    application = state["files"]["file.txt"]["applications"][0]
    application.pop("index_target_is_original")
    state_path.write_text(json.dumps(state))

    _record_overlay(temp_git_repo)

    rewritten_state = json.loads(state_path.read_text())
    rewritten_application = rewritten_state["files"]["file.txt"]["applications"][0]
    assert rewritten_application["index_target_is_original"] is True
    assert "index_preimage_source_lines" not in rewritten_application
    assert (
        fresh_applied_batch_overlay_for_path(
            "file.txt"
        ).index_preimage_source_line_ranges_by_batch
        == {}
    )


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

    assert state["files"]["file.txt"]["applications"][0]["file_metadata"] == {
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


def test_recording_excludes_partly_preexisting_selected_presence(temp_git_repo):
    """Discard authorization must exclude a partly preexisting selection."""
    revision = read_batch_metadata("saved")["revision"]
    assert isinstance(revision, str)
    source_object_id = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        input="existing\nintroduced\n",
        text=True,
        check=True,
        capture_output=True,
    ).stdout.strip()
    before_identity = capture_worktree_identity("file.txt")
    before_lines = overlays.LineBuffer.from_bytes(b"existing\n")
    after_lines = overlays.LineBuffer.from_bytes(b"existing\nintroduced\n")
    try:
        provenance = overlays.AppliedFileProvenance(
            file_metadata={
                "batch_source_commit": "a" * 40,
                "change_type": "modified",
                "presence_claims": [{"source_lines": ["1-2"]}],
            },
            source_object_id=source_object_id,
            introduced_selected_presence=(
                overlays._all_selected_presence_introduced(
                    {
                        "presence_claims": [{"source_lines": ["1-2"]}],
                    },
                    source_object_id,
                    before_lines,
                    after_lines,
                )
            ),
        )
    finally:
        before_lines.close()
        after_lines.close()
    (temp_git_repo / "file.txt").write_text("existing\nintroduced\n")
    record_applied_batch_overlays(
        batch_name="saved",
        batch_revision=revision,
        files={"file.txt": provenance},
        before_worktree_identities={"file.txt": before_identity},
    )

    view = fresh_applied_batch_overlay_for_path("file.txt")

    assert view.source_line_ranges_by_batch == {}


def test_recording_exposes_fully_introduced_selected_presence(temp_git_repo):
    """A reviewed apply may authorize its entirely new selected range."""
    revision = read_batch_metadata("saved")["revision"]
    assert isinstance(revision, str)
    source_object_id = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        input="introduced\n",
        text=True,
        check=True,
        capture_output=True,
    ).stdout.strip()
    before_lines = overlays.LineBuffer.from_bytes(b"before\n")
    after_lines = overlays.LineBuffer.from_bytes(b"introduced\nbefore\n")
    try:
        provenance = AppliedFileProvenance(
            file_metadata={
                "batch_source_commit": "a" * 40,
                "change_type": "modified",
                "presence_claims": [{"source_lines": ["1"]}],
            },
            source_object_id=source_object_id,
            introduced_selected_presence=(
                overlays._all_selected_presence_introduced(
                    {"presence_claims": [{"source_lines": ["1"]}]},
                    source_object_id,
                    before_lines,
                    after_lines,
                )
            ),
            index_preimage_source_ranges=((1, 1),),
        )
    finally:
        before_lines.close()
        after_lines.close()
    before_identity = capture_worktree_identity("file.txt")
    (temp_git_repo / "file.txt").write_text("introduced\nbefore\n")
    record_applied_batch_overlays(
        batch_name="saved",
        batch_revision=revision,
        files={"file.txt": provenance},
        before_worktree_identities={"file.txt": before_identity},
    )

    state = json.loads(get_applied_batch_overlays_file_path().read_text())
    application = state["files"]["file.txt"]["applications"][0]

    assert application["introduced_selected_presence"] is True
    assert application["index_preimage_source_lines"] == ["1"]
    view = fresh_applied_batch_overlay_for_path("file.txt")
    assert view.source_line_ranges_by_batch == {"saved": ((1, 1),)}
    assert view.index_preimage_source_line_ranges_by_batch == {"saved": ((1, 1),)}


def test_selected_presence_equal_to_preexisting_content_is_not_introduced(
    temp_git_repo,
):
    """LCS placement cannot authorize content that was already in the file."""
    source_object_id = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        input="duplicate\n",
        text=True,
        check=True,
        capture_output=True,
    ).stdout.strip()
    before_lines = overlays.LineBuffer.from_bytes(b"duplicate\nkeep\n")
    after_lines = overlays.LineBuffer.from_bytes(b"keep\nduplicate\n")
    try:
        assert not overlays._all_selected_presence_introduced(
            {"presence_claims": [{"source_lines": ["1"]}]},
            source_object_id,
            before_lines,
            after_lines,
        )
    finally:
        before_lines.close()
        after_lines.close()


def test_new_selected_occurrence_may_duplicate_preexisting_content(
    temp_git_repo,
):
    """An exact newly added occurrence remains attributable by position."""
    source_object_id = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        input="duplicate\n",
        text=True,
        check=True,
        capture_output=True,
    ).stdout.strip()
    before_lines = overlays.LineBuffer.from_bytes(b"duplicate\nkeep\n")
    after_lines = overlays.LineBuffer.from_bytes(b"duplicate\nkeep\nduplicate\n")
    try:
        assert overlays._all_selected_presence_introduced(
            {"presence_claims": [{"source_lines": ["1"]}]},
            source_object_id,
            before_lines,
            after_lines,
        )
    finally:
        before_lines.close()
        after_lines.close()


def test_reordered_selected_ranges_are_not_discard_authorized(temp_git_repo):
    """Trusted anchors must remain collectively ordered across source ranges."""
    source_object_id = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        input="first\nunselected\nlast\n",
        text=True,
        check=True,
        capture_output=True,
    ).stdout.strip()
    before_lines = overlays.LineBuffer.from_bytes(b"before\n")
    after_lines = overlays.LineBuffer.from_bytes(b"last\nbefore\nfirst\n")
    try:
        assert not overlays._all_selected_presence_introduced(
            {"presence_claims": [{"source_lines": ["1,3"]}]},
            source_object_id,
            before_lines,
            after_lines,
        )
    finally:
        before_lines.close()
        after_lines.close()


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
