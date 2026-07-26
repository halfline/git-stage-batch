"""Focused tests for merge candidate discovery."""

import pytest

from git_stage_batch.batch.merge import candidate_enumeration
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.replacement_units import (
    ReplacementUnit,
    ReplacementUnitOrigin,
)
from git_stage_batch.core.line_selection import LineRanges
from git_stage_batch.exceptions import MergeError


class _UnreadableReplacementUnit:
    @property
    def origin(self):
        raise AssertionError("candidate discovery read a third unresolved unit")


@pytest.mark.parametrize("deletion_index", [False, 0.0])
def test_replacement_origin_discovery_rejects_noninteger_deletion_index(
    deletion_index,
) -> None:
    """Replacement review should reject noninteger deletion aliases."""
    source_lines = [b"new value\n"]
    working_lines = [b"old value\n"]
    deletion_claims = [
        AbsenceClaim(anchor_line=None, content_lines=[b"old value\n"]),
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        deletion_claims,
        replacement_units=[
            ReplacementUnit(
                presence_lines=["1"],
                deletion_indices=[deletion_index],
                origin=ReplacementUnitOrigin(1, 1, 1, 1),
            ),
        ],
    )

    with pytest.raises(
        MergeError,
        match="Batch was created from a different version of the file",
    ):
        candidate_enumeration.enumerate_merge_batch_candidates_for_lines(
            source_lines,
            ownership,
            working_lines,
            resolution_is_valid=lambda _resolution: True,
            max_candidates=10,
        )
def test_replacement_origin_discovery_stops_after_second_unresolved_unit() -> None:
    """A second unresolved replacement should fail before reading later units."""
    source_lines = [b"new one\n", b"new two\n"]
    working_lines = [b"old one\n", b"old two\n"]
    deletion_claims = [
        AbsenceClaim(anchor_line=None, content_lines=[b"old one\n"]),
        AbsenceClaim(anchor_line=None, content_lines=[b"old two\n"]),
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        deletion_claims,
        replacement_units=[
            ReplacementUnit(
                presence_lines=["1"],
                deletion_indices=[0],
                origin=ReplacementUnitOrigin(1, 1, 1, 1),
            ),
            ReplacementUnit(
                presence_lines=["2"],
                deletion_indices=[1],
                origin=ReplacementUnitOrigin(2, 2, 2, 2),
            ),
        ],
    )
    ownership.replacement_units.append(_UnreadableReplacementUnit())

    with pytest.raises(
        MergeError,
        match="Multiple split replacement placements need review",
    ):
        candidate_enumeration.enumerate_merge_batch_candidates_for_lines(
            source_lines,
            ownership,
            working_lines,
            resolution_is_valid=lambda _resolution: True,
            max_candidates=10,
        )
def test_fragmented_replacement_discovery_avoids_heap_selections(
    monkeypatch,
) -> None:
    """Fragmented review ranges should stay in mapped discovery storage."""
    selected_ranges = tuple((line, line) for line in range(1, 2000, 2))
    range_spec = ",".join(str(start) for start, _end in selected_ranges)
    selected_lines = LineRanges.from_ranges(selected_ranges)
    source_lines = [b"new value\n"] * 1999
    working_lines = [b"old value\n"]
    deletion_claims = [
        AbsenceClaim(anchor_line=None, content_lines=[b"old value\n"]),
    ]
    ownership = BatchOwnership.from_presence_lines(
        [range_spec],
        deletion_claims,
        replacement_units=[
            ReplacementUnit(
                presence_lines=[range_spec],
                deletion_indices=[0],
                origin=ReplacementUnitOrigin(1, 1, 1, 1999),
            ),
        ],
    )

    def fail_heap_selection(*_args, **_kwargs):
        raise AssertionError("candidate discovery rebuilt a heap selection")

    monkeypatch.setattr(LineRanges, "from_specs", fail_heap_selection)

    candidates = candidate_enumeration._replacement_origin_candidate_set(
        source_lines,
        ownership,
        working_lines,
        selected_lines,
        deletion_claims,
        resolution_is_valid=lambda _resolution: True,
        max_candidates=10,
        spool_dir=None,
    )

    assert [candidate.summary for candidate in candidates.candidates] == [
        "replace target lines 1 with source lines 1-1999",
    ]
