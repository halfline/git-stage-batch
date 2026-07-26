"""Focused tests for merge candidate discovery."""

import pytest

from git_stage_batch.batch.merge import candidate_enumeration
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.replacement_units import (
    ReplacementUnit,
    ReplacementUnitOrigin,
)
from git_stage_batch.exceptions import MergeError


class _UnreadableReplacementUnit:
    @property
    def origin(self):
        raise AssertionError("candidate discovery read a third unresolved unit")


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
