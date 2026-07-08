"""Tests for batch-source merge refusal helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import git_stage_batch.commands.batch_source.merge_refusals as merge_refusals
from git_stage_batch.exceptions import CommandError


def _refusal_message(failed_files: list[str]) -> str:
    with pytest.raises(CommandError) as exc_info:
        merge_refusals.refuse_batch_source_merge_failures(
            batch_name="cleanup",
            failed_files=failed_files,
        )
    return exc_info.value.message


def test_refuse_batch_source_merge_failures_suggests_line_selection(
    monkeypatch,
):
    """Single-file merge failures should mention --lines when lines map."""
    monkeypatch.setattr(
        merge_refusals,
        "render_batch_file_display",
        lambda batch_name, file_path: SimpleNamespace(gutter_to_selection_id={1: 10}),
    )

    message = _refusal_message(["notes.txt"])

    assert "cleanup' contains changes to notes.txt" in message
    assert "or use '--lines' to apply only specific changes." in message


def test_refuse_batch_source_merge_failures_omits_line_hint_without_mapping(
    monkeypatch,
):
    """Single-file merge failures should omit --lines when no lines map."""
    monkeypatch.setattr(
        merge_refusals,
        "render_batch_file_display",
        lambda batch_name, file_path: None,
    )

    message = _refusal_message(["notes.txt"])

    assert "cleanup' contains changes to notes.txt" in message
    assert "or use '--lines'" not in message


def test_refuse_batch_source_merge_failures_reports_multiple_files(monkeypatch):
    """Multi-file merge failures should list the failed files."""
    monkeypatch.setattr(
        merge_refusals,
        "render_batch_file_display",
        lambda batch_name, file_path: pytest.fail("should not render files"),
    )

    message = _refusal_message(["a.txt", "b.txt"])

    assert "one or more files" in message
    assert "Failed for: a.txt, b.txt." in message
    assert "or use '--lines' to apply only specific changes." in message
