"""Tests for shared batch selection helpers."""

from __future__ import annotations

import pytest

from git_stage_batch.batch.selection import (
    require_display_ids_available,
    require_single_file_context_for_line_selection_ranges,
)
from git_stage_batch.core.line_selection import LineRanges
from git_stage_batch.exceptions import CommandError


def test_require_display_ids_available_accepts_range_selections():
    """Availability validation should compare display ID ranges directly."""
    require_display_ids_available(
        LineRanges.from_ranges([(2, 3)]),
        LineRanges.from_ranges([(1, 4)]),
        line_id_specification="2-3",
        file_path="test.py",
    )


def test_require_display_ids_available_rejects_missing_range_ids():
    """Unavailable range-selected display IDs should still be rejected."""
    with pytest.raises(CommandError):
        require_display_ids_available(
            LineRanges.from_ranges([(2, 5)]),
            LineRanges.from_ranges([(1, 4)]),
            line_id_specification="2-5",
            file_path="test.py",
        )


def test_require_single_file_context_can_parse_line_ranges():
    """Single-file line parsing should preserve contiguous ranges."""
    selected_ids = require_single_file_context_for_line_selection_ranges(
        "mybatch",
        {"test.py": {}},
        "1-100000",
        "reset",
    )

    assert selected_ids == LineRanges.from_ranges([(1, 100000)])
