"""Tests for line-matching anchors derived from deletion references."""

from git_stage_batch.batch.merge.baseline_edits import (
    acquire_deletion_anchor_pairs_for_target,
)
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.references import BaselineReference


def _deletion_anchor_pairs(*args, **kwargs):
    with acquire_deletion_anchor_pairs_for_target(*args, **kwargs) as anchors:
        return tuple(anchors)


def test_baseline_coordinates_anchor_exact_realization_target():
    """Exact baseline targets can use coordinate-only deletion references."""
    source = [b"head\n", b"new\n", b"\n", b"tail\n"]
    target = [b"head\n", b"\n", b"old\n", b"\n", b"tail\n"]
    claim = AbsenceClaim(
        anchor_line=3,
        content_lines=[b"old\n"],
        baseline_reference=BaselineReference(after_line=2),
    )

    anchors = _deletion_anchor_pairs(
        source,
        target,
        [claim],
        trust_baseline_coordinates=True,
    )

    assert anchors == ((3, 2),)
