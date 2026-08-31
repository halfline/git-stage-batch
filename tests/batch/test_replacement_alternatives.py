"""Tests for persisted replacement alternatives."""

import pytest

from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.replacement_units import ReplacementUnit
from git_stage_batch.batch.ownership.resolved_replacement_alternatives import (
    InvalidReplacementAlternatives,
    ResolvedReplacementAlternative,
)
from git_stage_batch.core.coordinates import BatchSourceSpace, LineBoundary, LineSpan


def test_batch_ownership_resolves_persisted_replacement_alternative() -> None:
    """Indirect metadata becomes one typed saved/live value before replay."""
    claim = AbsenceClaim(
        anchor_line=1,
        content_lines=[b"live one\n", b"live two\n"],
        source_alternative=True,
    )
    resolved = BatchOwnership.from_presence_lines(
        ["2-3"],
        [claim],
        replacement_units=[ReplacementUnit(["2-3"], [0])],
    ).resolve()
    live_span: LineSpan[BatchSourceSpace] = LineSpan(
        LineBoundary(3),
        LineBoundary(5),
    )

    assert resolved.replacement_alternatives == (
        ResolvedReplacementAlternative(
            unit_index=0,
            deletion_index=0,
            saved=LineSpan(LineBoundary(1), LineBoundary(3)),
            live_payload=(live_span,),
            live_envelope=live_span,
            absence_claim=claim,
        ),
    )


def test_resolved_alternatives_are_ordered_by_live_source_span() -> None:
    """Replay can binary-search alternatives independently of deletion order."""
    later_claim = AbsenceClaim(
        anchor_line=6,
        content_lines=[b"later live\n"],
        source_alternative=True,
    )
    earlier_claim = AbsenceClaim(
        anchor_line=2,
        content_lines=[b"earlier live\n"],
        source_alternative=True,
    )
    alternatives = (
        BatchOwnership.from_presence_lines(
            ["2", "6"],
            [later_claim, earlier_claim],
            replacement_units=[
                ReplacementUnit(["6"], [0]),
                ReplacementUnit(["2"], [1]),
            ],
        )
        .resolve()
        .replacement_alternatives
    )

    assert [alternative.deletion_index for alternative in alternatives] == [1, 0]
    assert [alternative.live_envelope.start.offset for alternative in alternatives] == [
        2,
        6,
    ]


def test_batch_ownership_rejects_uncoupled_persisted_alternative() -> None:
    """Every explicit old side belongs to exactly one replacement unit."""
    with pytest.raises(InvalidReplacementAlternatives, match="not coupled"):
        BatchOwnership.from_presence_lines(
            ["1"],
            [AbsenceClaim(content_lines=[b"old\n"], source_alternative=True)],
        ).resolve()
