"""Tests for batch display helpers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import overload

from git_stage_batch.batch.ownership.display_lines import (
    build_display_lines_from_batch_source_lines,
)
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim


class _NoLenByteLines(Sequence[bytes]):
    """Byte-line sequence that fails if display construction asks for length."""

    def __init__(self, lines: Iterable[bytes]) -> None:
        self._lines = tuple(lines)
        self.accessed_indexes: list[int] = []

    def __len__(self) -> int:
        raise AssertionError("display construction should not require len()")

    @overload
    def __getitem__(self, index: int) -> bytes: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[bytes, ...]: ...

    def __getitem__(self, index: int | slice) -> bytes | tuple[bytes, ...]:
        if isinstance(index, slice):
            raise AssertionError("display construction should use indexed reads")
        self.accessed_indexes.append(index)
        return self._lines[index]


class _IterationGuardedLineSelection:
    """Line selection that rejects full expansion in display tests."""

    def __init__(self, ranges: tuple[tuple[int, int], ...]) -> None:
        self._ranges = ranges

    def __contains__(self, line_number: object) -> bool:
        if type(line_number) is not int:
            return False
        return any(start <= line_number <= end for start, end in self._ranges)

    def __bool__(self) -> bool:
        return bool(self._ranges)

    def __iter__(self):
        raise AssertionError("claimed selection should not be expanded")

    def ranges(self) -> tuple[tuple[int, int], ...]:
        return self._ranges


class _RangeBackedDisplayOwnership:
    """Ownership stub that returns a guarded range-backed selection."""

    def __init__(
        self,
        selection: _IterationGuardedLineSelection,
        deletions: Iterable[AbsenceClaim] = (),
    ) -> None:
        self._selection = selection
        self.deletions = list(deletions)

    def presence_line_set(self) -> _IterationGuardedLineSelection:
        return self._selection


def test_display_builder_accepts_non_list_byte_line_sequences(line_sequence):
    """Batch display construction accepts indexed byte-line sequences."""
    source_lines = line_sequence([
        b"line 1\n",
        b"line 2\n",
        b"line 3\n",
    ])
    ownership = BatchOwnership.from_presence_lines(
        ["1,3"],
        [
            AbsenceClaim(
                anchor_line=1,
                content_lines=[b"deleted\n"],
            ),
        ],
    )

    display_lines = build_display_lines_from_batch_source_lines(
        source_lines,
        ownership,
        context_lines=0,
    )

    assert [line["content"] for line in display_lines] == [
        "line 1\n",
        "deleted\n",
        "... 1 more line ...\n",
        "line 3\n",
    ]
    assert [line["type"] for line in display_lines] == [
        "claimed",
        "deletion",
        "gap",
        "claimed",
    ]
    assert [line["id"] for line in display_lines] == [1, 2, None, 3]


def test_display_builder_does_not_require_source_line_count():
    """Display construction reads the requested source indexes directly."""
    source_lines = _NoLenByteLines(
        f"line {line_number}\n".encode("utf-8")
        for line_number in range(1, 1001)
    )
    ownership = BatchOwnership.from_presence_lines(["500"], [])

    display_lines = build_display_lines_from_batch_source_lines(
        source_lines,
        ownership,
        context_lines=1,
    )

    assert [line["content"] for line in display_lines] == [
        "line 499\n",
        "line 500\n",
        "line 501\n",
    ]
    assert [line["id"] for line in display_lines] == [None, 1, None]
    assert source_lines.accessed_indexes == [498, 499, 500]


def test_display_builder_uses_ranges_without_expanding_claims():
    """Display construction keeps claimed ranges compact."""
    source_lines = _NoLenByteLines(
        f"line {line_number}\n".encode("utf-8")
        for line_number in range(1, 101)
    )
    ownership = _RangeBackedDisplayOwnership(
        _IterationGuardedLineSelection(((50, 52),)),
        [
            AbsenceClaim(
                anchor_line=60,
                content_lines=[b"deleted\n"],
            ),
        ],
    )

    display_lines = build_display_lines_from_batch_source_lines(
        source_lines,
        ownership,
        context_lines=1,
    )

    assert [line["content"] for line in display_lines] == [
        "line 49\n",
        "line 50\n",
        "line 51\n",
        "line 52\n",
        "line 53\n",
        "... 5 more lines ...\n",
        "line 59\n",
        "line 60\n",
        "deleted\n",
        "line 61\n",
    ]
    assert [line["type"] for line in display_lines] == [
        "context",
        "claimed",
        "claimed",
        "claimed",
        "context",
        "gap",
        "context",
        "context",
        "deletion",
        "context",
    ]
    assert source_lines.accessed_indexes == [48, 49, 50, 51, 52, 58, 59, 60]
