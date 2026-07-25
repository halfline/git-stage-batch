"""Focused tests for replacement-origin placement choices."""

from git_stage_batch.batch.merge import baseline_replacement_choices
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.replacement_units import (
    ReplacementUnit,
    ReplacementUnitOrigin,
)
from git_stage_batch.core.line_selection import LineRanges


def test_replacement_origin_choices_normalize_content_without_a_list(
    monkeypatch,
) -> None:
    """Replacement review should expose a normalized sequence view."""
    claim = AbsenceClaim(
        anchor_line=None,
        content_lines=[b"old value\r\n"],
    )
    unit = ReplacementUnit(
        presence_lines=["1"],
        deletion_indices=[0],
        origin=ReplacementUnitOrigin(
            old_start=1,
            old_end=1,
            new_start=1,
            new_end=1,
        ),
    )

    def match_normalized_view(
        _working_lines,
        _position,
        expected,
    ) -> bool:
        assert not isinstance(expected, list)
        assert expected[0] == b"old value\n"
        return True

    monkeypatch.setattr(
        baseline_replacement_choices,
        "_line_slice_matches",
        match_normalized_view,
    )

    key, choices = baseline_replacement_choices.replacement_origin_choices_for_unit(
        claim,
        0,
        unit,
        LineRanges.from_ranges(((1, 1),)),
        [b"old value\n"],
    )

    assert key is not None
    assert len(choices) == 1
