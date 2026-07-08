"""Tests for status prompt rendering."""

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.output.status_prompt import (
    DEFAULT_PROMPT_FORMAT,
    prompt_needs_status_summary,
    render_prompt_status,
)


def _summary() -> dict:
    return {
        "session": {
            "active": True,
            "iteration": 3,
            "status": "in_progress",
            "in_progress": True,
        },
        "selected_change": {
            "change_type": "modified",
            "file": "README.md",
            "ids": [1, 2, 3],
            "kind": "hunk",
            "line": 12,
        },
        "file_review": {
            "batch_name": "review",
            "fresh": True,
            "source": "live",
        },
        "progress": {
            "included": 2,
            "skipped": 1,
            "discarded": 1,
            "remaining": 4,
        },
    }


def test_default_prompt_format_is_static_label():
    """Default prompt format should be the short active label."""
    assert DEFAULT_PROMPT_FORMAT == "STAGING"
    assert not prompt_needs_status_summary(DEFAULT_PROMPT_FORMAT)
    assert render_prompt_status(DEFAULT_PROMPT_FORMAT) == "STAGING"


def test_render_prompt_status_uses_summary_fields():
    """Prompt rendering should expose status, progress, and selection fields."""
    rendered = render_prompt_status(
        (
            "{status}:{progress_status}:{progress_label}:"
            "{processed}/{total}:{selected_file}:{selected_ids}:"
            "{file_review_batch}:{file_review_source}:{file_review_fresh}"
        ),
        _summary(),
    )

    assert rendered == "STAGING:in_progress:in progress:4/8:README.md:1-3:review:live:True"


def test_prompt_needs_status_summary_ignores_active_only_formats():
    """Only the active field should render without a status summary."""
    assert not prompt_needs_status_summary("{active}")
    assert prompt_needs_status_summary("{status}")


def test_render_prompt_status_rejects_unknown_fields():
    """Unknown prompt fields should report the bad field name."""
    with pytest.raises(CommandError, match="Unknown status prompt field 'missing'"):
        render_prompt_status("{missing}", _summary())


def test_render_prompt_status_rejects_positional_fields():
    """Prompt formats should use named fields."""
    with pytest.raises(CommandError, match="cannot use positional fields"):
        render_prompt_status("{}", _summary())
