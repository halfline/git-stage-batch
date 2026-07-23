"""Tests for line-matching anchors derived from deletion references."""

import git_stage_batch.batch.merge.baseline_edits as baseline_edits
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


def test_live_target_anchor_requires_verified_deletion_boundary():
    """Live targets require content identity around a deletion coordinate."""
    source = [b"head\n", b"new\n", b"\n", b"tail\n"]
    target = [b"head\n", b"\n", b"old\n", b"\n", b"tail\n"]
    numeric_claim = AbsenceClaim(
        anchor_line=3,
        content_lines=[b"old\n"],
        baseline_reference=BaselineReference(after_line=2),
    )
    verified_claim = AbsenceClaim(
        anchor_line=3,
        content_lines=[b"old\n"],
        baseline_reference=BaselineReference(
            after_line=2,
            after_content=b"\n",
            before_line=4,
            before_content=b"\n",
            has_before_line=True,
        ),
    )

    assert _deletion_anchor_pairs(
        source,
        target,
        [numeric_claim],
    ) == ()
    assert _deletion_anchor_pairs(
        source,
        target,
        [verified_claim],
    ) == ((3, 2),)


def test_live_target_rejects_repeated_verified_deletion_boundary():
    """A stale coordinate must not select one of several identical blocks."""
    source = [
        b"P\n",
        b"ANCHOR\n",
        b"OLD\n",
        b"NEXT\n",
        b"MIDDLE\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"TAIL\n",
    ]
    target = [
        b"P\n",
        b"ANCHOR\n",
        b"OLD\n",
        b"NEXT\n",
        b"FILL\n",
        b"ANCHOR\n",
        b"OLD\n",
        b"NEXT\n",
        b"MIDDLE\n",
        b"ANCHOR\n",
        b"OLD\n",
        b"NEXT\n",
        b"TAIL\n",
    ]
    claim = AbsenceClaim(
        anchor_line=6,
        content_lines=[b"OLD\n"],
        baseline_reference=BaselineReference(
            after_line=6,
            after_content=b"ANCHOR\n",
            before_line=8,
            before_content=b"NEXT\n",
            has_before_line=True,
        ),
    )

    assert _deletion_anchor_pairs(source, target, [claim]) == ()


def test_live_target_checks_indexed_boundary_candidates(monkeypatch):
    """Many unique claims must not rescan every target boundary."""
    target = [f"line-{index}\n".encode() for index in range(1002)]
    claims = [
        AbsenceClaim(
            anchor_line=index,
            content_lines=[target[index]],
            baseline_reference=BaselineReference(
                after_line=index,
                after_content=target[index - 1],
                before_line=index + 2,
                before_content=target[index + 1],
                has_before_line=True,
            ),
        )
        for index in range(1, 1001)
    ]
    identity_checks = 0
    original_check = baseline_edits._removal_boundary_identity_matches_at

    def count_identity_checks(*args, **kwargs):
        nonlocal identity_checks
        identity_checks += 1
        return original_check(*args, **kwargs)

    monkeypatch.setattr(
        baseline_edits,
        "_removal_boundary_identity_matches_at",
        count_identity_checks,
    )

    anchors = _deletion_anchor_pairs(target, target, claims)

    assert len(anchors) == len(claims)
    assert identity_checks == len(claims)
