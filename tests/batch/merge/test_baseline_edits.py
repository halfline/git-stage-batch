"""Focused tests for baseline-coordinate edit planning."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from git_stage_batch.batch.merge import baseline_edits
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.batch.ownership.replacement_units import ReplacementUnit
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

    result = baseline_edits.try_apply_baseline_replacement_units(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((1, 2),)),
        deletion_claims,
    )

    assert result is not None
    assert list(result) == [b"new value\n", b"inserted\n", b"tail\n"]


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
        result = baseline_edits.try_apply_baseline_replacement_units(
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

    result = baseline_edits.try_apply_baseline_replacement_units(
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

    result = baseline_edits.try_apply_baseline_replacement_units(
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

    result = baseline_edits.try_apply_baseline_replacement_units(
        source_lines,
        working_lines,
        ownership,
        LineRanges.from_ranges(((1, 1),)),
        deletion_claims,
        trust_baseline_coordinates=True,
    )

    assert result is not None
    assert list(result) == source_lines


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

    result = baseline_edits.try_apply_baseline_replacement_units(
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

    result = baseline_edits.try_apply_baseline_replacement_units(
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

    result = baseline_edits.try_apply_baseline_replacement_units(
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

    result = baseline_edits.try_apply_baseline_replacement_units(
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

    result = baseline_edits.try_apply_baseline_replacement_units(
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
        baseline_edits.try_apply_baseline_replacement_units(
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

    result = baseline_edits.try_apply_baseline_replacement_units(
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
