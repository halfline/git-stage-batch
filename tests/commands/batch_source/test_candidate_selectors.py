"""Tests for batch-source candidate selector helpers."""

from __future__ import annotations

import pytest

import git_stage_batch.commands.batch_source.candidate_selectors as candidate_selectors
from git_stage_batch.exceptions import CommandError


def test_resolve_batch_source_action_selector_accepts_plain_batch_name():
    """Plain batch names should pass through as non-candidate selectors."""
    selector = candidate_selectors.resolve_batch_source_action_selector(
        "cleanup",
        "apply",
        file=None,
    )

    assert selector.batch_name == "cleanup"
    assert selector.candidate_operation is None
    assert selector.candidate_ordinal is None


def test_resolve_batch_source_action_selector_accepts_numbered_candidate():
    """Numbered selectors should pass when the required file scope is present."""
    selector = candidate_selectors.resolve_batch_source_action_selector(
        "cleanup:include:2",
        "include",
        file="notes.txt",
    )

    assert selector.batch_name == "cleanup"
    assert selector.candidate_operation == "include"
    assert selector.candidate_ordinal == 2


def test_resolve_batch_source_action_selector_rejects_preview_set():
    """Bare candidate preview sets should stay preview-only."""
    with pytest.raises(CommandError) as exc_info:
        candidate_selectors.resolve_batch_source_action_selector(
            "cleanup:apply",
            "apply",
            file="notes.txt",
        )

    assert "'cleanup:apply' names the apply candidate preview set." in (
        exc_info.value.message
    )
    assert "git-stage-batch show --from cleanup:apply" in exc_info.value.message
    assert "cleanup:apply:N" in exc_info.value.message


def test_resolve_batch_source_action_selector_requires_file_for_numbered_candidate():
    """Numbered candidate execution should require an explicit file."""
    with pytest.raises(CommandError) as exc_info:
        candidate_selectors.resolve_batch_source_action_selector(
            "cleanup:include:2",
            "include",
            file=None,
        )

    assert (
        "Candidate selector 'cleanup:include:2' requires --file"
        in exc_info.value.message
    )
    assert "No changes applied." in exc_info.value.message


def test_resolve_batch_source_action_selector_rejects_wrong_operation():
    """Action commands should reject candidate selectors for the other action."""
    with pytest.raises(CommandError) as exc_info:
        candidate_selectors.resolve_batch_source_action_selector(
            "cleanup:include:2",
            "apply",
            file="notes.txt",
        )

    assert "'cleanup:include:2' is an include candidate" in exc_info.value.message
    assert "not an apply candidate" in exc_info.value.message
    assert "git-stage-batch show --from cleanup:apply --file notes.txt" in (
        exc_info.value.message
    )
