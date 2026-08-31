"""Tests for duplicate-source presence alternative resolution."""

import pytest

from git_stage_batch.batch.ownership.resolved_presence_alternatives import (
    resolve_presence_source_alternatives,
)
from git_stage_batch.core.line_selection import LineRanges


@pytest.mark.parametrize(
    ("claimed_suffix", "expected_count"),
    [
        (b"shared suffix\n", 1),
        (b"different suffix\n", 0),
    ],
)
def test_presence_source_alternative_requires_exact_adjacent_duplicate(
    claimed_suffix,
    expected_count,
):
    """A source gap is an alternative only when its bytes complete the prefix."""
    source_lines = [
        b"\n",
        b"claimed prefix\n",
        b"shared suffix\n",
        b"stale sibling\n",
        claimed_suffix,
    ]

    alternatives = resolve_presence_source_alternatives(
        LineRanges.from_specs(["2,5"]),
        source_lines,
    )

    assert len(alternatives) == expected_count
    if alternatives:
        assert alternatives[0].claimed_ranges == ((2, 2), (5, 5))
        assert alternatives[0].leading_separator_line == 1
