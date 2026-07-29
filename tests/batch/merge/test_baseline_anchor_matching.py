"""Tests for line-matching anchors derived from deletion references."""

from contextlib import nullcontext

import pytest

from git_stage_batch.batch.merge import baseline_anchor_matching
from git_stage_batch.batch.merge.baseline_anchor_matching import (
    acquire_deletion_anchor_pairs_for_target,
)
from git_stage_batch.batch.line_matching.match_workspace import MatcherWorkspace
from git_stage_batch.batch.line_matching.occurrence_index import (
    LinePayloadOccurrenceIndex,
)
from git_stage_batch.batch.line_matching.match import match_lines
from git_stage_batch.batch.merge.merge import (
    merge_batch_from_line_sequences_as_buffer,
)
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.batch.ownership.replacement_units import ReplacementUnitOrigin
from git_stage_batch.exceptions import MergeError


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


@pytest.mark.parametrize("supply_mapping", [False, True])
def test_live_merge_does_not_apply_ambiguous_baseline_coordinate(supply_mapping):
    """A stale deletion coordinate must conflict with structural placement."""
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
    mapping_context = (
        match_lines(source, target)
        if supply_mapping
        else nullcontext(None)
    )

    with mapping_context as mapping:
        with pytest.raises(
            MergeError,
            match="recorded baseline coordinates and structural content matching",
        ):
            merge_batch_from_line_sequences_as_buffer(
                source,
                BatchOwnership([], [claim]),
                target,
                source_to_working_mapping=mapping,
            )


def test_live_merge_does_not_insert_at_ambiguous_baseline_coordinate():
    """A stale insertion coordinate must conflict with structural placement."""
    source = [
        b"P\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"MIDDLE\n",
        b"ANCHOR\n",
        b"NEW\n",
        b"NEXT\n",
        b"TAIL\n",
    ]
    target = [
        b"P\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"FILL\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"MIDDLE\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"TAIL\n",
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["6"],
        [],
        baseline_references={
            6: BaselineReference(
                after_line=5,
                after_content=b"ANCHOR\n",
                before_line=6,
                before_content=b"NEXT\n",
                has_before_line=True,
            ),
        },
    )

    with pytest.raises(
        MergeError,
        match="recorded baseline coordinates and structural content matching",
    ):
        merge_batch_from_line_sequences_as_buffer(
            source,
            ownership,
            target,
        )


def test_live_merge_accepts_identical_coordinate_and_structural_candidates():
    """Matching candidates should not make a repeated boundary ambiguous."""
    source = [
        b"P\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"MIDDLE\n",
        b"ANCHOR\n",
        b"NEW\n",
        b"NEXT\n",
        b"TAIL\n",
    ]
    target = [
        b"P\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"FILL\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"MIDDLE\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"TAIL\n",
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["6"],
        [],
        baseline_references={
            6: BaselineReference(
                after_line=8,
                after_content=b"ANCHOR\n",
                before_line=9,
                before_content=b"NEXT\n",
                has_before_line=True,
            ),
        },
    )

    with merge_batch_from_line_sequences_as_buffer(
        source,
        ownership,
        target,
    ) as merged:
        result = list(merged)

    assert result == [
        b"P\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"FILL\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"MIDDLE\n",
        b"ANCHOR\n",
        b"NEW\n",
        b"NEXT\n",
        b"TAIL\n",
    ]


def test_repeated_boundary_candidates_match_with_pre_staged_prefix():
    """A pre-staged prefix must not stale exact reviewed coordinates."""
    source = [
        b"staged\n",
        b"P\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"MIDDLE\n",
        b"ANCHOR\n",
        b"NEW\n",
        b"NEXT\n",
        b"TAIL\n",
    ]
    target = [
        b"staged\n",
        b"P\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"FILL\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"MIDDLE\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"TAIL\n",
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["7"],
        [],
        baseline_references={
            7: BaselineReference(
                after_line=9,
                after_content=b"ANCHOR\n",
                before_line=10,
                before_content=b"NEXT\n",
                has_before_line=True,
            ),
        },
    )

    with merge_batch_from_line_sequences_as_buffer(
        source,
        ownership,
        target,
    ) as merged:
        result = list(merged)

    assert result == target[:9] + [b"NEW\n"] + target[9:]


def test_live_target_accepts_one_unique_composite_deletion_boundary():
    """Repeated payloads may still form one complete live boundary."""
    source = [
        b"A\n",
        b"B\n",
        b"A\n",
        b"X\n",
        b"O\n",
        b"Y\n",
        b"B\n",
    ]
    target = [
        b"A\n",
        b"O\n",
        b"B\n",
        b"A\n",
        b"X\n",
        b"O\n",
        b"Y\n",
        b"B\n",
    ]
    claim = AbsenceClaim(
        anchor_line=1,
        content_lines=[b"O\n"],
        baseline_reference=BaselineReference(
            after_line=1,
            after_content=b"A\n",
            before_line=3,
            before_content=b"B\n",
            has_before_line=True,
        ),
    )

    assert _deletion_anchor_pairs(source, target, [claim]) == ((1, 1),)


def test_live_target_rejects_repeated_replacement_parent_boundary():
    """A repeated parent identity must not validate a replacement coordinate."""
    target = [
        b"head\n",
        b"old one\n",
        b"old two\n",
        b"tail\n",
        b"gap\n",
        b"head\n",
        b"old one\n",
        b"old two\n",
        b"tail\n",
    ]
    origin = ReplacementUnitOrigin(
        old_start=2,
        old_end=3,
        new_start=2,
        new_end=3,
        baseline_reference=BaselineReference(
            after_line=1,
            after_content=b"head\n",
            before_line=4,
            before_content=b"tail\n",
            has_before_line=True,
        ),
    )

    with MatcherWorkspace() as workspace:
        occurrence_index = LinePayloadOccurrenceIndex(workspace, target)
        is_unique = (
            baseline_anchor_matching._live_replacement_origin_boundary_is_unique(
                origin,
                target,
                1,
                occurrence_index,
            )
        )

    assert is_unique is False


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
    original_check = (
        baseline_anchor_matching._removal_boundary_identity_matches_at
    )

    def count_identity_checks(*args, **kwargs):
        nonlocal identity_checks
        identity_checks += 1
        return original_check(*args, **kwargs)

    monkeypatch.setattr(
        baseline_anchor_matching,
        "_removal_boundary_identity_matches_at",
        count_identity_checks,
    )

    anchors = _deletion_anchor_pairs(target, target, claims)

    assert len(anchors) == len(claims)
    assert identity_checks == len(claims)
