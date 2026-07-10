"""Tests for batch-source candidate refusal helpers."""

from __future__ import annotations

import pytest

from git_stage_batch.batch.operation_candidate_types import CandidatePreviewCount
import git_stage_batch.commands.batch_source.candidate_refusals as candidate_refusals
from git_stage_batch.exceptions import CommandError


def _refusal_message(
    *,
    operation: str,
    failed_files: list[str],
    candidate_counts: dict[str, CandidatePreviewCount],
) -> str:
    with pytest.raises(CommandError) as exc_info:
        candidate_refusals.refuse_candidate_conflicts(
            batch_name="cleanup",
            operation=operation,
            failed_files=failed_files,
            candidate_counts=candidate_counts,
        )
    return exc_info.value.message


def test_refuse_candidate_conflicts_reports_single_candidate_limit():
    """A single candidate limit should name the affected file."""
    message = _refusal_message(
        operation="apply",
        failed_files=["notes.txt"],
        candidate_counts={"notes.txt": CandidatePreviewCount(too_many=True)},
    )

    assert (
        "Cannot apply batch 'cleanup': notes.txt has too many apply candidates"
        in message
    )
    assert "Use --line with a narrower selection" in message


def test_refuse_candidate_conflicts_reports_multiple_candidate_limits():
    """Multiple candidate limits should avoid picking one file."""
    message = _refusal_message(
        operation="include",
        failed_files=["a.txt", "b.txt"],
        candidate_counts={
            "a.txt": CandidatePreviewCount(too_many=True),
            "b.txt": CandidatePreviewCount(too_many=True),
        },
    )

    assert "multiple files have too many include candidates" in message
    assert "Use --line with narrower selections" in message


def test_refuse_candidate_conflicts_reports_single_enumeration_error():
    """A single enumeration failure should include the error detail."""
    message = _refusal_message(
        operation="apply",
        failed_files=["notes.txt"],
        candidate_counts={
            "notes.txt": CandidatePreviewCount(error="metadata drift"),
        },
    )

    assert (
        "Cannot enumerate apply candidates for notes.txt: metadata drift"
        in message
    )
    assert "No changes applied." in message


def test_refuse_candidate_conflicts_reports_multiple_enumeration_errors():
    """Multiple enumeration failures should list bounded examples."""
    message = _refusal_message(
        operation="include",
        failed_files=["a.txt", "b.txt", "c.txt", "d.txt"],
        candidate_counts={
            "a.txt": CandidatePreviewCount(error="first"),
            "b.txt": CandidatePreviewCount(error="second"),
            "c.txt": CandidatePreviewCount(error="third"),
            "d.txt": CandidatePreviewCount(error="fourth"),
        },
    )

    assert "Cannot enumerate include candidates for multiple files." in message
    assert "  a.txt: first" in message
    assert "  c.txt: third" in message
    assert "  d.txt: fourth" not in message


def test_refuse_candidate_conflicts_reports_single_ambiguous_file():
    """A single ambiguous file should point to preview and command execution."""
    message = _refusal_message(
        operation="include",
        failed_files=["notes.txt"],
        candidate_counts={"notes.txt": CandidatePreviewCount(count=2)},
    )

    assert "notes.txt has 2 include candidates" in message
    assert "git-stage-batch show --from cleanup:include --file notes.txt" in message
    assert (
        "git-stage-batch include --from cleanup:include:N --file notes.txt"
        in message
    )
    assert "Include a reviewed candidate:" in message


def test_refuse_candidate_conflicts_reports_multiple_ambiguous_files():
    """Multiple ambiguous files should list preview commands."""
    message = _refusal_message(
        operation="apply",
        failed_files=["a.txt", "b.txt"],
        candidate_counts={
            "a.txt": CandidatePreviewCount(count=2),
            "b.txt": CandidatePreviewCount(count=3),
        },
    )

    assert "multiple files need apply decisions" in message
    assert "git-stage-batch show --from cleanup:apply --file a.txt" in message
    assert "git-stage-batch show --from cleanup:apply --file b.txt" in message


def test_refuse_candidate_conflicts_returns_without_candidate_details():
    """Generic merge failures should fall through to command-specific errors."""
    candidate_refusals.refuse_candidate_conflicts(
        batch_name="cleanup",
        operation="apply",
        failed_files=["notes.txt"],
        candidate_counts={},
    )
