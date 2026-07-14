"""Neutral replacement text payloads and byte-line helpers."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import overload

from .buffer import LineBuffer


@dataclass(frozen=True, slots=True)
class ReplacementPayload:
    """Exact replacement bytes plus optional argv display text."""

    data: bytes
    display_text: str | None = None
    exact: bool = True

    @classmethod
    def from_text(cls, text: str, *, exact: bool = True) -> "ReplacementPayload":
        return cls(
            text.encode("utf-8", errors="surrogateescape"),
            display_text=text,
            exact=exact,
        )

    @property
    def has_trailing_lf(self) -> bool:
        return self.data.endswith(b"\n")

    def as_text(self) -> str:
        return self.data.decode("utf-8", errors="surrogateescape")


class ReplacementText(str):
    """String-compatible replacement value carrying exact source bytes."""

    def __new__(
        cls,
        text: str,
        *,
        data: bytes | None = None,
        exact: bool = True,
    ) -> "ReplacementText":
        obj = str.__new__(cls, text)
        obj.data = text.encode("utf-8", errors="surrogateescape") if data is None else data
        obj.exact = exact
        return obj


def coerce_replacement_payload(
    replacement: str | bytes | ReplacementPayload,
) -> ReplacementPayload:
    """Return exact replacement bytes for legacy str callers and new payloads."""
    if isinstance(replacement, ReplacementPayload):
        return replacement
    if isinstance(replacement, ReplacementText):
        return ReplacementPayload(
            replacement.data,
            display_text=str(replacement) if not replacement.exact else None,
            exact=replacement.exact,
        )
    if isinstance(replacement, bytes):
        return ReplacementPayload(replacement)
    # Plain str is the legacy command-internal API. Preserve its historical
    # line-oriented behavior; CLI --as-stdin uses ReplacementText for exact bytes.
    return ReplacementPayload.from_text(replacement, exact=False)


class _ReplacementLineBodies(Sequence[bytes]):
    """Lazy editor-line bodies over exact replacement chunks."""

    def __init__(
        self,
        lines: Sequence[bytes],
        indices: range | None = None,
    ) -> None:
        self._lines = lines
        self._indices = range(len(lines)) if indices is None else indices

    def __len__(self) -> int:
        return len(self._indices)

    @overload
    def __getitem__(self, index: int) -> bytes: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[bytes]: ...

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        if isinstance(index, slice):
            return _ReplacementLineBodies(self._lines, self._indices[index])
        try:
            line_index = self._indices[index]
        except IndexError as exc:
            raise IndexError(index) from exc
        line = self._lines[line_index]
        if line.endswith(b"\r\n"):
            line = line[:-2]
        elif line.endswith(b"\n"):
            line = line[:-1]
        return line

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sequence):
            return NotImplemented
        return len(self) == len(other) and all(
            self[index] == other[index]
            for index in range(len(self))
        )


_LEGACY_LINE_BREAKS = frozenset((10, 13))


def _legacy_replacement_line_chunks(data: bytes) -> Iterator[bytes]:
    """Yield legacy splitlines-compatible chunks normalized to LF."""
    start = 0
    index = 0
    while index < len(data):
        byte = data[index]
        if byte not in _LEGACY_LINE_BREAKS:
            index += 1
            continue
        yield data[start:index] + b"\n"
        index += 1
        if byte == 13 and index < len(data) and data[index] == 10:
            index += 1
        start = index
    if start < len(data):
        yield data[start:] + b"\n"


@contextmanager
def replacement_line_chunks(
    payload: ReplacementPayload,
) -> Iterator[Sequence[bytes]]:
    """Expose replacement bytes as a scoped indexed line sequence."""
    buffer = (
        LineBuffer.from_bytes(payload.data)
        if payload.exact
        else LineBuffer.from_chunks(_legacy_replacement_line_chunks(payload.data))
    )
    with buffer:
        yield buffer


@contextmanager
def replacement_line_bodies(
    payload: ReplacementPayload,
) -> Iterator[Sequence[bytes]]:
    """Expose scoped editor line bodies without retaining per-line objects."""
    with replacement_line_chunks(payload) as lines:
        yield _ReplacementLineBodies(lines)
