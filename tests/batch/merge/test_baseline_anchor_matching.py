"""Tests for line-matching anchors derived from deletion references."""

from contextlib import nullcontext
from typing import cast

import pytest

from git_stage_batch.batch.merge import baseline_anchor_matching
from git_stage_batch.batch.merge.baseline_anchor_matching import (
    acquire_discard_baseline_anchor_pairs,
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
from git_stage_batch.batch.ownership.claims import PresenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.batch.ownership.replacement_units import (
    LegacyReplacementUnitOrigin,
    ReplacementUnit,
    ReplacementUnitOrigin,
)
from git_stage_batch.core.line_selection import LineRanges
from git_stage_batch.exceptions import MergeError


def _deletion_anchor_pairs(*args, **kwargs):
    with acquire_deletion_anchor_pairs_for_target(*args, **kwargs) as anchors:
        return tuple(anchors)


def _discard_anchor_pairs(*args, **kwargs):
    with acquire_discard_baseline_anchor_pairs(*args, **kwargs) as anchors:
        return tuple(anchors)


def test_discard_anchors_complete_copied_bof_insertion():
    """A live copied run can prove which equal baseline copy must survive."""
    baseline = [b"A\n", b"B\n"]
    source = [b"A\n", b"B\n", b"A\n", b"B\n"]
    reference = BaselineReference(
        after_line=None,
        before_line=1,
        before_content=b"A\n",
        has_before_line=True,
    )
    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        baseline_references={1: reference, 2: reference},
    )

    with match_lines(source, source) as source_to_working:
        anchors = _discard_anchor_pairs(
            source,
            baseline,
            ownership,
            source_to_working_mapping=source_to_working,
            working_lines=source,
        )

    assert anchors == ((1, 3),)


def test_discard_insertion_anchors_do_not_flatten_reference_map(
    monkeypatch,
) -> None:
    """Insertion anchors should keep per-line references in mapped storage."""
    baseline = [b"A\n", b"B\n"]
    source = [b"A\n", b"X\n", b"B\n"]
    reference = BaselineReference(
        after_line=1,
        after_content=b"A\n",
        before_line=2,
        before_content=b"B\n",
        has_before_line=True,
    )
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        baseline_references={2: reference},
    )

    def fail_flatten():
        raise AssertionError("baseline references were copied into a dict")

    monkeypatch.setattr(
        ownership,
        "presence_baseline_references",
        fail_flatten,
    )

    with match_lines(source, source) as source_to_working:
        anchors = _discard_anchor_pairs(
            source,
            baseline,
            ownership,
            source_to_working_mapping=source_to_working,
            working_lines=source,
        )

    assert anchors == ((1, 1), (2, 3))


def test_discard_insertion_anchors_keep_last_overlapping_reference() -> None:
    """Storage-backed reference compaction must preserve last-claim-wins."""
    baseline = [b"A\n", b"B\n"]
    source = [b"A\n", b"X\n", b"B\n"]
    valid_reference = BaselineReference(
        after_line=1,
        after_content=b"A\n",
        before_line=2,
        before_content=b"B\n",
        has_before_line=True,
    )
    wrong_reference = BaselineReference(
        after_line=None,
        before_line=1,
        before_content=b"A\n",
        has_before_line=True,
    )

    def ownership_with_references(
        first: BaselineReference,
        last: BaselineReference,
    ) -> BatchOwnership:
        return BatchOwnership(
            [
                PresenceClaim(["2"], {2: first}),
                PresenceClaim(["2"], {2: last}),
            ],
            [],
        )

    with match_lines(source, source) as source_to_working:
        valid_anchors = _discard_anchor_pairs(
            source,
            baseline,
            ownership_with_references(wrong_reference, valid_reference),
            source_to_working_mapping=source_to_working,
            working_lines=source,
        )
    with match_lines(source, source) as source_to_working:
        invalid_anchors = _discard_anchor_pairs(
            source,
            baseline,
            ownership_with_references(valid_reference, wrong_reference),
            source_to_working_mapping=source_to_working,
            working_lines=source,
        )

    assert valid_anchors == ((1, 1), (2, 3))
    assert invalid_anchors == ()


def test_discard_does_not_reanchor_removed_repeated_bof_insertion():
    """A shorter baseline copy cannot impersonate an already removed run."""
    baseline = [b"A\n", b"A\n"]
    source = [b"A\n", b"A\n", b"A\n"]
    reference = BaselineReference(
        after_line=None,
        before_line=1,
        before_content=b"A\n",
        has_before_line=True,
    )
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        baseline_references={1: reference},
    )

    with match_lines(source, baseline) as source_to_working:
        anchors = _discard_anchor_pairs(
            source,
            baseline,
            ownership,
            source_to_working_mapping=source_to_working,
            working_lines=baseline,
        )

    assert anchors == ()


@pytest.mark.parametrize(
    "working",
    [
        [
            b"P\n",
            b"H\n",
            b"A\n",
            b"Q\n",
            b"B\n",
            b"T\n",
            b"U\n",
            b"H\n",
            b"A\n",
            b"X\n",
            b"B\n",
            b"T\n",
        ],
        [
            b"P\n",
            b"H\n",
            b"A\n",
            b"B\n",
            b"T\n",
            b"U\n",
            b"H\n",
            b"A\n",
            b"X\n",
            b"B\n",
            b"T\n",
        ],
    ],
    ids=["intended-gap-diverged", "intended-gap-already-absent"],
)
def test_discard_refuses_pure_insertion_in_ambiguous_source_clone(working):
    """An exact whole-source clone cannot steal a diverged insertion gap."""
    baseline = [b"H\n", b"A\n", b"B\n", b"T\n"]
    source = [b"H\n", b"A\n", b"X\n", b"B\n", b"T\n"]
    reference = BaselineReference(
        after_line=2,
        after_content=b"A\n",
        before_line=3,
        before_content=b"B\n",
        has_before_line=True,
    )
    ownership = BatchOwnership.from_presence_lines(
        ["3"],
        baseline_references={3: reference},
    )

    with match_lines(source, working) as source_to_working:
        with pytest.raises(MergeError, match="different version"):
            _discard_anchor_pairs(
                source,
                baseline,
                ownership,
                source_to_working_mapping=source_to_working,
                working_lines=working,
            )


def test_discard_does_not_anchor_partial_copied_insertion_run():
    """One selected line cannot stand in for its unselected insertion sibling."""
    baseline = [b"A\n", b"B\n"]
    source = [b"A\n", b"B\n", b"A\n", b"B\n"]
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        baseline_references={
            1: BaselineReference(
                after_line=None,
                before_line=1,
                before_content=b"A\n",
                has_before_line=True,
            )
        },
    )

    with match_lines(source, source) as source_to_working:
        anchors = _discard_anchor_pairs(
            source,
            baseline,
            ownership,
            source_to_working_mapping=source_to_working,
            working_lines=source,
        )

    assert anchors == ()


def test_discard_does_not_anchor_incomplete_reference_group():
    """Malformed metadata within a selected insertion run fails closed."""
    baseline = [b"A\n", b"B\n"]
    source = [b"A\n", b"B\n", b"A\n", b"B\n"]
    reference = BaselineReference(
        after_line=None,
        before_line=1,
        before_content=b"A\n",
        has_before_line=True,
    )
    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        baseline_references={
            1: reference,
            2: cast(BaselineReference, {}),
        },
    )

    with match_lines(source, source) as source_to_working:
        anchors = _discard_anchor_pairs(
            source,
            baseline,
            ownership,
            source_to_working_mapping=source_to_working,
            working_lines=source,
        )

    assert anchors == ()


def test_discard_does_not_anchor_malformed_deletion_reference():
    """Malformed deletion reference metadata cannot constrain correspondence."""
    ownership = BatchOwnership(
        [],
        [
            AbsenceClaim(
                anchor_line=1,
                content_lines=[b"old\n"],
                baseline_reference=cast(BaselineReference, {}),
            )
        ],
    )

    assert _discard_anchor_pairs(
        [b"A\n", b"new\n"],
        [b"A\n", b"old\n"],
        ownership,
    ) == ()


def test_discard_anchors_replacement_on_both_equal_edges():
    """Replacement-unit metadata contributes its verified trailing edge."""
    baseline = [
        b"old-header\n",
        b"{\n",
        b" common\n",
        b" old-only\n",
        b"}\n",
        b"tail\n",
    ]
    source = [
        b"new-header\n",
        b"{\n",
        b" common\n",
        b" new-only\n",
        b"}\n",
        b"tail\n",
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["1", "4"],
        [
            AbsenceClaim(
                anchor_line=None,
                content_lines=[b"old-header\n"],
                baseline_reference=BaselineReference(
                    after_line=None,
                    before_line=2,
                    has_before_line=True,
                ),
            ),
            AbsenceClaim(
                anchor_line=3,
                content_lines=[b" old-only\n"],
                baseline_reference=BaselineReference(
                    after_line=3,
                    before_line=5,
                    has_before_line=True,
                ),
            ),
        ],
        replacement_units=[
            ReplacementUnit(["1"], [0]),
            ReplacementUnit(["4"], [1]),
        ],
    )

    anchors = _discard_anchor_pairs(source, baseline, ownership)

    assert anchors == ((2, 2), (3, 3), (5, 5))


def test_discard_replacement_anchor_does_not_scan_prior_presence_ranges(
    monkeypatch,
):
    """Replacement containment uses indexed ranges instead of count scans."""
    baseline = [b"A\n", b"old\n", b"T\n"]
    source = [b"A\n", b"new\n", b"T\n"]
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        [
            AbsenceClaim(
                anchor_line=1,
                content_lines=[b"old\n"],
                baseline_reference=BaselineReference(
                    after_line=1,
                    before_line=3,
                    has_before_line=True,
                ),
            )
        ],
        replacement_units=[ReplacementUnit(["2"], [0])],
    )

    def reject_count_scan(*_args, **_kwargs):
        raise AssertionError("discard anchors must not scan presence ranges")

    monkeypatch.setattr(LineRanges, "count", reject_count_scan)

    assert _discard_anchor_pairs(source, baseline, ownership) == (
        (1, 1),
        (3, 3),
    )


def test_discard_refuses_collectively_crossing_deletion_anchors():
    """Individually valid but crossing ownership anchors refuse discard."""
    baseline = [b"X\n", b"old-one\n", b"Y\n", b"old-two\n"]
    source = [b"Y\n", b"middle\n", b"X\n"]
    ownership = BatchOwnership(
        [],
        [
            AbsenceClaim(
                anchor_line=3,
                content_lines=[b"old-one\n"],
                baseline_reference=BaselineReference(after_line=1),
            ),
            AbsenceClaim(
                anchor_line=1,
                content_lines=[b"old-two\n"],
                baseline_reference=BaselineReference(after_line=3),
            ),
        ],
    )

    with pytest.raises(MergeError, match="different version"):
        _discard_anchor_pairs(source, baseline, ownership)


def test_discard_does_not_anchor_partial_replacement_origin_trailing_line():
    """A sibling line outside a split unit cannot become its equal edge."""
    baseline = [b"A\n", b"O1\n", b"O2\n", b"T\n"]
    source = [b"A\n", b"N1\n", b"T\n", b"T\n"]
    reference = BaselineReference(
        after_line=1,
        before_line=4,
        has_before_line=True,
    )
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        [
            AbsenceClaim(
                anchor_line=1,
                content_lines=[b"O1\n", b"O2\n"],
                baseline_reference=reference,
            )
        ],
        replacement_units=[
            ReplacementUnit(
                ["2"],
                [0],
                origin=ReplacementUnitOrigin(
                    old_start=2,
                    old_end=3,
                    new_start=2,
                    new_end=3,
                    baseline_reference=reference,
                ),
            )
        ],
    )

    assert _discard_anchor_pairs(source, baseline, ownership) == ((1, 1),)


@pytest.mark.parametrize(
    "origin",
    [
        cast(ReplacementUnitOrigin, {}),
    ],
    ids=["wrong-type"],
)
def test_discard_does_not_trust_malformed_replacement_origin(origin):
    """Malformed origin metadata cannot authorize a trailing-edge anchor."""
    baseline = [b"A\n", b"old\n", b"T\n"]
    source = [b"A\n", b"new\n", b"T\n"]
    reference = BaselineReference(
        after_line=1,
        before_line=3,
        has_before_line=True,
    )
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        [
            AbsenceClaim(
                anchor_line=1,
                content_lines=[b"old\n"],
                baseline_reference=reference,
            )
        ],
        replacement_units=[
            ReplacementUnit(
                ["2"],
                [0],
                origin_evidence=LegacyReplacementUnitOrigin(origin),
            ),
        ],
    )

    assert _discard_anchor_pairs(source, baseline, ownership) == ((1, 1),)


def test_current_replacement_origin_rejects_non_positive_coordinates():
    """Malformed current provenance fails at the canonical boundary."""
    with pytest.raises(ValueError, match="invalid old span"):
        ReplacementUnitOrigin(
            old_start=0,
            old_end=0,
            new_start=0,
            new_end=0,
        )


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


def test_live_target_anchor_tracks_one_shifted_verified_boundary():
    """A unique live deletion boundary may move from recorded coordinates."""
    source = [b"head\n", b"\n", b"new\n", b"tail\n"]
    target = [b"staged\n", b"head\n", b"\n", b"old\n", b"tail\n"]
    claim = AbsenceClaim(
        anchor_line=2,
        content_lines=[b"old\n"],
        baseline_reference=BaselineReference(
            after_line=2,
            after_content=b"\n",
            before_line=4,
            before_content=b"tail\n",
            has_before_line=True,
        ),
    )

    assert _deletion_anchor_pairs(source, target, [claim]) == ((2, 3),)


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
            match="Batch was created from a different version of the file",
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
        match="Batch was created from a different version of the file",
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


def test_bounded_live_removal_refuses_common_candidates(monkeypatch):
    """Bounded relocation must not scan every repeated target block."""
    target = [line for _ in range(17) for line in (b"A\n", b"O\n", b"B\n")]
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

    def refuse_iteration(*_args, **_kwargs):
        raise AssertionError("common candidates must be rejected before iteration")

    monkeypatch.setattr(
        LinePayloadOccurrenceIndex,
        "matching_line_indexes",
        refuse_iteration,
    )
    with MatcherWorkspace() as workspace:
        occurrence_index = LinePayloadOccurrenceIndex(workspace, target)
        assert baseline_anchor_matching.unique_live_removal_edit(
            claim,
            target,
            occurrence_index,
            candidate_limit=16,
        ) is None


def test_live_deletion_anchors_bound_common_candidates(monkeypatch):
    """Many claims cannot repeatedly scan a common boundary identity."""
    target = [line for _ in range(17) for line in (b"A\n", b"O\n", b"B\n")]
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

    def refuse_iteration(*_args, **_kwargs):
        raise AssertionError("common candidates must be rejected before iteration")

    monkeypatch.setattr(
        LinePayloadOccurrenceIndex,
        "matching_line_indexes",
        refuse_iteration,
    )

    assert _deletion_anchor_pairs([b"A\n"], target, [claim] * 128) == ()


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


def test_occurrence_index_can_preserve_exact_line_identity():
    """Distinctive context should retain line terminators in identity checks."""
    lines = [b"same\n", b"same"]

    with MatcherWorkspace() as workspace:
        normalized_index = LinePayloadOccurrenceIndex(workspace, lines)
        exact_index = LinePayloadOccurrenceIndex(
            workspace,
            lines,
            normalize_payloads=False,
        )

        assert normalized_index.occurrence_count(b"same\n") == 2
        assert exact_index.occurrence_count(b"same\n") == 1
        assert exact_index.occurrence_count(b"same") == 1


def test_occurrence_index_can_filter_target_indexes():
    """Filtered indexes should retain only requested target positions."""
    lines = [b"keep\n", b"skip\n", b"keep\n", b"other\n"]

    with MatcherWorkspace() as workspace:
        occurrence_index = LinePayloadOccurrenceIndex(
            workspace,
            lines,
            normalize_payloads=False,
            target_indexes=(0, 3),
        )

        assert occurrence_index.occurrence_count(b"keep\n") == 1
        assert occurrence_index.occurrence_count(b"skip\n") == 0
        assert tuple(occurrence_index.matching_line_indexes(b"other\n")) == (3,)


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
