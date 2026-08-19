"""Tests for coordinate-versus-structural merge strategy decisions."""

import gc
import tracemalloc

from git_stage_batch.batch.merge.coordinate_strategy import (
    has_recorded_baseline_coordinates,
    presence_context_line_sets,
    presence_lines_requiring_distinctive_context,
)
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.claims import PresenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.batch.ownership.replacement_units import ReplacementUnit
from git_stage_batch.core.line_selection import LineRanges


_HEAP_GROWTH_TOLERANCE = 256 * 1024


class _IterationGuardedSelection(LineRanges):
    """Selection whose individual lines must not be expanded."""

    __slots__ = ()

    def __iter__(self):
        raise AssertionError("selected line ranges must not be expanded")


def test_coordinate_detection_scans_references_not_selected_lines() -> None:
    """Coordinate detection must scale with edits, not selected line count."""
    line_count = 1_000_000
    reference = BaselineReference(
        after_line=None,
        after_content=None,
        has_after_line=True,
        before_line=None,
        before_content=None,
        has_before_line=True,
    )
    ownership = BatchOwnership.from_presence_lines(
        [f"1-{line_count}"],
        baseline_references={line_count: reference},
    )

    assert has_recorded_baseline_coordinates(
        ownership,
        _IterationGuardedSelection.from_ranges(((line_count, line_count),)),
        [],
    )


def test_coordinate_policy_uses_last_overlapping_presence_reference() -> None:
    """Coordinate policy must use the effective last-claim-wins reference."""
    target_lines = [b"A\n", b"B\n"]
    valid_reference = BaselineReference(
        after_line=1,
        after_content=b"A\n",
        before_line=2,
        before_content=b"B\n",
        has_before_line=True,
    )
    unrecorded_reference = BaselineReference(
        after_line=None,
        has_after_line=False,
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

    valid = ownership_with_references(unrecorded_reference, valid_reference)
    invalid = ownership_with_references(valid_reference, unrecorded_reference)

    assert has_recorded_baseline_coordinates(
        valid,
        valid.presence_line_set(),
        [],
    )
    assert not presence_lines_requiring_distinctive_context(
        valid,
        valid.presence_line_set(),
        [],
        target_lines=target_lines,
    )
    assert not has_recorded_baseline_coordinates(
        invalid,
        invalid.presence_line_set(),
        [],
    )
    assert presence_lines_requiring_distinctive_context(
        invalid,
        invalid.presence_line_set(),
        [],
        target_lines=target_lines,
    ).ranges() == ((2, 2),)


def test_recorded_context_uses_effective_presence_reference() -> None:
    """Structural context must reject a shadowed recorded boundary."""
    valid_reference = BaselineReference(
        after_line=1,
        after_content=b"A\n",
        before_line=2,
        before_content=b"B\n",
        has_before_line=True,
    )
    unrecorded_reference = BaselineReference(
        after_line=None,
        has_after_line=False,
    )
    def recorded_for(first: BaselineReference, last: BaselineReference):
        ownership = BatchOwnership(
            [
                PresenceClaim(["2"], {2: first}),
                PresenceClaim(["2"], {2: last}),
            ],
            [],
        )
        _distinctive_lines, recorded_lines = presence_context_line_sets(
            ownership,
            ownership.presence_line_set(),
            [],
        )
        return recorded_lines

    assert recorded_for(unrecorded_reference, valid_reference).ranges() == ((2, 2),)
    assert not recorded_for(valid_reference, unrecorded_reference)


def test_unrelated_deletion_does_not_anchor_presence_context() -> None:
    """An independent absence claim cannot make an insertion structurally safe."""
    deletion = AbsenceClaim(anchor_line=3, content_lines=[b"unwanted\n"])
    ownership = BatchOwnership.from_presence_lines(["2"], [deletion])

    assert presence_lines_requiring_distinctive_context(
        ownership,
        ownership.presence_line_set(),
        ownership.deletions,
    )


def test_replacement_unit_covers_its_presence_context() -> None:
    """A valid deletion-coupled replacement retains its structural anchoring."""
    deletion = AbsenceClaim(anchor_line=1, content_lines=[b"old\n"])
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        [deletion],
        replacement_units=[
            ReplacementUnit(presence_lines=["2"], deletion_indices=[0]),
        ],
    )

    assert not presence_lines_requiring_distinctive_context(
        ownership,
        ownership.presence_line_set(),
        ownership.deletions,
    )


def test_legacy_adjacent_deletion_covers_replacement_presence_context() -> None:
    """Legacy replacement metadata retains adjacency-based coupling."""
    deletion = AbsenceClaim(anchor_line=1, content_lines=[b"old\n"])
    ownership = BatchOwnership.from_presence_lines(["2-3"], [deletion])

    assert not presence_lines_requiring_distinctive_context(
        ownership,
        ownership.presence_line_set(),
        ownership.deletions,
    )


def test_legacy_replacement_covers_tail_of_spanning_presence_range() -> None:
    """Adjacent legacy coverage starts inside a normalized presence range."""
    deletion = AbsenceClaim(anchor_line=1, content_lines=[b"old\n"])
    ownership = BatchOwnership.from_presence_lines(["1-3"], [deletion])

    strict_lines = presence_lines_requiring_distinctive_context(
        ownership,
        ownership.presence_line_set(),
        ownership.deletions,
    )

    assert strict_lines.ranges() == ((1, 1),)


def test_only_unanchored_presence_requires_distinctive_context() -> None:
    """Replacement adjacency must not exempt an independent presence run."""
    deletion = AbsenceClaim(anchor_line=3, content_lines=[b"old\n"])
    ownership = BatchOwnership.from_presence_lines(["2", "4"], [deletion])

    strict_lines = presence_lines_requiring_distinctive_context(
        ownership,
        ownership.presence_line_set(),
        ownership.deletions,
    )

    assert strict_lines.ranges() == ((2, 2),)


def test_stale_presence_reference_does_not_cover_distinctive_context() -> None:
    """Recorded coordinates only anchor presence while their payloads resolve."""
    reference = BaselineReference(
        after_line=1,
        after_content=b"stale-head",
        before_line=3,
        before_content=b"tail",
        has_before_line=True,
    )
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        baseline_references={2: reference},
    )

    strict_lines = presence_lines_requiring_distinctive_context(
        ownership,
        ownership.presence_line_set(),
        ownership.deletions,
        target_lines=[b"head\n", b"live variant\n", b"tail\n"],
    )

    assert strict_lines.ranges() == ((2, 2),)


def test_coordinate_coverage_avoids_line_scale_python_heap() -> None:
    """Per-line coordinate coverage should stay in mapped storage."""
    heap_peaks = []
    for line_count in (512, 8192):
        target_lines = [b"anchor\n"] * line_count
        ownership = BatchOwnership.from_presence_lines(
            [f"1-{line_count}"],
            baseline_references={
                line_number: BaselineReference(
                    after_line=line_number - 1 if line_number > 1 else None,
                    after_content=b"anchor" if line_number > 1 else None,
                    before_line=line_number,
                    before_content=b"anchor",
                    has_before_line=True,
                )
                for line_number in range(1, line_count + 1)
            },
        )
        selection = ownership.presence_line_set()

        gc.collect()
        tracemalloc.start()
        try:
            requires_context = bool(
                presence_lines_requiring_distinctive_context(
                    ownership,
                    selection,
                    ownership.deletions,
                    target_lines=target_lines,
                )
            )
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert requires_context is False
        heap_peaks.append(peak_heap)

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + _HEAP_GROWTH_TOLERANCE
