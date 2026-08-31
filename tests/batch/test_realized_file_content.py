"""Tests for realized file content construction."""

from git_stage_batch.batch.realized_file_content import (
    _ownership_for_realization,
    build_realized_buffer_from_lines,
)
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.replacement_units import (
    LegacyReplacementUnitOrigin,
    ReplacementUnit,
    ReplacementUnitOrigin,
)


def test_ownership_for_realization_preserves_origin_evidence_tier():
    """Source-alternative filtering must not promote legacy evidence to proven."""
    origin = ReplacementUnitOrigin(old_start=1, old_end=1, new_start=1, new_end=1)
    legacy_evidence = LegacyReplacementUnitOrigin(origin)
    unit = ReplacementUnit(
        presence_lines=["1"],
        deletion_indices=[0, 1],
        origin_evidence=legacy_evidence,
    )
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [
            AbsenceClaim(anchor_line=None, content_lines=[b"old\n"]),
            AbsenceClaim(
                anchor_line=None,
                content_lines=[b"alt\n"],
                source_alternative=True,
            ),
        ],
        replacement_units=[unit],
    )

    result = _ownership_for_realization(ownership)

    result_unit = result.replacement_units[0]
    assert isinstance(result_unit.origin_evidence, LegacyReplacementUnitOrigin)
    assert result_unit.origin_evidence.value is origin


def test_realization_omits_multiple_live_replacement_alternatives():
    """Source-wide claims must not reclaim any explicit live alternative."""
    source_lines = [
        b"head\n",
        b"saved one\n",
        b"}\n",
        b"live one\n",
        b"}\n",
        b"middle\n",
        b"saved two\n",
        b"}\n",
        b"live two\n",
        b"}\n",
        b"tail\n",
    ]
    ownership = BatchOwnership.from_presence_lines(
        [f"1-{len(source_lines)}"],
        [
            AbsenceClaim(
                anchor_line=1,
                content_lines=source_lines[3:5],
                source_alternative=True,
            ),
            AbsenceClaim(
                anchor_line=6,
                content_lines=source_lines[8:10],
                source_alternative=True,
            ),
        ],
        replacement_units=[
            ReplacementUnit(["2-3"], [0]),
            ReplacementUnit(["7-8"], [1]),
        ],
    )

    with build_realized_buffer_from_lines(
        [],
        source_lines,
        ownership,
    ) as realized:
        assert realized.to_bytes() == (
            b"head\nsaved one\n}\nmiddle\nsaved two\n}\ntail\n"
        )
