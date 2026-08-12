"""Focused tests for replacement-origin placement choices."""

import re

import pytest

from git_stage_batch.batch.merge import baseline_replacement_choices
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.replacement_units import (
    ReplacementUnit,
    ReplacementUnitOrigin,
)
from git_stage_batch.core.line_selection import LineRanges


@pytest.mark.parametrize(
    ("ambiguity_key", "expected"),
    [
        ("replacement-origin:0:delete:0:identity", 0),
        ("replacement-origin:42:delete:1:identity", 42),
        ("replacement-origin::delete:0:identity", None),
        ("replacement-origin:-1:delete:0:identity", None),
        ("replacement-origin:1", None),
        ("presence:1", None),
        (None, None),
    ],
)
def test_replacement_origin_unit_index_parses_only_generated_key_shape(
    ambiguity_key,
    expected,
) -> None:
    """Resolution preflight should scope only well-formed origin decisions."""
    assert (
        baseline_replacement_choices.replacement_origin_unit_index(ambiguity_key)
        == expected
    )


@pytest.mark.parametrize("max_results", [-1, 0, True, 1.0, None])
def test_replacement_origin_choices_require_positive_integer_limit(
    max_results,
) -> None:
    """Replacement review should reject invalid collection bounds."""
    with pytest.raises(ValueError, match="max_results must be positive"):
        baseline_replacement_choices.replacement_origin_choices_for_unit(
            AbsenceClaim(anchor_line=None, content_lines=[]),
            0,
            object(),
            LineRanges.empty(),
            [],
            max_results=max_results,
        )


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
        assert expected is normalized_content
        return True

    normalized_content = (b"old value\n",)
    monkeypatch.setattr(
        baseline_replacement_choices,
        "normalize_line_sequence_endings",
        lambda _lines: normalized_content,
    )
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
        max_results=2,
    )

    assert key is not None
    assert len(choices) == 1


def test_replacement_origin_key_keeps_claimed_ranges_compact() -> None:
    """Replacement review keys should describe ranges without listing lines."""
    claim = AbsenceClaim(
        anchor_line=None,
        content_lines=[b"old value\n"],
    )
    unit = ReplacementUnit(
        presence_lines=["1-1000"],
        deletion_indices=[0],
        origin=ReplacementUnitOrigin(
            old_start=1,
            old_end=1,
            new_start=1,
            new_end=1000,
        ),
    )

    key, choices = baseline_replacement_choices.replacement_origin_choices_for_unit(
        claim,
        0,
        unit,
        LineRanges.from_ranges(((1, 1000),)),
        [b"old value\n"],
        max_results=2,
    )

    assert key is not None
    assert re.fullmatch(
        r"replacement-origin:0:delete:0:"
        r"claimed:1-1000:1:[0-9a-f]{12}:"
        r"old:1-1:new:1-1000:[0-9a-f]{12}",
        key,
    )
    assert len(choices) == 1


def test_replacement_origin_key_hashes_fragmented_ranges() -> None:
    """Replacement review keys should stay fixed-size for fragmented claims."""
    claim = AbsenceClaim(
        anchor_line=None,
        content_lines=[b"old value\n"],
    )
    unit = ReplacementUnit(
        presence_lines=["1"],
        deletion_indices=[0],
        origin=ReplacementUnitOrigin(
            old_start=1,
            old_end=1,
            new_start=1,
            new_end=1999,
        ),
    )

    def fragmented_ranges():
        return ((line, line) for line in range(1, 2000, 2))

    key, choices = baseline_replacement_choices.replacement_origin_choices_for_unit(
        claim,
        0,
        unit,
        fragmented_ranges(),
        [b"old value\n"],
        max_results=2,
    )
    canonical_key, _canonical_choices = (
        baseline_replacement_choices.replacement_origin_choices_for_unit(
            claim,
            0,
            unit,
            LineRanges.from_ranges(fragmented_ranges()),
            [b"old value\n"],
            max_results=2,
        )
    )

    assert key is not None
    assert canonical_key == key
    assert re.fullmatch(
        r"replacement-origin:0:delete:0:"
        r"claimed:1-1999:1000:[0-9a-f]{12}:"
        r"old:1-1:new:1-1999:[0-9a-f]{12}",
        key,
    )
    assert len(key) < 150
    assert len(choices) == 1


def test_replacement_origin_key_accepts_unbounded_line_ids() -> None:
    """Replacement review keys should preserve the line-selection domain."""
    line = 2**64
    claim = AbsenceClaim(
        anchor_line=None,
        content_lines=[b"old value\n"],
    )
    unit = ReplacementUnit(
        presence_lines=[str(line)],
        deletion_indices=[0],
        origin=ReplacementUnitOrigin(
            old_start=1,
            old_end=1,
            new_start=line,
            new_end=line,
        ),
    )

    key, choices = baseline_replacement_choices.replacement_origin_choices_for_unit(
        claim,
        0,
        unit,
        LineRanges.from_ranges(((line, line),)),
        [b"old value\n"],
        max_results=2,
    )

    assert key is not None
    assert f"claimed:{line}-{line}:1:" in key
    assert len(choices) == 1
