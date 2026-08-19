"""Tests for realized file content construction."""

from git_stage_batch.batch.realized_file_content import _ownership_for_realization
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
