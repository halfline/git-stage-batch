"""Store complete saved and live versions of a file together."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, replace
from itertools import chain
from pathlib import Path
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
from ..core.line_selection import LineRangeBuilder, LineRanges
from .file_state import SourceBoundOwnership
from .line_matching.comparison import (
    SemanticChangeKind,
    stream_semantic_change_runs,
)
from .line_matching.line_range_view import LineRangeView
from .line_matching.match import match_lines
from .line_matching.sequence_equality import line_sequences_equal
from .ownership.absence_claims import AbsenceClaim
from .ownership.claims import presence_claims_from_source_lines
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


@dataclass(frozen=True, slots=True)
class CompleteSourceReplacementChanges:
    """The changed lines between the two stored versions."""

    source_lines: Sequence[bytes]
    live_lines: Sequence[bytes]
    ownership: BatchOwnership
    original_deletion_index: int


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


def changes_from_complete_source_replacement(
    source_lines: Sequence[bytes],
    ownership: BatchOwnership,
    *,
    spool_dir: str | Path | None = None,
) -> CompleteSourceReplacementChanges | None:
    """Return only the lines that differ between the saved and live files."""
    spans = _complete_source_replacement_spans(source_lines, ownership)
    if spans is None:
        return None
    saved_span, live_span = spans
    saved_lines = LineRangeView(
        source_lines,
        saved_span.start.offset,
        saved_span.end.offset,
    )
    live_lines = LineRangeView(
        source_lines,
        live_span.start.offset,
        live_span.end.offset,
    )
    presence_builder = LineRangeBuilder()
    deletions: list[AbsenceClaim] = []
    replacement_units: list[ReplacementUnit] = []
    original_deletion = ownership.deletions[0]
    semantic_runs = stream_semantic_change_runs(
        live_lines,
        saved_lines,
        spool_dir=spool_dir,
    )
    try:
        for run in semantic_runs:
            presence_range: LineRanges | None = None
            if run.target_start is not None and run.target_end is not None:
                presence_builder.add_range(run.target_start, run.target_end)
                presence_range = LineRanges.from_ranges(
                    ((run.target_start, run.target_end),)
                )
            deletion_index: int | None = None
            if run.source_start is not None and run.source_end is not None:
                deletion_index = len(deletions)
                deletions.append(
                    AbsenceClaim(
                        anchor_line=run.target_anchor,
                        content_lines=LineRangeView(
                            live_lines,
                            run.source_start - 1,
                            run.source_end,
                        ),
                        baseline_reference=original_deletion.baseline_reference,
                    )
                )
            if (
                run.kind is SemanticChangeKind.REPLACEMENT
                and presence_range is not None
                and deletion_index is not None
            ):
                replacement_units.append(
                    ReplacementUnit(
                        presence_range.to_range_strings(),
                        [deletion_index],
                    )
                )
    finally:
        close_runs = getattr(semantic_runs, "close", None)
        if close_runs is not None:
            close_runs()

    presence_lines = presence_builder.finish()
    changed_ownership = BatchOwnership(
        presence_claims=presence_claims_from_source_lines(
            presence_lines,
            ownership.presence_baseline_references(),
        ),
        deletions=deletions,
        replacement_units=replacement_units,
    )
    return CompleteSourceReplacementChanges(
        saved_lines,
        live_lines,
        changed_ownership,
        original_deletion_index=0,
    )


def refresh_complete_source_replacement(
    source_lines: Sequence[bytes],
    bound_ownership: SourceBoundOwnership,
    *,
    rewritten_lines: Sequence[bytes],
    alternatives: ExplicitReplacementAlternatives | None,
) -> MaterializedCompleteSourceReplacement | None:
    """Update the live version after saving more of its lines.

    Do this only when each selected line has one match in the saved file. Keep
    the current live text outside the selection.
    """
    complete = resolve_complete_source_replacement(
        source_lines,
        bound_ownership,
    )
    if (
        complete is None
        or alternatives is None
        or not alternatives.uses_untracked_source
        or alternatives.live is None
        or alternatives.parent is not None
    ):
        return None

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
        saved_lines = LineRangeView(
            source_lines,
            complete.saved.span.start.offset,
            complete.saved.span.end.offset,
        )
        if not _selected_saved_lines_map_to_complete_snapshot(
            saved_lines,
            prior_worktree,
            alternatives,
        ):
            return None

        new_live = stack.enter_context(
            _buffer_without_span(rewritten_lines, alternatives.saved.span)
        )
        return _materialize_complete_source_replacement(
            bound_ownership.source_snapshot.path,
            saved_lines,
            new_live,
            template_ownership=bound_ownership.value,
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
    *,
    template_ownership: BatchOwnership | None = None,
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
        if template_ownership is None:
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
        else:
            deletion = replace(
                template_ownership.deletions[0],
                content_lines=live_view,
            )
            ownership = BatchOwnership(
                presence_claims=list(template_ownership.presence_claims),
                deletions=[deletion],
                replacement_units=list(template_ownership.replacement_units),
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


def _selected_saved_lines_map_to_complete_snapshot(
    complete_saved_lines: Sequence[bytes],
    prior_worktree: LineBuffer,
    alternatives: ExplicitReplacementAlternatives,
) -> bool:
    """Check that every selected line has one match in the saved file."""
    selected_span = alternatives.saved.span
    if selected_span.end.offset > len(prior_worktree):
        return False
    with match_lines(complete_saved_lines, prior_worktree) as mapping:
        return all(
            mapping.get_source_line_from_target_line(target_offset + 1) is not None
            for target_offset in range(
                selected_span.start.offset,
                selected_span.end.offset,
            )
        )
