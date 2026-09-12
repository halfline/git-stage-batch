"""Expand replacement parents across relocated source context."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ...batch.ownership.replacement_line_runs import (
    ReplacementLineRun,
    stream_replacement_line_runs_from_lines,
)
from ...batch.ownership.absence_claims import AbsenceClaim
from ...batch.line_matching.match import match_lines
from ...batch.line_matching.match_workspace import MatcherWorkspace
from ...batch.line_matching.occurrence_index import LinePayloadOccurrenceIndex
from ...batch.line_matching.line_range_view import LineRangeView
from ...batch.ownership.line_entries import (
    baseline_reference_for_file_line_range,
    replacement_unit_origin_for_line_run,
)
from ...batch.ownership.replacement_units import ReplacementUnit, ReplacementUnitOrigin
from ...batch.ownership.replacement_units import normalize_replacement_units
from ...batch.ownership.replacement_origins import ReplacementOriginSourceProjection
from ...batch.source.projection import SourceCoordinateProjection
from ...core.buffer import LineBuffer
from ...core.line_selection import LineRangeBuilder, LineRanges
from ...core.coordinates import (
    LineBoundary,
    LineSpan,
    RewrittenWorktreeSpace,
    SnapshotSpan,
)
from ...core.models import LineEntry
from ...batch.ownership.model import BatchOwnership
from .discard_replacement_models import (
    DiscardLineReplacementSelection,
)
from .discard_replacement_selection import (
    _line_is_delimiter_only,
)
from .discard_replacement_projection import (
    _advance_ordered_range_membership,
)


@dataclass(frozen=True)
class _ExpandedReplacementParent:
    parent: ReplacementLineRun
    rewritten_deletion_ids: LineRanges
    rewritten_addition_ids: LineRanges


@dataclass(frozen=True)
class _ExpansionCandidate:
    """One selected run that may need its complete semantic old parent."""

    old_start: int
    old_end: int
    new_start: int
    new_end: int
    original_old_count: int
    rewritten_deletion_ids: LineRanges
    rewritten_addition_ids: LineRanges


def _expansion_candidates(
    selection: DiscardLineReplacementSelection,
) -> tuple[_ExpansionCandidate, ...]:
    candidates: list[_ExpansionCandidate] = []
    for selection_run in selection.rewritten_selection_runs:
        original_old_lines = selection_run.original_old_lines
        original_new_lines = selection_run.original_new_lines
        rewritten_deletion_ids = selection_run.rewritten_deletion_ids
        if not (
            original_old_lines
            and original_new_lines
            and not selection_run.restore_deletions
            and (
                rewritten_deletion_ids
                or (
                    selection.replacement_alternatives is not None
                    and selection.replacement_alternatives.parent is not None
                )
            )
        ):
            continue
        candidates.append(
            _ExpansionCandidate(
                old_start=original_old_lines.ranges()[0][0],
                old_end=original_old_lines.ranges()[-1][1],
                new_start=original_new_lines.ranges()[0][0],
                new_end=original_new_lines.ranges()[-1][1],
                original_old_count=original_old_lines.count(),
                rewritten_deletion_ids=rewritten_deletion_ids,
                rewritten_addition_ids=selection_run.rewritten_addition_ids,
            )
        )
    return tuple(candidates)


def _selection_may_need_parent_expansion(
    selection: DiscardLineReplacementSelection,
) -> bool:
    return bool(_expansion_candidates(selection))


def _merged_explicit_replacement_parent(
    selection: DiscardLineReplacementSelection,
    expanded_parents: list[_ExpandedReplacementParent],
) -> _ExpandedReplacementParent | None:
    """Return the exact transformed parent for a batch/live payload split."""
    alternatives = selection.replacement_alternatives
    if alternatives is None or alternatives.parent is None:
        return None
    explicit_parent = alternatives.parent.as_line_run()
    prefix = alternatives.saved.span

    old_start = explicit_parent.old_start
    old_end = explicit_parent.old_end
    new_start = explicit_parent.new_start
    new_end = explicit_parent.new_end
    for expanded in expanded_parents:
        old_start = min(old_start, expanded.parent.old_start)
        old_end = max(old_end, expanded.parent.old_end)
        new_start = min(new_start, expanded.parent.new_start)
        new_end = max(new_end, expanded.parent.new_end)
    parent = ReplacementLineRun(old_start, old_end, new_start, new_end)

    deletion_builder = LineRangeBuilder()
    addition_builder = LineRangeBuilder()
    selected_ranges = selection.rewritten_selected_ids.ranges()
    selected_range_index = 0
    for line in selection.rewritten_line_changes.lines:
        line_id = line.id
        if line_id is None:
            continue
        selected_range_index, line_is_selected = _advance_ordered_range_membership(
            selected_ranges,
            selected_range_index,
            line_id,
        )
        if not line_is_selected:
            continue
        if (
            line.kind == "-"
            and line.old_line_number is not None
            and parent.old_start <= line.old_line_number <= parent.old_end
        ):
            deletion_builder.add_line(line_id)
        elif (
            line.kind == "+"
            and line.new_line_number is not None
            and prefix.start.offset < line.new_line_number <= prefix.end.offset
        ):
            addition_builder.add_line(line_id)

    return _ExpandedReplacementParent(
        parent=parent,
        rewritten_deletion_ids=deletion_builder.finish(),
        rewritten_addition_ids=addition_builder.finish(),
    )


def _expand_parent_through_relocated_prefix_context(
    parent: ReplacementLineRun,
    *,
    baseline_lines: Sequence[bytes],
    original_working_lines: Sequence[bytes],
    rewritten_prefix_lines: Sequence[bytes],
) -> ReplacementLineRun:
    """Include trailing baseline delimiters relocated past an owned prefix.

    A transformed replacement can own a closing delimiter while structural
    matching associates the corresponding baseline delimiter with a later,
    adjacent block.  Trusting that association leaves the baseline delimiter
    behind in the batch in addition to the owned copy.  Walk only the local
    delimiter tail and expand through an exact prefix duplicate; a contentful
    line ends the inference.
    """
    if parent.old_end >= len(baseline_lines) or not rewritten_prefix_lines:
        return parent

    expanded_old_end = parent.old_end
    expanded_new_end = parent.new_end
    with (
        MatcherWorkspace() as workspace,
        match_lines(baseline_lines, original_working_lines) as alignment,
    ):
        prefix_occurrences = LinePayloadOccurrenceIndex(
            workspace,
            rewritten_prefix_lines,
            normalize_payloads=False,
        )
        for source_line in range(parent.old_end + 1, len(baseline_lines) + 1):
            content = baseline_lines[source_line - 1]
            if not _line_is_delimiter_only(content):
                break
            target_line = alignment.get_target_line_from_source_line(source_line)
            prefix_count = prefix_occurrences.occurrence_count(content)
            if (
                content.strip()
                and prefix_count == 1
                and target_line is not None
                and target_line > parent.new_end
            ):
                expanded_old_end = source_line
                expanded_new_end = max(expanded_new_end, target_line)

    if expanded_old_end == parent.old_end:
        return parent
    return ReplacementLineRun(
        old_start=parent.old_start,
        old_end=expanded_old_end,
        new_start=parent.new_start,
        new_end=expanded_new_end,
    )


def _expand_explicit_parent_relocated_context(
    selection: DiscardLineReplacementSelection,
    expanded_parent: _ExpandedReplacementParent,
    *,
    baseline_lines: LineBuffer,
    original_working_lines: LineBuffer,
) -> _ExpandedReplacementParent:
    """Expand an explicit parent when its owned prefix shadows later context."""
    alternatives = selection.replacement_alternatives
    if alternatives is None:
        return expanded_parent
    prefix_start, prefix_end = alternatives.saved_range
    prefix_lines = LineRangeView(
        selection.rewritten_working_lines,
        prefix_start - 1,
        prefix_end,
    )
    parent = _expand_parent_through_relocated_prefix_context(
        expanded_parent.parent,
        baseline_lines=baseline_lines,
        original_working_lines=original_working_lines,
        rewritten_prefix_lines=prefix_lines,
    )
    if parent == expanded_parent.parent:
        return expanded_parent
    return _ExpandedReplacementParent(
        parent=parent,
        rewritten_deletion_ids=expanded_parent.rewritten_deletion_ids,
        rewritten_addition_ids=expanded_parent.rewritten_addition_ids,
    )


def _expanded_selected_replacement_parents(
    selection: DiscardLineReplacementSelection,
    *,
    baseline_lines: LineBuffer,
    original_working_lines: LineBuffer | None,
) -> tuple[_ExpandedReplacementParent, ...]:
    """Return selected parents whose old side became rewritten context."""
    candidates = _expansion_candidates(selection)
    if not candidates:
        explicit_parent = _merged_explicit_replacement_parent(selection, [])
        return (explicit_parent,) if explicit_parent is not None else ()
    if original_working_lines is None:
        return ()

    parents: list[_ExpandedReplacementParent] = []
    candidate_index = 0
    hunk_index = 0
    semantic_parents = stream_replacement_line_runs_from_lines(
        old_file_lines=baseline_lines,
        new_file_lines=original_working_lines,
    )
    try:
        for parent in semantic_parents:
            while candidate_index < len(candidates):
                candidate = candidates[candidate_index]
                if (
                    candidate.old_end < parent.old_start
                    or candidate.new_end < parent.new_start
                ):
                    candidate_index += 1
                    continue
                break
            if candidate_index >= len(candidates):
                break

            matched: list[_ExpansionCandidate] = []
            scan_index = candidate_index
            while scan_index < len(candidates):
                candidate = candidates[scan_index]
                if (
                    candidate.old_start > parent.old_end
                    or candidate.new_start > parent.new_end
                ):
                    break
                if (
                    candidate.old_start >= parent.old_start
                    and candidate.old_end <= parent.old_end
                    and candidate.new_start >= parent.new_start
                    and candidate.new_end <= parent.new_end
                ):
                    matched.append(candidate)
                    scan_index += 1
                    continue
                break
            if not matched:
                continue
            candidate_index = scan_index

            visible_parent_old_line_count = 0
            while hunk_index < len(selection.line_changes.lines):
                line = selection.line_changes.lines[hunk_index]
                old_line_number = line.old_line_number
                if old_line_number is None:
                    hunk_index += 1
                    continue
                if old_line_number < parent.old_start:
                    hunk_index += 1
                    continue
                if old_line_number > parent.old_end:
                    break
                if line.kind == "-":
                    visible_parent_old_line_count += 1
                hunk_index += 1

            original_old_count = sum(
                candidate.original_old_count for candidate in matched
            )
            rewritten_deletion_ids = LineRanges.from_ranges(
                range_pair
                for candidate in matched
                for range_pair in candidate.rewritten_deletion_ids.ranges()
            )
            if (
                visible_parent_old_line_count == original_old_count
                and len(rewritten_deletion_ids) < parent.old_end - parent.old_start + 1
            ):
                parents.append(
                    _ExpandedReplacementParent(
                        parent=parent,
                        rewritten_deletion_ids=rewritten_deletion_ids,
                        rewritten_addition_ids=LineRanges.from_ranges(
                            range_pair
                            for candidate in matched
                            for range_pair in candidate.rewritten_addition_ids.ranges()
                        ),
                    )
                )
    finally:
        close = getattr(semantic_parents, "close", None)
        if close is not None:
            close()
    explicit_parent = _merged_explicit_replacement_parent(selection, parents)
    if explicit_parent is not None:
        explicit_parent = _expand_explicit_parent_relocated_context(
            selection,
            explicit_parent,
            baseline_lines=baseline_lines,
            original_working_lines=original_working_lines,
        )
        return (explicit_parent,)
    return tuple(parents)


def _add_expanded_replacement_parents(
    ownership: BatchOwnership,
    *,
    selection: DiscardLineReplacementSelection,
    expanded_parents: tuple[_ExpandedReplacementParent, ...],
    baseline_lines: LineBuffer,
    source_projection: SourceCoordinateProjection,
    replacement_origin_source_projection: (
        ReplacementOriginSourceProjection[RewrittenWorktreeSpace]
    ),
) -> BatchOwnership:
    """Add full semantic-parent absence claims without dropping other units."""
    deletions = list(ownership.deletions)
    replacement_units = list(ownership.replacement_units)
    hunk_index = 0
    anchor_hunk_index = 0
    hunk_lines = selection.rewritten_line_changes.lines

    def source_line_for(line: LineEntry) -> int | None:
        return source_projection.source_line_for(line)

    def source_bound_origin_for(
        parent: ReplacementLineRun,
    ) -> ReplacementUnitOrigin | None:
        origin = replacement_unit_origin_for_line_run(
            parent,
            old_file_lines=baseline_lines,
        )
        original_span = SnapshotSpan(
            selection.transformed_projection.explicit_edit.source_snapshot,
            LineSpan(
                LineBoundary(parent.new_start - 1),
                LineBoundary(parent.new_end),
            ),
        )
        rewritten_span = selection.transformed_projection.explicit_edit.translate_span(
            original_span
        )
        if rewritten_span is None:
            return None
        source_span = replacement_origin_source_projection.translate_span(
            rewritten_span
        )
        return (
            origin.with_batch_source_span(source_span)
            if source_span is not None
            else None
        )

    for expanded_parent in expanded_parents:
        deletion_first = expanded_parent.rewritten_deletion_ids.first()
        addition_first = expanded_parent.rewritten_addition_ids.first()
        if deletion_first is None and addition_first is None:
            first_id = None
            last_id = None
        else:
            deletion_last = (
                expanded_parent.rewritten_deletion_ids.ranges()[-1][1]
                if deletion_first is not None
                else None
            )
            addition_last = (
                expanded_parent.rewritten_addition_ids.ranges()[-1][1]
                if addition_first is not None
                else None
            )
            first_id = min(
                line_id
                for line_id in (deletion_first, addition_first)
                if line_id is not None
            )
            last_id = max(
                line_id
                for line_id in (deletion_last, addition_last)
                if line_id is not None
            )

        deletion_anchor: int | None = None
        found_deletion = False
        presence_builder = LineRangeBuilder()
        deletion_ranges = expanded_parent.rewritten_deletion_ids.ranges()
        addition_ranges = expanded_parent.rewritten_addition_ids.ranges()
        deletion_range_index = 0
        addition_range_index = 0
        while first_id is not None and hunk_index < len(hunk_lines):
            line = hunk_lines[hunk_index]
            line_id = line.id
            if line_id is None or line_id < first_id:
                hunk_index += 1
                continue
            if last_id is not None and line_id > last_id:
                break
            deletion_range_index, line_is_deletion = _advance_ordered_range_membership(
                deletion_ranges,
                deletion_range_index,
                line_id,
            )
            addition_range_index, line_is_addition = _advance_ordered_range_membership(
                addition_ranges,
                addition_range_index,
                line_id,
            )
            if line_is_deletion:
                if not found_deletion:
                    deletion_anchor = source_line_for(line)
                    found_deletion = True
            source_line = source_line_for(line)
            if line_is_addition and source_line is not None:
                presence_builder.add_line(source_line)
            hunk_index += 1
        parent = expanded_parent.parent
        if not found_deletion:
            while anchor_hunk_index < len(hunk_lines):
                line = hunk_lines[anchor_hunk_index]
                old_line_number = line.old_line_number
                if old_line_number is None or old_line_number < parent.old_start:
                    anchor_hunk_index += 1
                    continue
                if old_line_number > parent.old_end:
                    break
                source_line = source_line_for(line)
                if source_line is not None:
                    deletion_anchor = source_line
                    found_deletion = True
                    break
                anchor_hunk_index += 1
        if not found_deletion and addition_first is not None:
            first_presence = presence_builder.finish().first()
            if first_presence is not None:
                deletion_anchor = max(first_presence - 1, 0)
                found_deletion = True
        if not found_deletion:
            continue
        presence_lines = presence_builder.finish()
        deletions.append(
            AbsenceClaim(
                anchor_line=deletion_anchor,
                content_lines=LineRangeView(
                    baseline_lines,
                    parent.old_start - 1,
                    parent.old_end,
                ),
                baseline_reference=baseline_reference_for_file_line_range(
                    parent.old_start,
                    parent.old_end,
                    baseline_lines,
                ),
            )
        )
        if presence_lines:
            replacement_units.append(
                ReplacementUnit(
                    presence_lines=presence_lines.to_range_strings(),
                    deletion_indices=[len(deletions) - 1],
                    origin=source_bound_origin_for(parent),
                )
            )
    return BatchOwnership(
        presence_claims=ownership.presence_claims,
        deletions=deletions,
        replacement_units=normalize_replacement_units(
            replacement_units,
            deletion_count=len(deletions),
        ),
    )
