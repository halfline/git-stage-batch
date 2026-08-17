"""Line mapping data structures."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Protocol

from ...core.mapped_storage import MappedIntVector
from ...core.resource_cleanup import close_resources_preserving_first


_MAX_UINT32 = (1 << 32) - 1


def allocate_mapping_vector(
    size: int,
    max_line_number: int,
    *,
    spool_dir: str | Path | None = None,
) -> MappedIntVector:
    """Allocate one zero-filled line-number vector."""
    return MappedIntVector(
        size,
        width=4 if max_line_number <= _MAX_UINT32 else 8,
        fill=0,
        spool_dir=spool_dir,
    )


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
    may_have_unmapped_equal_lines: bool = field(default=True, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def __enter__(self) -> LineMapping:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        close_resources_preserving_first(
            (self,),
            suppress_errors=exc_type is not None,
        )

    def close(self) -> None:
        """Close owned vector storage."""
        if self._closed:
            return

        _close_vectors(self.source_to_target, self.target_to_source)
        self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except BaseException:
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


def allocate_line_mapping(
    source_line_count: int,
    target_line_count: int,
    *,
    spool_dir: str | Path | None = None,
) -> LineMapping:
    """Allocate an empty bidirectional mapping in bounded-heap storage."""
    max_line_number = max(source_line_count, target_line_count)
    source_to_target: MappedIntVector | None = None
    target_to_source: MappedIntVector | None = None
    try:
        source_to_target = allocate_mapping_vector(
            source_line_count,
            max_line_number,
            spool_dir=spool_dir,
        )
        target_to_source = allocate_mapping_vector(
            target_line_count,
            max_line_number,
            spool_dir=spool_dir,
        )
        return LineMapping(source_to_target, target_to_source)
    except BaseException:
        try:
            _close_vectors(source_to_target, target_to_source)
        except BaseException:
            pass
        raise


def _close_vector(vector: IntVector) -> None:
    close = getattr(vector, "close", None)
    if close is not None:
        close()


def _close_vectors(*vectors: IntVector | None) -> None:
    """Close every vector while preserving the first close failure."""
    first_error: BaseException | None = None
    for vector in vectors:
        if vector is None:
            continue
        try:
            _close_vector(vector)
        except BaseException as error:
            if first_error is None:
                first_error = error
    if first_error is not None:
        raise first_error


def _lookup_line_mapping(mapping: IntVector, line_number: int) -> int | None:
    if line_number < 1 or line_number > len(mapping):
        return None

    mapped_line = mapping[line_number - 1]
    if mapped_line == 0:
        return None
    return mapped_line
