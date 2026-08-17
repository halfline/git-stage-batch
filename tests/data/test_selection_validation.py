"""Tests for review-scoped line-selection validation."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from git_stage_batch.data.file_review.selection_validation import (
    validate_review_scoped_line_selection,
)
from git_stage_batch.exceptions import CommandError


@dataclass(frozen=True)
class _Selection:
    display_ids: tuple[int, ...]
    is_splittable: bool = False


def test_complete_overlapping_groups_form_a_valid_union() -> None:
    """Complete atomic groups may overlap without recursive partitioning."""
    validate_review_scoped_line_selection(
        {1, 2, 3},
        (
            _Selection((1, 2)),
            _Selection((2, 3)),
        ),
    )


def test_partial_atomic_group_reports_its_complete_selection() -> None:
    """An unmatched partial atom still names the whole required group."""
    with pytest.raises(CommandError, match=r"Use: --line 1-3"):
        validate_review_scoped_line_selection(
            {1, 2},
            (_Selection((1, 2, 3)),),
        )


def test_splittable_group_accepts_a_requested_subset() -> None:
    """Splittable review groups contribute only the requested IDs."""
    validate_review_scoped_line_selection(
        {2},
        (_Selection((1, 2, 3), is_splittable=True),),
    )


def test_unmatched_id_is_rejected() -> None:
    """IDs outside every eligible review group remain invalid."""
    with pytest.raises(CommandError, match=r"Line selection #4 is not valid"):
        validate_review_scoped_line_selection(
            {1, 2, 4},
            (_Selection((1, 2)),),
        )
