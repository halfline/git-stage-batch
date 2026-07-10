"""Indexed views over contiguous line ranges."""

from __future__ import annotations

from collections.abc import Sequence


class LineRangeView(Sequence[bytes]):
    """Indexed view over a contiguous range of lines."""

    def __init__(
        self,
        lines: Sequence[bytes],
        start: int,
        end: int,
    ) -> None:
        if start < 0 or end < start:
            raise ValueError("invalid line range")
        self._lines = lines
        self._start = start
        self._end = end

    def __len__(self) -> int:
        return self._end - self._start

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step == 1:
                return LineRangeView(
                    self._lines,
                    self._start + start,
                    self._start + stop,
                )
            return tuple(
                self[child_index]
                for child_index in range(start, stop, step)
            )

        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        return self._lines[self._start + index]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sequence):
            return NotImplemented
        if len(self) != len(other):
            return False
        return all(
            left_line == right_line
            for left_line, right_line in zip(self, other, strict=True)
        )
