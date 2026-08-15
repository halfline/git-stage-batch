"""Indexed views over contiguous line ranges."""

from __future__ import annotations

from collections.abc import Sequence
from typing import overload


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
        self._indices = range(start, end)

    @classmethod
    def _from_indices(
        cls,
        lines: Sequence[bytes],
        indices: range,
    ) -> LineRangeView:
        view = cls.__new__(cls)
        view._lines = lines
        view._indices = indices
        return view

    def __len__(self) -> int:
        return len(self._indices)

    @overload
    def __getitem__(self, index: int) -> bytes: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[bytes]: ...

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        if isinstance(index, slice):
            return self._from_indices(
                self._lines,
                self._indices[index],
            )

        try:
            line_index = self._indices[index]
        except IndexError as error:
            raise IndexError(index) from error
        # Acquired line buffers expose scoped no-copy views even though their
        # public sequence contract is bytes.  Materialize one requested line
        # so a range value cannot outlive that acquisition scope.
        return bytes(self._lines[line_index])

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sequence):
            return NotImplemented
        if len(self) != len(other):
            return False
        return all(
            left_line == right_line
            for left_line, right_line in zip(self, other, strict=True)
        )
