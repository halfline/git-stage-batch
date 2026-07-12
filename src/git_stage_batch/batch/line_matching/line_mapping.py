"""Line mapping data structures."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol


class IntVector(Protocol):
    """Fixed-width integer vector used by line mappings."""

    def __len__(self) -> int: ...

    def __getitem__(self, index: int) -> int: ...

    def __setitem__(self, index: int, value: int) -> None: ...


@dataclass
class LineMapping:
    """Alignment between batch source lines and working tree lines."""

    source_to_target: IntVector
    target_to_source: IntVector
    _closed: bool = field(default=False, init=False, repr=False)
    _close_on_exit: bool = field(default=True, init=False, repr=False)

    def __enter__(self) -> LineMapping:
        self._require_open()
        self._close_on_exit = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._close_on_exit:
            self.close()

    def detach(self) -> LineMapping:
        """Transfer ownership out of the current context manager."""
        self._require_open()
        self._close_on_exit = False
        return self

    def close(self) -> None:
        """Close owned vector storage."""
        if self._closed:
            return

        _close_vector(self.source_to_target)
        _close_vector(self.target_to_source)
        self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def is_source_line_present(
        self,
        source_line: int,
    ) -> bool:
        """Check if a batch source line is present in working tree."""
        self._require_open()
        return _lookup_line_mapping(self.source_to_target, source_line) is not None

    def get_target_line_from_source_line(
        self,
        source_line: int,
    ) -> int | None:
        """Map batch source line to working tree line."""
        self._require_open()
        return _lookup_line_mapping(self.source_to_target, source_line)

    def get_source_line_from_target_line(
        self,
        target_line: int,
    ) -> int | None:
        """Map working tree line to batch source line."""
        self._require_open()
        return _lookup_line_mapping(self.target_to_source, target_line)

    def mapped_line_pairs(self) -> Iterator[tuple[int, int]]:
        """Yield mapped source/target line pairs in source-line order."""
        self._require_open()
        for source_index in range(len(self.source_to_target)):
            target_line = self.source_to_target[source_index]
            if target_line != 0:
                yield source_index + 1, target_line

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("line mapping is closed")


def _close_vector(vector: IntVector) -> None:
    close = getattr(vector, "close", None)
    if close is not None:
        close()


def _lookup_line_mapping(mapping: IntVector, line_number: int) -> int | None:
    if line_number < 1 or line_number > len(mapping):
        return None

    mapped_line = mapping[line_number - 1]
    if mapped_line == 0:
        return None
    return mapped_line
