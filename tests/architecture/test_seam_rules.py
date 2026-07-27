"""Declarative tests for policy-bearing architecture seams."""

from __future__ import annotations

from .import_boundary_helpers import (
    ForbiddenImportRule,
    forbidden_import_violations,
    internal_import_edges,
    internal_module_import_edges,
    modules_defining,
)


def test_live_change_policy_has_one_owner():
    """Hashing, blocking, and batch ownership policy belongs to candidates."""
    policy_symbols = {
        "prepare_live_change",
        "stream_eligible_live_changes",
    }
    assert modules_defining(policy_symbols) == {
        "git_stage_batch.data.live_change_candidates": policy_symbols,
    }

    rules = (
        ForbiddenImportRule(
            "git_stage_batch.data.hunk_tracking",
            "git_stage_batch.core.hashing",
            "navigation must consume prepared live-change candidates",
        ),
        ForbiddenImportRule(
            "git_stage_batch.data.remaining_hunks",
            "git_stage_batch.core.hashing",
            "status must count canonical file-job results",
        ),
        ForbiddenImportRule(
            "git_stage_batch.data.remaining_hunks",
            "git_stage_batch.data.live_change_candidates",
            "status must not fall back to the lazy candidate stream",
        ),
        ForbiddenImportRule(
            "git_stage_batch.commands.selection.next_change_display",
            "git_stage_batch.core.hashing",
            "display must render prepared live-change candidates",
        ),
        ForbiddenImportRule(
            "git_stage_batch.data.hunk_tracking",
            "git_stage_batch.data.selected_change.hunk_filtering",
            "navigation must not recreate batch-ownership filtering",
        ),
    )
    assert forbidden_import_violations(rules) == []

    consumers = {
        edge.source
        for edge in internal_import_edges()
        if edge.target == "git_stage_batch.data.live_change_candidates"
    }
    assert {
        "git_stage_batch.data.hunk_tracking",
        "git_stage_batch.data.live_change_jobs",
        "git_stage_batch.commands.selection.next_change_display",
    } <= consumers
    assert "git_stage_batch.data.remaining_hunks" not in consumers


def test_repository_readers_stay_below_policy_layers():
    """Repository readers must not depend on command or session policy."""
    rules = (
        ForbiddenImportRule(
            "git_stage_batch.utils.repository_buffers",
            "git_stage_batch.commands",
            "repository readers are lower-level infrastructure",
        ),
        ForbiddenImportRule(
            "git_stage_batch.utils.repository_buffers",
            "git_stage_batch.data",
            "repository readers cannot depend on session state",
        ),
    )
    assert forbidden_import_violations(rules) == []


def test_file_review_state_policy_stays_below_output():
    """Persisted review policy must not depend on terminal presentation."""
    policy_symbols = {"resolve_default_review_pages"}
    assert modules_defining(policy_symbols) == {
        "git_stage_batch.data.file_review.state_builder": policy_symbols,
    }

    rules = (
        ForbiddenImportRule(
            "git_stage_batch.data.file_review",
            "git_stage_batch.output",
            "review data and policy cannot depend on terminal presentation",
        ),
    )
    assert forbidden_import_violations(rules) == []


def test_atomic_buffer_publication_stays_out_of_core():
    """Core buffers describe bytes; filesystem publication belongs to utils."""
    publication_symbols = {
        "write_buffer_to_path",
        "write_buffer_to_working_tree_path",
    }
    assert modules_defining(publication_symbols) == {
        "git_stage_batch.utils.buffer_io": publication_symbols,
    }

    atomic_symbols = {
        "fsync_directory",
        "replace_symlink_atomically",
        "write_chunks_atomically",
    }
    assert modules_defining(atomic_symbols) == {
        "git_stage_batch.utils.atomic_write": atomic_symbols,
    }


def test_undo_checkpoint_orchestration_delegates_state_policy():
    """Stack orchestration must not absorb snapshot or restore policy."""
    snapshot_symbols = {
        "filesystem_directory_state",
        "push_redo_node",
        "snapshot_current_state",
        "write_snapshot_commit",
    }
    snapshot_module = "git_stage_batch.data.undo.snapshots"
    assert modules_defining(snapshot_symbols) == {
        snapshot_module: snapshot_symbols,
    }

    state_symbols = {
        "detect_redo_conflicts",
        "detect_undo_conflicts",
        "restore_checkpoint_state",
    }
    state_module = "git_stage_batch.data.undo.state"
    assert modules_defining(state_symbols) == {
        state_module: state_symbols,
    }

    rules = (
        ForbiddenImportRule(
            snapshot_module,
            "git_stage_batch.data.undo.checkpoints",
            "snapshot storage must stay below stack orchestration",
        ),
        ForbiddenImportRule(
            state_module,
            "git_stage_batch.data.undo.checkpoints",
            "checkpoint state policy must stay below stack orchestration",
        ),
    )
    assert forbidden_import_violations(rules) == []

    consumers = {
        edge.source
        for edge in internal_module_import_edges()
        if edge.target in {snapshot_module, state_module}
    }
    assert consumers == {
        "git_stage_batch.data.undo.checkpoints",
        "git_stage_batch.data.undo.state",
    }


def test_undo_subpackage_hides_internal_policy_modules():
    """Undo consumers use checkpoints or refs, not internal policy modules."""
    implementation_prefix = "git_stage_batch.data.undo."
    public_modules = {
        "git_stage_batch.data.undo.checkpoints",
        "git_stage_batch.data.undo.refs",
    }
    violations = [
        f"{edge.source}:{edge.line} -> {edge.target}"
        for edge in internal_module_import_edges()
        if edge.target.startswith(implementation_prefix)
        and not edge.source.startswith(implementation_prefix)
        and edge.target not in public_modules
    ]
    assert violations == []


def test_tui_shell_boundary_does_not_own_repository_locking():
    """Arbitrary shell children run outside repository action locks."""
    rules = (
        ForbiddenImportRule(
            "git_stage_batch.tui.shell_command",
            "git_stage_batch.utils.session_lock",
            "shell waits must never hold the repository lock",
        ),
    )
    assert forbidden_import_violations(rules) == []
