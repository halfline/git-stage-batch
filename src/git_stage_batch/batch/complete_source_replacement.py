"""Store complete saved and live versions of a file together."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from itertools import chain
from pathlib import Path
from types import TracebackType

from ..core.buffer import LineBuffer
from ..core.coordinates import (
    BatchSourceSpace,
    LineBoundary,
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
    SemanticChangeRun,
    stream_semantic_change_runs,
)
from .line_matching.line_mapping import LineMapping
from .line_matching.line_range_view import LineRangeView
from .line_matching.match import match_lines
from .line_matching.match_workspace import MatcherWorkspace
from .line_matching.sequence_search import iter_exact_sequence_indexes
from .line_matching.sequence_equality import line_sequences_equal
from .ownership.absence_claims import AbsenceClaim
from .ownership.claims import presence_claims_from_source_lines
from .ownership.model import BatchOwnership
from .ownership.references import BaselineReference, KnownBoundary
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
    first_change: SemanticChangeRun | None
    last_change: SemanticChangeRun | None


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
    *,
    allow_unmarked_empty_baseline: bool = False,
) -> tuple[LineSpan[BatchSourceSpace], LineSpan[BatchSourceSpace]] | None:
    resolved = ownership.resolve()
    if (
        len(ownership.deletions) != 1
        or len(ownership.replacement_units) != 1
        or len(resolved.replacement_alternatives) != 1
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
    if not ownership.deletions[0].complete_file_pair and not (
        allow_unmarked_empty_baseline
        and _unmarked_pair_came_from_an_empty_file(
            ownership,
            saved_line_count=len(alternative.saved),
        )
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


def _leading_source_replacement_spans(
    source_lines: Sequence[bytes],
    ownership: BatchOwnership,
) -> tuple[LineSpan[BatchSourceSpace], LineSpan[BatchSourceSpace]] | None:
    """Return a saved/live pair that starts the source.

    The source may contain unrelated text after the pair. Ownership, rather
    than a text search, identifies the pair's far edge.
    """
    resolved = ownership.resolve()
    if (
        len(ownership.deletions) != 1
        or len(ownership.replacement_units) != 1
        or len(resolved.replacement_alternatives) != 1
    ):
        return None
    alternative = resolved.replacement_alternatives[0]
    if (
        alternative.deletion_index != 0
        or alternative.unit_index != 0
        or len(alternative.live_payload) != 1
        or alternative.saved.start.offset != 0
        or alternative.live_envelope.end.offset > len(source_lines)
        or resolved.presence_line_set != alternative.saved_lines
        or alternative.absence_claim.anchor.offset != 0
    ):
        return None
    if not ownership.deletions[0].complete_file_pair and not (
        _unmarked_pair_came_from_an_empty_file(
            ownership,
            saved_line_count=len(alternative.saved),
        )
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


def _reference_names_empty_file(reference: BaselineReference | None) -> bool:
    """Return whether both known sides identify an empty baseline."""
    return (
        reference is not None
        and isinstance(reference.after, KnownBoundary)
        and reference.after.line is None
        and isinstance(reference.before, KnownBoundary)
        and reference.before.line is None
    )


def _unmarked_pair_came_from_an_empty_file(
    ownership: BatchOwnership,
    *,
    saved_line_count: int,
) -> bool:
    """Recognize complete pairs written before their explicit marker existed."""
    if (
        len(ownership.presence_claims) != 1
        or not _reference_names_empty_file(
            ownership.deletions[0].baseline_reference
        )
    ):
        return False
    references = ownership.presence_claims[0].baseline_references
    return len(references) == saved_line_count and all(
        1 <= source_line <= saved_line_count
        and _reference_names_empty_file(reference)
        for source_line, reference in references.items()
    )


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
    unmarked_target_lines: Sequence[bytes] | None = None,
    spool_dir: str | Path | None = None,
) -> CompleteSourceReplacementChanges | None:
    """Return only the lines that differ between the saved and live files.

    Old metadata can omit the complete-file marker. It is accepted only when
    its empty-file boundaries are exact and the current target has the same
    extent and edge lines as the stored live file.
    """
    spans = _complete_source_replacement_spans(
        source_lines,
        ownership,
        allow_unmarked_empty_baseline=unmarked_target_lines is not None,
    )
    if spans is None:
        return None
    saved_span, live_span = spans
    if not ownership.deletions[0].complete_file_pair:
        assert unmarked_target_lines is not None
        live_lines = LineRangeView(
            source_lines,
            live_span.start.offset,
            live_span.end.offset,
        )
        if (
            not live_lines
            or len(live_lines) != len(unmarked_target_lines)
            or not unmarked_target_lines
            or normalize_line_endings(bytes(live_lines[0]))
            != normalize_line_endings(bytes(unmarked_target_lines[0]))
            or normalize_line_endings(bytes(live_lines[-1]))
            != normalize_line_endings(bytes(unmarked_target_lines[-1]))
        ):
            return None
    return _changes_from_source_replacement_spans(
        source_lines,
        ownership,
        saved_span,
        live_span,
        spool_dir=spool_dir,
    )


def changes_from_leading_source_replacement(
    source_lines: Sequence[bytes],
    ownership: BatchOwnership,
    *,
    spool_dir: str | Path | None = None,
) -> CompleteSourceReplacementChanges | None:
    """Return changed lines for a saved/live pair at the start of the source."""
    spans = _leading_source_replacement_spans(source_lines, ownership)
    if spans is None:
        return None
    saved_span, live_span = spans
    return _changes_from_source_replacement_spans(
        source_lines,
        ownership,
        saved_span,
        live_span,
        spool_dir=spool_dir,
    )


def _changes_from_source_replacement_spans(
    source_lines: Sequence[bytes],
    ownership: BatchOwnership,
    saved_span: LineSpan[BatchSourceSpace],
    live_span: LineSpan[BatchSourceSpace],
    *,
    spool_dir: str | Path | None,
) -> CompleteSourceReplacementChanges:
    """Compare one saved version with the live version stored after it."""
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
    first_change: SemanticChangeRun | None = None
    last_change: SemanticChangeRun | None = None
    semantic_runs = stream_semantic_change_runs(
        live_lines,
        saved_lines,
        spool_dir=spool_dir,
    )
    try:
        for run in semantic_runs:
            if first_change is None:
                first_change = run
            last_change = run
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
        first_change=first_change,
        last_change=last_change,
    )


def _unique_sequence_start(
    working_lines: Sequence[bytes],
    sequence: Sequence[bytes],
    *,
    spool_dir: str | Path | None,
) -> int | None:
    """Return the only exact sequence start, or None when absent or repeated."""
    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        matches = iter_exact_sequence_indexes(
            working_lines,
            sequence,
            workspace=workspace,
        )
        try:
            first = next(matches, None)
            if first is None or next(matches, None) is not None:
                return None
            return first
        finally:
            close_matches = getattr(matches, "close", None)
            if close_matches is not None:
                close_matches()


def _mapping_covers_source(mapping: LineMapping) -> bool:
    """Return whether every source line has one target line."""
    return all(
        mapping.get_target_line_from_source_line(source_line) is not None
        for source_line in range(1, len(mapping.source_to_target) + 1)
    )


def unique_live_alternative_span(
    changes: CompleteSourceReplacementChanges,
    working_lines: Sequence[bytes],
    *,
    spool_dir: str | Path | None = None,
) -> LineSpan[WorktreeSpace] | None:
    """Locate one exact nonempty live alternative in the working file."""
    if not changes.live_lines:
        return None
    start = _unique_sequence_start(
        working_lines,
        changes.live_lines,
        spool_dir=spool_dir,
    )
    if start is None:
        return None
    return LineSpan(
        LineBoundary(start),
        LineBoundary(start + len(changes.live_lines)),
    )


def _verified_prefix_anchor(
    changes: CompleteSourceReplacementChanges,
    working_lines: Sequence[bytes],
    *,
    live_line: int | None,
    saved_line: int | None,
    spool_dir: str | Path | None,
) -> tuple[int, int] | None:
    """Map one unchanged boundary after verifying its complete live prefix."""
    if live_line is None or saved_line is None:
        return None
    if not (1 <= live_line <= len(changes.live_lines)):
        return None
    if not (1 <= saved_line <= len(changes.source_lines)):
        return None
    if normalize_line_endings(bytes(changes.live_lines[live_line - 1])) != (
        normalize_line_endings(bytes(changes.source_lines[saved_line - 1]))
    ):
        return None

    live_prefix = LineRangeView(changes.live_lines, 0, live_line)
    with match_lines(
        live_prefix,
        working_lines,
        spool_dir=spool_dir,
    ) as mapping:
        working_line = mapping.get_target_line_from_source_line(live_line)
        if (
            working_line is not None
            and not mapping.may_have_unmapped_equal_lines
            and _mapping_covers_source(mapping)
        ):
            return saved_line, working_line

    working_start = _unique_sequence_start(
        working_lines,
        live_prefix,
        spool_dir=spool_dir,
    )
    if working_start is None:
        return None
    return saved_line, working_start + live_line


def _verified_suffix_anchor(
    changes: CompleteSourceReplacementChanges,
    working_lines: Sequence[bytes],
    *,
    live_line: int,
    saved_line: int,
    spool_dir: str | Path | None,
) -> tuple[int, int] | None:
    """Map one unchanged boundary after verifying its complete live suffix."""
    if not (1 <= live_line <= len(changes.live_lines)):
        return None
    if not (1 <= saved_line <= len(changes.source_lines)):
        return None
    if normalize_line_endings(bytes(changes.live_lines[live_line - 1])) != (
        normalize_line_endings(bytes(changes.source_lines[saved_line - 1]))
    ):
        return None

    live_suffix = LineRangeView(
        changes.live_lines,
        live_line - 1,
        len(changes.live_lines),
    )
    with match_lines(
        live_suffix,
        working_lines,
        spool_dir=spool_dir,
    ) as mapping:
        working_line = mapping.get_target_line_from_source_line(1)
        if (
            working_line is not None
            and not mapping.may_have_unmapped_equal_lines
            and _mapping_covers_source(mapping)
        ):
            return saved_line, working_line

    working_start = _unique_sequence_start(
        working_lines,
        live_suffix,
        spool_dir=spool_dir,
    )
    if working_start is None:
        return None
    return saved_line, working_start + 1


def _source_replacement_replay_anchors(
    changes: CompleteSourceReplacementChanges,
    working_lines: Sequence[bytes],
    *,
    spool_dir: str | Path | None,
) -> tuple[tuple[int, int], ...]:
    """Find up to two unchanged boundaries around the stored change."""
    first_change = changes.first_change
    last_change = changes.last_change
    if first_change is None or last_change is None:
        return ()

    preceding = _verified_prefix_anchor(
        changes,
        working_lines,
        live_line=first_change.source_anchor,
        saved_line=first_change.target_anchor,
        spool_dir=spool_dir,
    )
    if preceding is None:
        first_source_end = (
            first_change.source_end
            if first_change.source_end is not None
            else (first_change.source_anchor or 0)
        )
        first_target_end = (
            first_change.target_end
            if first_change.target_end is not None
            else (first_change.target_anchor or 0)
        )
        preceding = _verified_suffix_anchor(
            changes,
            working_lines,
            live_line=first_source_end + 1,
            saved_line=first_target_end + 1,
            spool_dir=spool_dir,
        )

    source_change_end = (
        last_change.source_end
        if last_change.source_end is not None
        else (last_change.source_anchor or 0)
    )
    target_change_end = (
        last_change.target_end
        if last_change.target_end is not None
        else (last_change.target_anchor or 0)
    )
    following_live_line = source_change_end + 1
    following_saved_line = target_change_end + 1
    following = _verified_suffix_anchor(
        changes,
        working_lines,
        live_line=following_live_line,
        saved_line=following_saved_line,
        spool_dir=spool_dir,
    )
    if following is None:
        following = _verified_prefix_anchor(
            changes,
            working_lines,
            live_line=last_change.source_anchor,
            saved_line=last_change.target_anchor,
            spool_dir=spool_dir,
        )

    if preceding is not None and following is not None:
        if preceding == following:
            return (preceding,)
        if preceding[0] < following[0] and preceding[1] < following[1]:
            return preceding, following
        return ()
    if preceding is not None:
        return (preceding,)
    if following is not None:
        return (following,)
    return ()


@contextmanager
def acquire_source_replacement_replay_mapping(
    changes: CompleteSourceReplacementChanges,
    working_lines: Sequence[bytes],
    *,
    spool_dir: str | Path | None = None,
) -> Iterator[LineMapping | None]:
    """Map a stored saved version through its live predecessor."""
    anchors = _source_replacement_replay_anchors(
        changes,
        working_lines,
        spool_dir=spool_dir,
    )
    if not anchors:
        yield None
        return
    with match_lines(
        changes.source_lines,
        working_lines,
        anchor_pairs=anchors,
        spool_dir=spool_dir,
    ) as mapping:
        yield mapping


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
