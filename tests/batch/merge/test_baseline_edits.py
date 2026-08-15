"""Focused tests for baseline-coordinate edit planning."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from git_stage_batch.batch.merge import (
    baseline_anchor_matching,
    baseline_edits,
    baseline_removal_edits,
)
from git_stage_batch.batch.merge.baseline_replacement_choices import (
    replacement_origin_choices_for_unit,
)
from git_stage_batch.batch.merge.candidates import MergeResolution
from git_stage_batch.batch.line_matching.match import match_lines
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.batch.ownership.replacement_units import (
    ReplacementUnit,
    ReplacementUnitOrigin,
)
from git_stage_batch.core.line_selection import LineRanges


class _CountingLines(Sequence[bytes]):
    def __init__(self, line_count: int) -> None:
        self.line_count = line_count
        self.read_count = 0

    def __len__(self) -> int:
        return self.line_count

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        if isinstance(index, slice):
            raise AssertionError("source lines must not be sliced")
        if index < 0:
            index += self.line_count
        if index < 0 or index >= self.line_count:
            raise IndexError(index)
        self.read_count += 1
        return b"new value\n"


class _InterruptingLines(Sequence[bytes]):
    def __init__(self, line_count: int) -> None:
        self.line_count = line_count

    def __len__(self) -> int:
        return self.line_count

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        raise KeyboardInterrupt


def _boundary_reference(
    *,
    after_line: int | None,
    after_content: bytes | None = None,
    before_line: int | None,
    before_content: bytes | None = None,
) -> BaselineReference:
    return BaselineReference(
        after_line=after_line,
        after_content=after_content,
        has_after_line=True,
        before_line=before_line,
        before_content=before_content,
        has_before_line=True,
    )


def test_baseline_edit_planning_composes_all_edit_kinds() -> None:
    """Replacement, removal, and insertion plans should compose in order."""
    source_lines = [b"new value\n", b"inserted\n"]
    working_lines = [b"old value\n", b"remove me\n", b"tail\n"]
    replacement_reference = _boundary_reference(
        after_line=None,
        before_line=2,
        before_content=b"remove me",
    )
    removal_reference = _boundary_reference(
        after_line=1,
        after_content=b"old value",
        before_line=3,
        before_content=b"tail",
    )
    insertion_reference = _boundary_reference(
        after_line=2,
        after_content=b"remove me",
        before_line=3,
        before_content=b"tail",
    )
    deletion_claims = [
        AbsenceClaim(
            anchor_line=None,
            content_lines=[b"old value\n"],
            baseline_reference=replacement_reference,
        ),
        AbsenceClaim(
            anchor_line=1,
            content_lines=[b"remove me\n"],
            baseline_reference=removal_reference,
        ),
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        deletion_claims,
        baseline_references={
            1: replacement_reference,
            2: insertion_reference,
        },
        replacement_units=[
            ReplacementUnit(
                presence_lines=["1"],
                deletion_indices=[0],
            ),
        ],
    )

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((1, 2),)),
        deletion_claims,
    )

    assert result is not None
    assert list(result) == [b"new value\n", b"inserted\n", b"tail\n"]


def test_baseline_edit_planning_places_presence_after_replacement_anchor() -> None:
    """An unmapped presence beside a replacement anchor joins that edit."""
    source_lines = [
        b"prefix\n",
        b"static bool strides_are_valid(unsigned long stride)\n",
        b"{\n",
        b"\tif (stride > SSIZE_MAX)\n",
        b"\tif (stride > INT_MAX)\n",
        b"\t\treturn false;\n",
        b"\n",
        b"\treturn true;\n",
        b"}\n",
        b"\n",
        b"static ptrdiff_t block_step(void)\n",
        b"{\n",
        b"\treturn BLOCK_HEIGHT;\n",
        b"}\n",
        b"suffix\n",
    ]
    working_lines = [
        b"prefix\n",
        b"static int block_step(void)\n",
        b"{\n",
        b"\treturn BLOCK_WIDTH;\n",
        b"}\n",
        b"suffix\n",
    ]
    deletion_reference = _boundary_reference(
        after_line=3,
        after_content=b"{",
        before_line=5,
        before_content=b"}",
    )
    origin_reference = _boundary_reference(
        after_line=1,
        after_content=b"prefix",
        before_line=5,
        before_content=b"}",
    )
    deletion_claims = [
        AbsenceClaim(
            anchor_line=3,
            content_lines=[b"\treturn BLOCK_WIDTH;\n"],
            baseline_reference=deletion_reference,
        )
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["4,13"],
        deletion_claims,
        replacement_units=[
            ReplacementUnit(
                presence_lines=["13"],
                deletion_indices=[0],
                origin=ReplacementUnitOrigin(
                    old_start=2,
                    old_end=4,
                    new_start=2,
                    new_end=12,
                    baseline_reference=origin_reference,
                ),
            )
        ],
    )

    with match_lines(
        source_lines,
        working_lines,
        anchor_pairs=((3, 3),),
    ) as mapping:
        assert baseline_edits.try_apply_baseline_coordinate_edits(
            source_lines,
            working_lines,
            ownership,
            LineRanges.from_ranges(((4, 4), (13, 13))),
            deletion_claims,
            trust_baseline_coordinates=True,
            source_to_working_mapping=mapping,
        ) is None

    with match_lines(
        source_lines,
        working_lines,
        anchor_pairs=((3, 3),),
    ) as mapping:
        result = baseline_edits.try_apply_baseline_coordinate_edits(
            source_lines,
            working_lines,
            ownership,
            LineRanges.from_ranges(((4, 4), (13, 13))),
            deletion_claims,
            allow_adjacent_unmapped_presence=True,
            trust_baseline_coordinates=True,
            source_to_working_mapping=mapping,
        )

    assert result is not None
    assert list(result) == [
        b"prefix\n",
        b"static int block_step(void)\n",
        b"{\n",
        b"\tif (stride > SSIZE_MAX)\n",
        b"\treturn BLOCK_HEIGHT;\n",
        b"}\n",
        b"suffix\n",
    ]


def test_live_planning_tracks_one_shifted_insertion_boundary() -> None:
    """A unique saved insertion boundary may move in the live target."""
    source_lines = [b"head\n", b"added\n", b"tail\n"]
    working_lines = [b"staged\n", b"head\n", b"tail\n"]
    reference = _boundary_reference(
        after_line=1,
        after_content=b"head\n",
        before_line=2,
        before_content=b"tail\n",
    )
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        baseline_references={2: reference},
    )

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((2, 2),)),
        [],
    )

    assert result is not None
    assert list(result) == [
        b"staged\n",
        b"head\n",
        b"added\n",
        b"tail\n",
    ]
    assert baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((2, 2),)),
        [],
        trust_baseline_coordinates=True,
    ) is None


def test_shifted_insertion_lookup_checks_one_indexed_boundary_per_line(
    monkeypatch,
) -> None:
    """Many shifted unique insertions must not rescan the target per claim."""
    insertion_count = 1000
    anchors = [
        f"anchor-{index}\n".encode()
        for index in range(insertion_count)
    ]
    source_lines: list[bytes] = []
    claimed_lines = []
    references = {}
    for index, anchor in enumerate(anchors):
        source_lines.extend((anchor, f"added-{index}\n".encode()))
        claimed_line = len(source_lines)
        claimed_lines.append(claimed_line)
        references[claimed_line] = _boundary_reference(
            after_line=index + 1,
            after_content=anchor,
            before_line=(
                index + 2
                if index + 1 < insertion_count
                else None
            ),
            before_content=(
                anchors[index + 1]
                if index + 1 < insertion_count
                else None
            ),
        )
    working_lines = [b"staged\n", *anchors]
    ownership = BatchOwnership.from_presence_lines(
        [LineRanges.from_lines(claimed_lines).to_line_spec()],
        baseline_references=references,
    )
    identity_checks = 0
    original_check = (
        baseline_anchor_matching._insertion_boundary_identity_matches_at
    )

    def count_identity_checks(*args, **kwargs):
        nonlocal identity_checks
        identity_checks += 1
        return original_check(*args, **kwargs)

    monkeypatch.setattr(
        baseline_anchor_matching,
        "_insertion_boundary_identity_matches_at",
        count_identity_checks,
    )

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_lines(claimed_lines),
        [],
    )

    assert result is not None
    assert list(result) == [b"staged\n", *source_lines]
    assert identity_checks <= insertion_count * 3


def test_trusted_plan_composes_partial_replacement_and_repeated_insertion() -> None:
    """Partial replacements and additions retain pre-staged target content."""
    source_lines = [
        line.encode()
        for line in (
            "head\n",
            "NEW-A\n",
            "NEW-B\n",
            "section\n",
            "marker\n",
            "ADDED\n",
            "end\n",
            "section\n",
            "marker\n",
            "end\n",
            "tail\n",
        )
    ]
    working_lines = [
        line.encode()
        for line in (
            "staged\n",
            "head\n",
            "old-a\n",
            "old-b\n",
            "section\n",
            "marker\n",
            "end\n",
            "section\n",
            "marker\n",
            "end\n",
            "tail\n",
        )
    ]
    replacement_reference = _boundary_reference(
        after_line=2,
        after_content=b"head\n",
        before_line=4,
        before_content=b"old-b\n",
    )
    insertion_reference = _boundary_reference(
        after_line=6,
        after_content=b"marker\n",
        before_line=7,
        before_content=b"end\n",
    )
    deletion_claims = [
        AbsenceClaim(
            anchor_line=1,
            content_lines=[b"old-a\n"],
            baseline_reference=replacement_reference,
        )
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["2", "6"],
        deletion_claims,
        baseline_references={
            2: replacement_reference,
            6: insertion_reference,
        },
        replacement_units=[
            ReplacementUnit(
                presence_lines=["2"],
                deletion_indices=[0],
                origin=ReplacementUnitOrigin(
                    old_start=2,
                    old_end=3,
                    new_start=2,
                    new_end=3,
                    baseline_reference=_boundary_reference(
                        after_line=2,
                        after_content=b"head\n",
                        before_line=5,
                        before_content=b"section\n",
                    ),
                ),
            )
        ],
    )

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((2, 2), (6, 6))),
        deletion_claims,
        trust_baseline_coordinates=True,
    )

    assert result is not None
    assert list(result) == [
        b"staged\n",
        b"head\n",
        b"NEW-A\n",
        b"old-b\n",
        b"section\n",
        b"marker\n",
        b"ADDED\n",
        b"end\n",
        b"section\n",
        b"marker\n",
        b"end\n",
        b"tail\n",
    ]


def test_same_boundary_replacement_payloads_follow_source_order() -> None:
    """Replacement and insertion payloads at one boundary should retain order."""
    source_lines = [b"new one\n", b"new two\n"]
    working_lines = [b"old\n"]
    replacement_reference = _boundary_reference(
        after_line=None,
        before_line=None,
    )
    insertion_reference = _boundary_reference(
        after_line=None,
        before_line=1,
        before_content=b"old\n",
    )
    deletion_claims = [
        AbsenceClaim(
            anchor_line=None,
            content_lines=[b"old\n"],
            baseline_reference=replacement_reference,
        ),
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        deletion_claims,
        baseline_references={
            1: insertion_reference,
            2: insertion_reference,
        },
        replacement_units=[
            ReplacementUnit(
                presence_lines=["1"],
                deletion_indices=[0],
            ),
        ],
    )

    for trust_baseline_coordinates in (False, True):
        result = baseline_edits.try_apply_baseline_coordinate_edits(
            source_lines,
            working_lines,
            ownership,
            LineRanges.from_ranges(((1, 2),)),
            deletion_claims,
            trust_baseline_coordinates=trust_baseline_coordinates,
        )

        assert result is not None
        assert list(result) == source_lines


def test_same_boundary_noncontiguous_payloads_follow_source_order() -> None:
    """Payload ranges from separate edits should merge by source position."""
    source_lines = [b"new one\n", b"new two\n", b"new three\n"]
    working_lines = [b"old\n"]
    replacement_reference = _boundary_reference(
        after_line=None,
        before_line=None,
    )
    insertion_reference = _boundary_reference(
        after_line=None,
        before_line=1,
        before_content=b"old\n",
    )
    deletion_claims = [
        AbsenceClaim(
            anchor_line=None,
            content_lines=[b"old\n"],
            baseline_reference=replacement_reference,
        ),
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["1-3"],
        deletion_claims,
        baseline_references={
            1: insertion_reference,
            2: insertion_reference,
            3: insertion_reference,
        },
        replacement_units=[
            ReplacementUnit(
                presence_lines=["1", "3"],
                deletion_indices=[0],
            ),
        ],
    )

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((1, 3),)),
        deletion_claims,
    )

    assert result is not None
    assert list(result) == source_lines


def test_baseline_edit_planning_rejects_incomplete_replacement_unit() -> None:
    """Replacement metadata without one removal must fail closed."""
    source_lines = [b"new value\n"]
    working_lines = [b"old value\n"]
    deletion_claims = [
        AbsenceClaim(
            anchor_line=None,
            content_lines=[b"old value\n"],
            baseline_reference=_boundary_reference(
                after_line=None,
                before_line=None,
            ),
        ),
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        deletion_claims,
        replacement_units=[
            ReplacementUnit(
                presence_lines=["1"],
                deletion_indices=[],
            ),
        ],
    )

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((1, 1),)),
        deletion_claims,
        trust_baseline_coordinates=True,
    )

    assert result is None


@pytest.mark.parametrize("deletion_index", [False, 0.0])
def test_baseline_edit_planning_rejects_noninteger_deletion_index(
    deletion_index,
) -> None:
    """Malformed replacement indexes should fail closed at the unit boundary."""
    source_lines = [b"new value\n"]
    working_lines = [b"old value\n"]
    deletion_claims = [
        AbsenceClaim(
            anchor_line=None,
            content_lines=[b"old value\n"],
            baseline_reference=_boundary_reference(
                after_line=None,
                before_line=None,
            ),
        ),
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        deletion_claims,
        replacement_units=[
            ReplacementUnit(
                presence_lines=["1"],
                deletion_indices=[deletion_index],
            ),
        ],
    )

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((1, 1),)),
        deletion_claims,
        trust_baseline_coordinates=True,
    )

    assert result is None


def test_baseline_edit_planning_accepts_integer_source_range_metadata() -> None:
    """Replacement range metadata should retain its documented integer form."""
    source_lines = [b"new value\n"]
    working_lines = [b"old value\n"]
    deletion_claims = [
        AbsenceClaim(
            anchor_line=None,
            content_lines=[b"old value\n"],
            baseline_reference=_boundary_reference(
                after_line=None,
                before_line=None,
            ),
        ),
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        deletion_claims,
        replacement_units=[
            ReplacementUnit(
                presence_lines=[1],
                deletion_indices=[0],
            ),
        ],
    )

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((1, 1),)),
        deletion_claims,
        trust_baseline_coordinates=True,
    )

    assert result is not None
    assert list(result) == source_lines


def test_baseline_edit_planning_rejects_duplicate_deletion_binding() -> None:
    """One deletion claim must not be rebound by a later replacement unit."""
    source_lines = [b"new one\n", b"new two\n"]
    working_lines = [b"old\n", b"x\n", b"old\n"]
    claim = AbsenceClaim(
        anchor_line=None,
        content_lines=[b"old\n"],
        baseline_reference=_boundary_reference(
            after_line=None,
            before_line=2,
            before_content=b"x",
        ),
    )
    origin = ReplacementUnitOrigin(
        old_start=1,
        old_end=1,
        new_start=2,
        new_end=2,
        baseline_reference=BaselineReference(
            after_line=99,
            after_content=b"missing",
        ),
    )
    units = [
        ReplacementUnit(
            presence_lines=["1"],
            deletion_indices=[0],
        ),
        ReplacementUnit(
            presence_lines=["2"],
            deletion_indices=[0],
            origin=origin,
        ),
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        [claim],
        replacement_units=units,
    )
    key, choices = replacement_origin_choices_for_unit(
        claim,
        1,
        units[1],
        ((2, 2),),
        working_lines,
        max_results=10,
    )

    assert key is not None
    assert [(choice.choice_index, choice.position) for choice in choices] == [
        (1, 0),
        (2, 2),
    ]
    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((1, 2),)),
        [claim],
        resolution=MergeResolution({key: 2}),
    )

    assert result is None


def test_live_planning_ignores_already_present_insertion_groups() -> None:
    """A skipped no-op group should not need an ambiguous insertion boundary."""
    same = b"same\n"
    source_lines = [same, same, same, b"new\n"]
    working_lines = [same, same, same, b"old\n"]
    noop_reference = _boundary_reference(
        after_line=1,
        after_content=b"same",
        before_line=2,
        before_content=b"same",
    )
    replacement_reference = _boundary_reference(
        after_line=3,
        after_content=b"same",
        before_line=None,
    )
    claim = AbsenceClaim(
        anchor_line=3,
        content_lines=[b"old\n"],
        baseline_reference=replacement_reference,
    )
    ownership = BatchOwnership.from_presence_lines(
        ["2", "4"],
        [claim],
        baseline_references={
            2: noop_reference,
            4: replacement_reference,
        },
        replacement_units=[
            ReplacementUnit(
                presence_lines=["4"],
                deletion_indices=[0],
            ),
        ],
    )

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((2, 2), (4, 4))),
        [claim],
    )

    assert result is not None
    assert list(result) == source_lines


def test_live_planning_reinserts_payload_removed_by_replacement() -> None:
    """Matching insertion bytes should survive a planned target removal."""
    source_lines = [b"new\n", b"old\n"]
    working_lines = [b"old\n"]
    replacement_reference = _boundary_reference(
        after_line=None,
        before_line=None,
    )
    insertion_reference = _boundary_reference(
        after_line=None,
        before_line=1,
        before_content=b"old",
    )
    claim = AbsenceClaim(
        anchor_line=None,
        content_lines=[b"old\n"],
        baseline_reference=replacement_reference,
    )
    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        [claim],
        baseline_references={2: insertion_reference},
        replacement_units=[
            ReplacementUnit(
                presence_lines=["1"],
                deletion_indices=[0],
            ),
        ],
    )

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((1, 2),)),
        [claim],
    )

    assert result is not None
    assert list(result) == source_lines


def test_live_planning_rejects_partially_removed_matching_payload() -> None:
    """A partly removed no-op group should fail instead of duplicating content."""
    source_lines = [b"new\n", b"old\n", b"tail\n"]
    working_lines = [b"old\n", b"tail\n"]
    replacement_reference = _boundary_reference(
        after_line=None,
        before_line=2,
        before_content=b"tail",
    )
    insertion_reference = _boundary_reference(
        after_line=None,
        before_line=1,
        before_content=b"old",
    )
    claim = AbsenceClaim(
        anchor_line=None,
        content_lines=[b"old\n"],
        baseline_reference=replacement_reference,
    )
    ownership = BatchOwnership.from_presence_lines(
        ["1-3"],
        [claim],
        baseline_references={
            2: insertion_reference,
            3: insertion_reference,
        },
        replacement_units=[
            ReplacementUnit(
                presence_lines=["1"],
                deletion_indices=[0],
            ),
        ],
    )

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((1, 3),)),
        [claim],
    )

    assert result is None


def test_baseline_replacement_payload_stays_lazy(
    monkeypatch,
) -> None:
    """Planning should retain source ranges instead of collecting their lines."""

    def fail_collection(*_args, **_kwargs):
        raise AssertionError("baseline plans must use storage-backed ranges")

    line_count = 1000
    source_lines = _CountingLines(line_count)
    working_lines = [b"old value\n"]
    replacement_reference = _boundary_reference(
        after_line=None,
        before_line=None,
    )
    deletion_claims = [
        AbsenceClaim(
            anchor_line=None,
            content_lines=[b"old value\n"],
            baseline_reference=replacement_reference,
        ),
    ]
    ownership = BatchOwnership.from_presence_lines(
        [f"1-{line_count}"],
        deletion_claims,
        replacement_units=[
            ReplacementUnit(
                presence_lines=[f"1-{line_count}"],
                deletion_indices=[0],
            ),
        ],
    )
    monkeypatch.setattr(
        baseline_edits,
        "list",
        fail_collection,
        raising=False,
    )
    monkeypatch.setattr(
        baseline_edits,
        "sorted",
        fail_collection,
        raising=False,
    )

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((1, line_count),)),
        deletion_claims,
        trust_baseline_coordinates=True,
    )

    assert result is not None
    assert source_lines.read_count == 0
    assert sum(1 for line in result if line == b"new value\n") == line_count
    assert source_lines.read_count == line_count


def test_fragmented_replacement_planning_avoids_heap_selections(
    monkeypatch,
) -> None:
    """Fragmented replacement ranges should stay in mapped planning storage."""
    selected_ranges = tuple((line, line) for line in range(1, 2000, 2))
    range_spec = ",".join(str(start) for start, _end in selected_ranges)
    selected_lines = LineRanges.from_ranges(selected_ranges)
    source_lines = _CountingLines(1999)
    working_lines = [b"old value\n"]
    replacement_reference = _boundary_reference(
        after_line=None,
        before_line=None,
    )
    deletion_claims = [
        AbsenceClaim(
            anchor_line=None,
            content_lines=[b"old value\n"],
            baseline_reference=replacement_reference,
        ),
    ]
    ownership = BatchOwnership.from_presence_lines(
        [range_spec],
        deletion_claims,
        replacement_units=[
            ReplacementUnit(
                presence_lines=[range_spec],
                deletion_indices=[0],
            ),
        ],
    )

    def fail_heap_selection(*_args, **_kwargs):
        raise AssertionError("baseline planning rebuilt a heap selection")

    monkeypatch.setattr(LineRanges, "from_specs", fail_heap_selection)
    monkeypatch.setattr(LineRanges, "union", fail_heap_selection)
    monkeypatch.setattr(LineRanges, "difference", fail_heap_selection)

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        selected_lines,
        deletion_claims,
        trust_baseline_coordinates=True,
    )

    assert result is not None
    assert source_lines.read_count == 0
    assert sum(1 for _line in result) == 1000
    assert source_lines.read_count == 1000


def test_baseline_insertion_planning_does_not_flatten_references(
    monkeypatch,
) -> None:
    """Insertion planning should query ownership without copying its reference map."""
    line_count = 1000
    source_lines = _CountingLines(line_count)
    reference = _boundary_reference(
        after_line=None,
        before_line=None,
    )
    ownership = BatchOwnership.from_presence_lines(
        [f"1-{line_count}"],
        baseline_references={
            source_line: reference for source_line in range(1, line_count + 1)
        },
    )

    def fail_flatten():
        raise AssertionError("baseline references must not be flattened")

    monkeypatch.setattr(
        ownership,
        "presence_baseline_references",
        fail_flatten,
    )

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        [],
        ownership,
        LineRanges.from_ranges(((1, line_count),)),
        [],
        trust_baseline_coordinates=True,
    )

    assert result is not None
    assert source_lines.read_count == 0
    assert sum(1 for line in result if line == b"new value\n") == line_count
    assert source_lines.read_count == line_count


def test_baseline_insertion_planning_sorts_target_positions() -> None:
    """Source-order claims should still apply in target-coordinate order."""
    source_lines = [b"after first\n", b"before first\n"]
    working_lines = [b"first\n", b"second\n"]
    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        baseline_references={
            1: _boundary_reference(
                after_line=1,
                after_content=b"first",
                before_line=2,
                before_content=b"second",
            ),
            2: _boundary_reference(
                after_line=None,
                before_line=1,
                before_content=b"first",
            ),
        },
    )

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((1, 2),)),
        [],
        trust_baseline_coordinates=True,
    )

    assert result is not None
    assert list(result) == [
        b"before first\n",
        b"first\n",
        b"after first\n",
        b"second\n",
    ]


def test_baseline_edit_stream_closes_planning_workspace(
    monkeypatch,
) -> None:
    """Stopping edit output early should release mapped planning storage."""
    workspaces = []
    original_workspace = baseline_edits.MatcherWorkspace

    class TrackingWorkspace(original_workspace):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.was_closed = False
            workspaces.append(self)

        def close(self) -> None:
            self.was_closed = True
            super().close()

    source_lines = [b"first\n", b"second\n"]
    working_lines = [b"old\n"]
    replacement_reference = _boundary_reference(
        after_line=None,
        before_line=None,
    )
    deletion_claims = [
        AbsenceClaim(
            anchor_line=None,
            content_lines=[b"old\n"],
            baseline_reference=replacement_reference,
        ),
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        deletion_claims,
        replacement_units=[
            ReplacementUnit(
                presence_lines=["1-2"],
                deletion_indices=[0],
            ),
        ],
    )
    monkeypatch.setattr(
        baseline_edits,
        "MatcherWorkspace",
        TrackingWorkspace,
    )

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((1, 2),)),
        deletion_claims,
        trust_baseline_coordinates=True,
    )

    assert result is not None
    assert len(workspaces) == 1
    assert workspaces[0].was_closed is False
    assert next(result) == b"first\n"
    result.close()
    assert workspaces[0].was_closed is True


def test_baseline_edit_planning_closes_workspace_on_base_exception(
    monkeypatch,
) -> None:
    """Interrupted planning should release mapped scratch storage."""
    workspaces = []
    original_workspace = baseline_edits.MatcherWorkspace

    class TrackingWorkspace(original_workspace):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.was_closed = False
            workspaces.append(self)

        def close(self) -> None:
            self.was_closed = True
            super().close()

    source_lines = [b"new\n", b"extra\n"]
    working_lines = _InterruptingLines(1)
    replacement_reference = _boundary_reference(
        after_line=None,
        before_line=None,
    )
    deletion_claims = [
        AbsenceClaim(
            anchor_line=None,
            content_lines=[b"old\n"],
            baseline_reference=replacement_reference,
        ),
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        deletion_claims,
        replacement_units=[
            ReplacementUnit(
                presence_lines=["1"],
                deletion_indices=[0],
            ),
        ],
    )
    monkeypatch.setattr(
        baseline_edits,
        "MatcherWorkspace",
        TrackingWorkspace,
    )

    with pytest.raises(KeyboardInterrupt):
        baseline_edits.try_apply_baseline_coordinate_edits(
            source_lines,
            working_lines,
            ownership,
            LineRanges.from_ranges(((1, 1),)),
            deletion_claims,
            trust_baseline_coordinates=True,
        )

    assert len(workspaces) == 1
    assert workspaces[0].was_closed is True


def test_baseline_edit_stream_closes_workspace_on_base_exception(
    monkeypatch,
) -> None:
    """Interrupted payload reads should release mapped planning storage."""
    workspaces = []
    original_workspace = baseline_edits.MatcherWorkspace

    class TrackingWorkspace(original_workspace):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.was_closed = False
            workspaces.append(self)

        def close(self) -> None:
            self.was_closed = True
            super().close()

    source_lines = _InterruptingLines(1)
    working_lines = [b"old\n", b"tail\n"]
    replacement_reference = _boundary_reference(
        after_line=None,
        before_line=2,
        before_content=b"tail",
    )
    deletion_claims = [
        AbsenceClaim(
            anchor_line=None,
            content_lines=[b"old\n"],
            baseline_reference=replacement_reference,
        ),
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        deletion_claims,
        replacement_units=[
            ReplacementUnit(
                presence_lines=["1"],
                deletion_indices=[0],
            ),
        ],
    )
    monkeypatch.setattr(
        baseline_edits,
        "MatcherWorkspace",
        TrackingWorkspace,
    )

    result = baseline_edits.try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((1, 1),)),
        deletion_claims,
        trust_baseline_coordinates=True,
    )

    assert result is not None
    assert workspaces[0].was_closed is False
    with pytest.raises(KeyboardInterrupt):
        next(result)
    assert workspaces[0].was_closed is True


def test_source_alternative_can_consume_its_exact_mapped_neighbor() -> None:
    """An explicit old side may be the mapped line after its owned new side."""
    source_lines = [b"head\n", b"new\n", b"old\n", b"tail\n"]
    working_lines = [b"head\n", b"old\n", b"tail\n"]
    claim = AbsenceClaim(
        anchor_line=1,
        content_lines=[b"old\n"],
        source_alternative=True,
    )

    with match_lines(source_lines, working_lines) as mapping:
        mapped_source_lines = tuple(
            (source_line,)
            for source_line, _target_line in mapping.mapped_line_pairs()
        )
        assert baseline_replacement_edits._replacement_edit_fits_mapped_source_neighbors(
            (1, 2),
            claim,
            ((2, 2),),
            source_lines,
            len(working_lines),
            mapping,
            mapped_source_lines,
        )


def test_source_alternative_does_not_consume_unrelated_mapped_neighbor() -> None:
    """The source-alternative exception requires exact source adjacency."""
    source_lines = [
        b"head\n",
        b"new\n",
        b"unrelated\n",
        b"old\n",
        b"tail\n",
    ]
    working_lines = [b"head\n", b"unrelated\n", b"old\n", b"tail\n"]
    claim = AbsenceClaim(
        anchor_line=1,
        content_lines=[b"old\n"],
        source_alternative=True,
    )

    with match_lines(source_lines, working_lines) as mapping:
        mapped_source_lines = tuple(
            (source_line,)
            for source_line, _target_line in mapping.mapped_line_pairs()
        )
        assert not baseline_replacement_edits._replacement_edit_fits_mapped_source_neighbors(
            (2, 3),
            claim,
            ((2, 2),),
            source_lines,
            len(working_lines),
            mapping,
            mapped_source_lines,
        )


def test_trusted_target_replacement_ranges_stay_streamed(
    monkeypatch,
) -> None:
    """Trusted replacement provenance must not collect one tuple per child."""
    source_lines = [b"head\n", b"new\n", b"tail\n"]
    trusted_lines = [b"head\n", b"transformed\n", b"tail\n"]
    reference = _boundary_reference(
        after_line=1,
        after_content=b"head\n",
        before_line=3,
        before_content=b"tail\n",
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
        replacement_units=[ReplacementUnit(["2"], [0])],
    )
    original_from_ranges = LineRanges.from_ranges

    def reject_heap_range_list(cls, ranges):
        assert not isinstance(ranges, list)
        return original_from_ranges(ranges)

    monkeypatch.setattr(
        LineRanges,
        "from_ranges",
        classmethod(reject_heap_range_list),
    )

    with (
        match_lines(source_lines, trusted_lines) as source_mapping,
        match_lines(source_lines, trusted_lines) as source_trusted_mapping,
        match_lines(trusted_lines, trusted_lines) as trusted_mapping,
    ):
        trusted_ranges = (
            baseline_replacement_edits.trusted_target_replacement_source_ranges(
                source_lines,
                ownership,
                trusted_lines,
                trusted_lines,
                source_mapping,
                source_trusted_mapping,
                trusted_mapping,
            )
        )

    assert trusted_ranges.ranges() == ((2, 2),)


@pytest.mark.parametrize("old_side_is_present", [False, True])
def test_mapped_independent_deletion_spans_claimed_presence(
    old_side_is_present,
) -> None:
    """Mapped outer anchors should retain an already-applied adjacent new side."""
    source_lines = [b"head\n", b"new\n", b"tail\n"]
    working_lines = [b"staged\n", b"head\n", b"new\n"]
    if old_side_is_present:
        working_lines.append(b"old\n")
    working_lines.append(b"tail\n")
    deletion_claims = [
        AbsenceClaim(
            anchor_line=1,
            content_lines=[b"old\n"],
            baseline_reference=_boundary_reference(
                after_line=1,
                after_content=b"head\n",
                before_line=3,
                before_content=b"tail\n",
            ),
        )
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        deletion_claims,
    )

    with match_lines(source_lines, working_lines) as mapping:
        result = baseline_edits.try_apply_baseline_coordinate_edits(
            source_lines,
            working_lines,
            ownership,
            LineRanges.from_ranges(((2, 2),)),
            deletion_claims,
            source_to_working_mapping=mapping,
        )

    assert result is not None
    assert list(result) == [b"staged\n", *source_lines]


def test_mapped_independent_deletion_rejects_partial_old_side() -> None:
    """A mapped gap containing only part of a deletion must fail closed."""
    source_lines = [b"head\n", b"tail\n"]
    working_lines = [b"staged\n", b"head\n", b"old one\n", b"tail\n"]
    deletion_claims = [
        AbsenceClaim(
            anchor_line=1,
            content_lines=[b"old one\n", b"old two\n"],
            baseline_reference=_boundary_reference(
                after_line=1,
                after_content=b"head\n",
                before_line=4,
                before_content=b"tail\n",
            ),
        )
    ]
    ownership = BatchOwnership.from_presence_lines([], deletion_claims)

    with match_lines(source_lines, working_lines) as mapping:
        result = baseline_edits.try_apply_baseline_coordinate_edits(
            source_lines,
            working_lines,
            ownership,
            LineRanges.empty(),
            deletion_claims,
            source_to_working_mapping=mapping,
        )

    assert result is None


def test_mapped_independent_deletion_lookup_stays_linear(monkeypatch) -> None:
    """Many shifted removals should share one indexed source traversal."""
    deletion_count = 500
    source_lines: list[bytes] = []
    working_lines = [b"staged\n"]
    deletion_claims = []
    for index in range(deletion_count):
        anchor = f"anchor-{index}\n".encode()
        old = f"old-{index}\n".encode()
        tail = f"tail-{index}\n".encode()
        source_anchor = len(source_lines) + 1
        baseline_anchor = index * 3 + 1
        source_lines.extend((anchor, tail))
        working_lines.extend((anchor, old, tail))
        deletion_claims.append(
            AbsenceClaim(
                anchor_line=source_anchor,
                content_lines=[old],
                baseline_reference=_boundary_reference(
                    after_line=baseline_anchor,
                    after_content=anchor,
                    before_line=baseline_anchor + 2,
                    before_content=tail,
                ),
            )
        )
    ownership = BatchOwnership.from_presence_lines([], deletion_claims)

    with match_lines(source_lines, working_lines) as mapping:
        target_lookups = 0
        source_lookups = 0
        original_target_lookup = mapping.get_target_line_from_source_line
        original_source_lookup = mapping.get_source_line_from_target_line

        def count_target_lookup(source_line):
            nonlocal target_lookups
            target_lookups += 1
            return original_target_lookup(source_line)

        def count_source_lookup(target_line):
            nonlocal source_lookups
            source_lookups += 1
            return original_source_lookup(target_line)

        monkeypatch.setattr(
            mapping,
            "get_target_line_from_source_line",
            count_target_lookup,
        )
        monkeypatch.setattr(
            mapping,
            "get_source_line_from_target_line",
            count_source_lookup,
        )
        result = baseline_edits.try_apply_baseline_coordinate_edits(
            source_lines,
            working_lines,
            ownership,
            LineRanges.empty(),
            deletion_claims,
            source_to_working_mapping=mapping,
        )

    assert result is not None
    assert list(result) == [b"staged\n", *source_lines]
    assert target_lookups <= deletion_count * 3
    assert source_lookups <= deletion_count * 3


def test_overlapping_mapped_deletion_gaps_fail_before_content_scans(
    monkeypatch,
) -> None:
    """Overlapping shifted gaps must not trigger repeated full-gap matching."""
    deletion_count = 500
    additions = [
        f"added-{index}\n".encode()
        for index in range(deletion_count)
    ]
    source_lines = [b"head\n", *additions, b"tail\n"]
    working_lines = [b"staged\n", *source_lines]
    deletion_claims = []
    for source_anchor in range(1, deletion_count + 1):
        deletion_claims.append(
            AbsenceClaim(
                anchor_line=source_anchor,
                content_lines=[f"absent-{source_anchor}\n".encode()],
                baseline_reference=_boundary_reference(
                    after_line=source_anchor,
                    after_content=source_lines[source_anchor - 1],
                    before_line=deletion_count + 2,
                    before_content=b"tail\n",
                ),
            )
        )
    selected_presence = LineRanges.from_ranges(((2, deletion_count + 1),))
    ownership = BatchOwnership.from_presence_lines(
        [selected_presence.to_line_spec()],
        deletion_claims,
    )
    classification_calls = 0
    original_classify = (
        baseline_removal_edits.classify_replacement_old_side
    )

    def count_classification(*args, **kwargs):
        nonlocal classification_calls
        classification_calls += 1
        return original_classify(*args, **kwargs)

    monkeypatch.setattr(
        baseline_removal_edits,
        "classify_replacement_old_side",
        count_classification,
    )

    with match_lines(source_lines, working_lines) as mapping:
        result = baseline_edits.try_apply_baseline_coordinate_edits(
            source_lines,
            working_lines,
            ownership,
            selected_presence,
            deletion_claims,
            source_to_working_mapping=mapping,
        )

    assert result is None
    assert classification_calls == 0
