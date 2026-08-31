"""Store complete saved and live versions of a file together."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from itertools import chain
from types import TracebackType

from ..core.buffer import LineBuffer
from ..core.coordinates import (
    BatchSourceSpace,
    LineSpan,
    RewrittenWorktreeSpace,
    SnapshotSpan,
    WorktreeSpace,
    content_snapshot,
    require_same_snapshot,
)
from ..core.text_lines import normalize_line_endings
from ..core.line_selection import LineRanges
from .file_state import SourceBoundOwnership
from .line_matching.line_range_view import LineRangeView
from .line_matching.sequence_equality import line_sequences_equal
from .ownership.absence_claims import AbsenceClaim
from .ownership.model import BatchOwnership
from .ownership.references import BaselineReference
from .ownership.replacement_units import ReplacementUnit
from .replacement_alternatives import ExplicitReplacementAlternatives


@dataclass(frozen=True, slots=True)
class CompleteSourceReplacementAlternative:
    """Where two complete versions of a file are stored in a batch."""

    saved: SnapshotSpan[BatchSourceSpace]
    live: SnapshotSpan[BatchSourceSpace]

    def __post_init__(self) -> None:
        require_same_snapshot(self.saved.snapshot, self.live.snapshot)
        if self.saved.span.start.offset != 0:
            raise ValueError("complete saved replacement does not start at SOF")
        if self.live.span.start != self.saved.span.end:
            raise ValueError("complete replacement alternatives are not adjacent")
        if self.live.span.end.offset != self.live.snapshot.line_count:
            raise ValueError("complete live replacement does not end at EOF")


@dataclass(slots=True)
class MaterializedCompleteSourceReplacement:
    """A temporary source file and the batch claims for both versions."""

    source_buffer: LineBuffer
    bound_ownership: SourceBoundOwnership

    def close(self) -> None:
        """Release the temporary source buffer."""
        self.source_buffer.close()

    def __enter__(self) -> MaterializedCompleteSourceReplacement:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _complete_source_replacement_spans(
    source_lines: Sequence[bytes],
    ownership: BatchOwnership,
) -> tuple[LineSpan[BatchSourceSpace], LineSpan[BatchSourceSpace]] | None:
    resolved = ownership.resolve()
    if (
        len(ownership.deletions) != 1
        or len(ownership.replacement_units) != 1
        or len(resolved.replacement_alternatives) != 1
        or not ownership.deletions[0].complete_file_pair
    ):
        return None
    alternative = resolved.replacement_alternatives[0]
    if (
        alternative.deletion_index != 0
        or alternative.unit_index != 0
        or len(alternative.live_payload) != 1
        or alternative.saved.start.offset != 0
        or alternative.live_envelope.end.offset != len(source_lines)
        or resolved.presence_line_set != alternative.saved_lines
        or alternative.absence_claim.anchor.offset != 0
    ):
        return None
    for source_offset, content_line in zip(
        alternative.iter_live_source_offsets(),
        alternative.absence_claim.content_lines,
        strict=True,
    ):
        if normalize_line_endings(bytes(source_lines[source_offset])) != (
            normalize_line_endings(bytes(content_line))
        ):
            return None
    return alternative.saved, alternative.live_payload[0]


def resolve_complete_source_replacement(
    source_lines: Sequence[bytes],
    bound_ownership: SourceBoundOwnership,
) -> CompleteSourceReplacementAlternative | None:
    """Return two versions that together fill the stored source.

    Return ``None`` if the source has other claims. The batch must own exactly
    the first version before the second can be updated directly.
    """
    require_same_snapshot(
        bound_ownership.source_snapshot,
        content_snapshot(
            bound_ownership.source_snapshot.path,
            source_lines,
            space=BatchSourceSpace,
        ),
    )
    spans = _complete_source_replacement_spans(
        source_lines,
        bound_ownership.value,
    )
    if spans is None:
        return None
    saved_span, live_span = spans
    source_snapshot = bound_ownership.source_snapshot
    return CompleteSourceReplacementAlternative(
        saved=SnapshotSpan(source_snapshot, saved_span),
        live=SnapshotSpan(source_snapshot, live_span),
    )


def materialize_untracked_source_replacement(
    rewritten_lines: Sequence[bytes],
    alternatives: ExplicitReplacementAlternatives,
) -> MaterializedCompleteSourceReplacement:
    """Store both versions when an untracked file is first changed."""
    if (
        not alternatives.uses_untracked_source
        or alternatives.live is None
        or alternatives.parent is not None
        or alternatives.edit.plan.baseline_snapshot.line_count != 0
    ):
        raise ValueError("replacement is not an untracked source alternative")
    require_same_snapshot(
        alternatives.edit.rewritten_snapshot,
        content_snapshot(
            alternatives.edit.rewritten_snapshot.path,
            rewritten_lines,
            space=RewrittenWorktreeSpace,
        ),
    )
    with ExitStack() as stack:
        saved_file = stack.enter_context(
            _buffer_without_span(rewritten_lines, alternatives.live.span)
        )
        require_same_snapshot(
            alternatives.edit.source_snapshot,
            content_snapshot(
                alternatives.edit.source_snapshot.path,
                saved_file,
                space=WorktreeSpace,
            ),
        )
        live_file = stack.enter_context(
            _buffer_without_span(rewritten_lines, alternatives.saved.span)
        )
        return _materialize_complete_source_replacement(
            alternatives.edit.source_snapshot.path,
            saved_file,
            live_file,
        )


def promote_untracked_presence_to_complete_source_replacement(
    source_lines: Sequence[bytes],
    bound_ownership: SourceBoundOwnership,
    *,
    rewritten_lines: Sequence[bytes],
    alternatives: ExplicitReplacementAlternatives | None,
) -> MaterializedCompleteSourceReplacement | None:
    """Store both versions before changing part of a newly added file.

    First check that removing this batch recreates the current worktree. If it
    does not, another batch may also have changed the file.
    """
    ownership = bound_ownership.value
    if (
        alternatives is None
        or not alternatives.uses_untracked_source
        or alternatives.live is None
        or alternatives.parent is not None
        or ownership.deletions
        or ownership.replacement_units
        or not ownership.presence_claims
    ):
        return None
    require_same_snapshot(
        bound_ownership.source_snapshot,
        content_snapshot(
            bound_ownership.source_snapshot.path,
            source_lines,
            space=BatchSourceSpace,
        ),
    )
    require_same_snapshot(
        alternatives.edit.rewritten_snapshot,
        content_snapshot(
            alternatives.edit.rewritten_snapshot.path,
            rewritten_lines,
            space=RewrittenWorktreeSpace,
        ),
    )
    with ExitStack() as stack:
        prior_worktree = stack.enter_context(
            _buffer_without_span(rewritten_lines, alternatives.live.span)
        )
        require_same_snapshot(
            alternatives.edit.source_snapshot,
            content_snapshot(
                alternatives.edit.source_snapshot.path,
                prior_worktree,
                space=WorktreeSpace,
            ),
        )
        expected_predecessor = stack.enter_context(
            _buffer_without_ranges(
                source_lines,
                ownership.presence_line_set(),
            )
        )
        if not line_sequences_equal(expected_predecessor, prior_worktree):
            return None
        new_live = stack.enter_context(
            _buffer_without_span(rewritten_lines, alternatives.saved.span)
        )
        return _materialize_complete_source_replacement(
            bound_ownership.source_snapshot.path,
            source_lines,
            new_live,
        )


def _materialize_complete_source_replacement(
    path: str,
    saved_file: Sequence[bytes],
    live_file: Sequence[bytes],
) -> MaterializedCompleteSourceReplacement:
    """Store both complete versions and build their batch claims."""
    if not saved_file or not live_file:
        raise ValueError("complete replacement snapshots must be non-empty")
    new_source = LineBuffer.from_chunks(chain(saved_file, live_file))
    try:
        saved_line_count = len(saved_file)
        live_view = LineRangeView(
            new_source,
            saved_line_count,
            len(new_source),
        )
        deletion = AbsenceClaim(
            content_lines=live_view,
            baseline_reference=BaselineReference(
                after_line=None,
                before_line=None,
                has_before_line=True,
            ),
            source_alternative=True,
            complete_file_pair=True,
        )
        source_range = f"1-{saved_line_count}"
        ownership = BatchOwnership.from_presence_lines(
            [source_range],
            [deletion],
            replacement_units=[ReplacementUnit([source_range], [0])],
        )
        bound_ownership = SourceBoundOwnership(
            content_snapshot(path, new_source, space=BatchSourceSpace),
            ownership,
        )
        if resolve_complete_source_replacement(new_source, bound_ownership) is None:
            raise ValueError("materialized complete replacement lost its shape")
        return MaterializedCompleteSourceReplacement(
            new_source,
            bound_ownership,
        )
    except BaseException:
        new_source.close()
        raise


def _buffer_without_ranges(
    lines: Sequence[bytes],
    excluded_lines: LineRanges,
) -> LineBuffer:
    """Return a buffer that omits the given one-based source ranges."""

    def iter_chunks() -> Iterator[bytes]:
        previous_end = 0
        for start, end in excluded_lines.ranges():
            if start <= previous_end or end > len(lines):
                raise ValueError("excluded source range is outside its snapshot")
            yield from LineRangeView(lines, previous_end, start - 1)
            previous_end = end
        yield from LineRangeView(lines, previous_end, len(lines))

    return LineBuffer.from_chunks(iter_chunks())


def _buffer_without_span(
    lines: Sequence[bytes],
    span: LineSpan[RewrittenWorktreeSpace],
) -> LineBuffer:
    """Return a buffer with one line span removed."""
    start = span.start.offset
    end = span.end.offset
    if end > len(lines):
        raise ValueError("removed replacement span is outside its snapshot")
    return LineBuffer.from_chunks(
        chain(
            LineRangeView(lines, 0, start),
            LineRangeView(lines, end, len(lines)),
        )
    )
