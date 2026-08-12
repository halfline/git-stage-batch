"""Focused tests for merge candidate discovery."""

import pytest

from git_stage_batch.batch.merge import candidate_enumeration
from git_stage_batch.batch.merge.candidates import MergeCandidateSetOutcome
from git_stage_batch.batch.merge.merge import (
    merge_batch_from_line_sequences_as_buffer,
)
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


def test_complete_replacement_hybrid_refuses_all_candidate_families() -> None:
    """A partly realized atomic replacement cannot become a reviewed move."""
    source_lines = [b"head\n", b"new1\n", b"new2\n", b"tail\n"]
    working_lines = [
        b"head\n",
        b"new1\n",
        b"old1\n",
        b"old2\n",
        b"tail\n",
    ]
    deletion_claims = [
        AbsenceClaim(
            anchor_line=1,
            content_lines=[b"old1\n", b"old2\n"],
        ),
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["2-3"],
        deletion_claims,
        replacement_units=[
            ReplacementUnit(
                presence_lines=["2-3"],
                deletion_indices=[0],
                origin=ReplacementUnitOrigin(2, 3, 2, 3),
            ),
        ],
    )
    validation_calls = 0

    def accept_resolution(_resolution):
        nonlocal validation_calls
        validation_calls += 1
        return True

    candidates = candidate_enumeration.enumerate_merge_batch_candidates_for_lines(
        source_lines,
        ownership,
        working_lines,
        resolution_is_valid=accept_resolution,
        max_candidates=10,
        coordinate_strategies_differ=True,
    )

    assert candidates.outcome is MergeCandidateSetOutcome.REFUSED
    assert candidates.candidates == ()
    assert validation_calls == 0


def test_safe_mapped_origin_allows_review_for_another_missing_origin(
    monkeypatch,
) -> None:
    """Independent review reuses the mapping that found its missing unit."""
    source_lines = [b"new one\n", b"new two\n"]
    working_lines = [b"new one\n", b"old two\n"]
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

    mapping_calls = 0
    real_match_lines = candidate_enumeration.match_lines

    def count_mapping(*args, **kwargs):
        nonlocal mapping_calls
        mapping_calls += 1
        return real_match_lines(*args, **kwargs)

    monkeypatch.setattr(candidate_enumeration, "match_lines", count_mapping)

    candidates = candidate_enumeration.enumerate_merge_batch_candidates_for_lines(
        source_lines,
        ownership,
        working_lines,
        resolution_is_valid=lambda _resolution: True,
        max_candidates=10,
    )

    assert candidates.outcome is MergeCandidateSetOutcome.REVIEW_REQUIRED
    assert [candidate.summary for candidate in candidates.candidates] == [
        "replace target lines 2 with source lines 2",
    ]
    assert mapping_calls == 1
    with merge_batch_from_line_sequences_as_buffer(
        source_lines,
        ownership,
        working_lines,
        resolution=candidates.candidates[0].resolution,
    ) as merged:
        assert merged.to_bytes() == b"new one\nnew two\n"


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

    with candidate_enumeration.match_lines(
        source_lines,
        working_lines,
    ) as source_to_working_mapping:
        unresolved = candidate_enumeration._find_unresolved_replacement_origin(
            source_lines,
            ownership,
            working_lines,
            selected_lines,
            deletion_claims,
            source_to_working_mapping,
            max_candidates=10,
            spool_dir=None,
        )
    candidates = (
        candidate_enumeration._replacement_origin_candidate_set_from_unresolved(
            unresolved,
            deletion_claims,
            resolution_is_valid=lambda _resolution: True,
            max_candidates=10,
        )
    )

    assert [candidate.summary for candidate in candidates.candidates] == [
        "replace target lines 1 with source lines 1-1999",
    ]
