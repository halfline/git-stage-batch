"""Helpers for expressing replacement text in batch source space."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..core.line_selection import format_line_ids
from ..editor import EditorBuffer
from ..utils.text import bytes_to_lines
from .ownership import BatchOwnership, AbsenceClaim, ReplacementUnit


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


@dataclass(slots=True)
class ReplacementBatchView:
    """Batch source buffer and ownership produced for replacement text."""

    source_buffer: EditorBuffer
    ownership: BatchOwnership

    def close(self) -> None:
        """Close the generated source buffer."""
        self.source_buffer.close()

    def __enter__(self) -> ReplacementBatchView:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _format_presence_lines(line_numbers: list[int]) -> list[str]:
    """Format presence source line numbers as normalized range strings."""
    if not line_numbers:
        return []
    return [format_line_ids(line_numbers)]


def build_replacement_batch_view_from_lines(
    source_lines: Sequence[bytes],
    ownership: BatchOwnership,
    replacement_text: str | ReplacementPayload,
) -> ReplacementBatchView:
    """Build replacement source content from an indexed byte-line sequence."""
    claimed_source_lines = sorted(ownership.presence_line_set())
    payload = coerce_replacement_payload(replacement_text)
    replacement_lines = replacement_line_chunks(payload)

    if claimed_source_lines:
        expected_claimed = list(range(claimed_source_lines[0], claimed_source_lines[-1] + 1))
        if claimed_source_lines != expected_claimed:
            raise ValueError("Replacement selection must resolve to one contiguous batch-source line range.")

        start_line = claimed_source_lines[0]
        end_line = claimed_source_lines[-1]
        removed_count = end_line - start_line + 1
        added_count = len(replacement_lines)

        new_claimed_lines = list(range(start_line, start_line + added_count))
        new_deletions = []
        for deletion in ownership.deletions:
            anchor = deletion.anchor_line
            if anchor is None:
                new_anchor = None
            elif anchor < start_line:
                new_anchor = anchor
            elif anchor > end_line:
                new_anchor = anchor - removed_count + added_count
            elif added_count > 0:
                new_anchor = start_line + added_count - 1
            elif start_line > 1:
                new_anchor = start_line - 1
            else:
                new_anchor = None

            new_deletions.append(AbsenceClaim(
                anchor_line=new_anchor,
                content_lines=deletion.content_lines,
            ))

        return ReplacementBatchView(
            source_buffer=EditorBuffer.from_chunks(
                _replacement_source_chunks(
                    source_lines=source_lines,
                    prefix_end=start_line - 1,
                    replacement_lines=replacement_lines,
                    suffix_start=end_line,
                )
            ),
            ownership=BatchOwnership.from_presence_lines(
                _format_presence_lines(new_claimed_lines),
                new_deletions,
                replacement_units=[
                    ReplacementUnit(
                        presence_lines=_format_presence_lines(new_claimed_lines),
                        deletion_indices=list(range(len(new_deletions))),
                    )
                ] if new_claimed_lines and new_deletions else [],
            ),
        )

    distinct_anchors = {deletion.anchor_line for deletion in ownership.deletions}
    if len(distinct_anchors) > 1:
        raise ValueError("Replacement selection must resolve to one contiguous batch-source region.")

    anchor_line = next(iter(distinct_anchors), None)
    insert_at = 0 if anchor_line is None else anchor_line
    added_count = len(replacement_lines)

    if added_count == 0:
        new_claimed_lines: list[int] = []
    elif anchor_line is None:
        new_claimed_lines = list(range(1, added_count + 1))
    else:
        new_claimed_lines = list(range(anchor_line + 1, anchor_line + added_count + 1))

    new_deletions = []
    for deletion in ownership.deletions:
        if deletion.anchor_line is None:
            new_anchor = None
        elif anchor_line is None:
            new_anchor = deletion.anchor_line + added_count
        elif deletion.anchor_line <= anchor_line:
            new_anchor = deletion.anchor_line
        else:
            new_anchor = deletion.anchor_line + added_count

        new_deletions.append(AbsenceClaim(
            anchor_line=new_anchor,
            content_lines=deletion.content_lines,
        ))

    return ReplacementBatchView(
        source_buffer=EditorBuffer.from_chunks(
            _replacement_source_chunks(
                source_lines=source_lines,
                prefix_end=insert_at,
                replacement_lines=replacement_lines,
                suffix_start=insert_at,
            )
        ),
        ownership=BatchOwnership.from_presence_lines(
            _format_presence_lines(new_claimed_lines),
            new_deletions,
            replacement_units=[
                ReplacementUnit(
                    presence_lines=_format_presence_lines(new_claimed_lines),
                    deletion_indices=list(range(len(new_deletions))),
                )
            ] if new_claimed_lines and new_deletions else [],
        ),
    )


def _replacement_source_chunks(
    *,
    source_lines: Sequence[bytes],
    prefix_end: int,
    replacement_lines: Iterable[bytes],
    suffix_start: int,
) -> Iterable[bytes]:
    """Yield replacement source content without materializing source lines."""
    for line_index in range(prefix_end):
        yield source_lines[line_index]

    yield from replacement_lines

    for line_index in range(suffix_start, len(source_lines)):
        yield source_lines[line_index]


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


def replacement_line_chunks(payload: ReplacementPayload) -> list[bytes]:
    """Split replacement bytes into exact line chunks."""
    if not payload.exact:
        return [line + b"\n" for line in payload.data.splitlines()]
    return list(bytes_to_lines([payload.data]))


def replacement_line_bodies(payload: ReplacementPayload) -> list[bytes]:
    """Return editor line bodies while preserving CRLF as body CR bytes."""
    if not payload.exact:
        return payload.data.splitlines()
    bodies: list[bytes] = []
    for line in replacement_line_chunks(payload):
        if line.endswith(b"\n"):
            bodies.append(line[:-1])
        else:
            bodies.append(line)
    return bodies
