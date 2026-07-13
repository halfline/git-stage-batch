"""Declarative tests for policy-bearing architecture seams."""

from __future__ import annotations

from .import_boundary_helpers import (
    ForbiddenImportRule,
    forbidden_import_violations,
    internal_import_edges,
    modules_defining,
)


def test_live_change_policy_has_one_owner():
    """Hashing, blocking, and batch ownership policy belongs to candidates."""
    policy_symbols = {
        "prepare_live_change",
        "iter_eligible_live_changes",
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
            "status must count prepared live-change candidates",
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
        "git_stage_batch.data.remaining_hunks",
        "git_stage_batch.commands.selection.next_change_display",
    } <= consumers


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
