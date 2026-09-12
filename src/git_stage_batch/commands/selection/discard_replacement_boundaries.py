"""Preserve explicit replacement ownership boundaries and alternatives."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Iterable, Sequence
from itertools import chain

from ...batch.ownership.absence_claims import AbsenceClaim
from ...batch.line_matching.match_workspace import MatcherWorkspace
from ...batch.line_matching.occurrence_index import (
    LinePayloadOccurrenceIndex,
    normalized_line_payload,
)
from ...batch.line_matching.line_range_view import LineRangeView
from ...batch.line_matching.sequence_equality import line_slice_equals
from ...batch.ownership.references import BaselineReference
from ...batch.ownership.replacement_units import (
    NoReplacementUnitOrigin,
    ReplacementUnit,
)
from ...batch.ownership.replacement_units import normalize_replacement_units
from ...batch.ownership.replacement_origins import ReplacementOriginSourceProjection
from ...batch.ownership.claims import presence_claims_from_source_lines
from ...batch.merge.presence_reference_index import EffectivePresenceReferenceIndex
from ...core.buffer import LineBuffer
from ...core.text_lines import normalize_line_sequence_endings
from ...core.line_selection import LineRanges
from ...core.coordinates import (
    BatchSourceSpace,
    FileSnapshot,
    LineBoundary,
    LineSpan,
    RewrittenWorktreeSpace,
    SnapshotSpan,
)
from ...batch.ownership.model import BatchOwnership
from .discard_replacement_models import (
    DiscardLineReplacementSelection,
)


def _refine_presence_references_from_source_content(
    ownership: BatchOwnership,
    source_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
) -> None:
    normalized_baseline = normalize_line_sequence_endings(baseline_lines)

    baseline_positions: dict[bytes, list[int]] = {}
    for bl_idx, bl_content in enumerate(normalized_baseline):
        baseline_positions.setdefault(bl_content, []).append(bl_idx)

    for claim in ownership.presence_claims:
        for source_line in list(claim.baseline_references):
            if source_line <= 1:
                continue
            ref = claim.baseline_references[source_line]
            current_after = ref.after_line or 0

            preceding_content = normalize_line_sequence_endings(
                [bytes(source_lines[source_line - 2])]
            )[0]
            if not normalized_line_payload(preceding_content):
                continue

            positions = baseline_positions.get(preceding_content)
            if positions is None:
                continue

            insert_point = bisect_left(positions, current_after)
            if insert_point >= len(positions):
                continue
            best_match = positions[insert_point] + 1

            if best_match <= current_after:
                continue

            position = best_match
            new_after = position or None
            new_before = position + 1 if position < len(baseline_lines) else None
            claim.baseline_references[source_line] = BaselineReference(
                after_line=new_after,
                after_content=(
                    bytes(baseline_lines[new_after - 1])
                    if new_after is not None
                    else None
                ),
                has_after_line=True,
                before_line=new_before,
                before_content=(
                    bytes(baseline_lines[new_before - 1])
                    if new_before is not None
                    else None
                ),
                has_before_line=True,
            )


def _refine_and_preserve_explicit_presence_span_boundary(
    ownership: BatchOwnership,
    *,
    selection: DiscardLineReplacementSelection,
    baseline_lines: Sequence[bytes],
    source_content_lines: Sequence[bytes],
    replacement_origin_source_projection: (
        ReplacementOriginSourceProjection[RewrittenWorktreeSpace]
    ),
) -> BatchOwnership:
    """Update references to nearby lines without moving the saved text."""
    source_span = _explicit_presence_source_span(
        ownership,
        selection=selection,
        source_content_lines=source_content_lines,
        replacement_origin_source_projection=(replacement_origin_source_projection),
    )
    initial_reference = (
        _effective_presence_reference_for_line(
            ownership,
            source_span.span.start.offset + 1,
        )
        if source_span is not None
        else None
    )
    _refine_presence_references_from_source_content(
        ownership,
        source_content_lines,
        baseline_lines,
    )
    if source_span is None:
        return ownership
    with MatcherWorkspace() as workspace:
        return _preserve_explicit_presence_span_boundary(
            workspace,
            ownership,
            selection=selection,
            initial_reference=initial_reference,
            source_span=source_span,
            baseline_lines=baseline_lines,
            source_content_lines=source_content_lines,
            replacement_origin_source_projection=(replacement_origin_source_projection),
        )


def _explicit_presence_source_span(
    ownership: BatchOwnership,
    *,
    selection: DiscardLineReplacementSelection,
    source_content_lines: Sequence[bytes],
    replacement_origin_source_projection: (
        ReplacementOriginSourceProjection[RewrittenWorktreeSpace]
    ),
) -> SnapshotSpan[BatchSourceSpace] | None:
    """Find the replacement in the batch source."""
    if ownership.deletions or ownership.replacement_units:
        return None
    explicit_edit_span = SnapshotSpan(
        selection.transformed_projection.rewritten_snapshot,
        selection.transformed_projection.explicit_edit.rewritten_span,
    )
    alternatives = selection.replacement_alternatives
    owned_prefix_span = alternatives.saved.span if alternatives is not None else None
    presence_span = explicit_edit_span.span
    if (
        alternatives is not None
        and alternatives.requires_exact_saved_presence
        and owned_prefix_span is not None
        and len(owned_prefix_span) > 1
    ):
        presence_span = owned_prefix_span
    rewritten_span = SnapshotSpan(
        selection.transformed_projection.rewritten_snapshot,
        presence_span,
    )
    source_span = replacement_origin_source_projection.translate_span(rewritten_span)
    if source_span is None:
        source_span = _infer_explicit_source_span_from_owned_prefix(
            rewritten_span,
            rewritten_lines=selection.rewritten_working_lines,
            source_lines=source_content_lines,
            owned_presence=ownership.presence_line_set(),
            target_snapshot=(replacement_origin_source_projection.target_snapshot),
        )
    if source_span is None or len(source_span.span) == 0:
        return None
    return source_span


def _effective_presence_reference_for_line(
    ownership: BatchOwnership,
    source_line: int,
) -> BaselineReference | None:
    """Return the latest saved location for one source line."""
    for claim in reversed(ownership.presence_claims):
        reference = claim.baseline_references.get(source_line)
        if reference is not None:
            return reference
    return None


def _preserve_explicit_presence_span_boundary(
    workspace: MatcherWorkspace,
    ownership: BatchOwnership,
    *,
    selection: DiscardLineReplacementSelection,
    initial_reference: BaselineReference | None,
    source_span: SnapshotSpan[BatchSourceSpace],
    baseline_lines: Sequence[bytes],
    source_content_lines: Sequence[bytes],
    replacement_origin_source_projection: (
        ReplacementOriginSourceProjection[RewrittenWorktreeSpace]
    ),
) -> BatchOwnership:
    """Give all newly saved lines one shared insertion point."""
    explicit_edit_span = SnapshotSpan(
        selection.transformed_projection.rewritten_snapshot,
        selection.transformed_projection.explicit_edit.rewritten_span,
    )
    alternatives = selection.replacement_alternatives
    boundary_search_span = (
        alternatives.boundary_search_span
        if alternatives is not None
        else explicit_edit_span
    )
    following_boundary_span = replacement_origin_source_projection.translate_span(
        boundary_search_span
    )
    presence_lines = LineRanges.from_ranges(
        (
            (
                source_span.span.start.offset + 1,
                source_span.span.end.offset,
            ),
        )
    )
    owned_presence = ownership.presence_line_set()
    if owned_presence != presence_lines and not (
        _presence_is_explicit_span_prefix_with_blank_suffix(
            owned_presence,
            explicit_presence=presence_lines,
            source_lines=source_content_lines,
        )
    ):
        return ownership

    references = EffectivePresenceReferenceIndex(workspace, ownership)
    source_occurrences = LinePayloadOccurrenceIndex(
        workspace,
        source_content_lines,
    )
    first_source_line = source_span.span.start.offset + 1
    following_boundary = (
        following_boundary_span.span.end.offset
        if following_boundary_span is not None
        else source_span.span.end.offset
    )
    refined_first_reference = references.reference_for(first_source_line)
    shared_reference = (
        (
            initial_reference
            if initial_reference == refined_first_reference
            else (
                _nearest_following_explicit_boundary(
                    (initial_reference,),
                    source_occurrences=source_occurrences,
                    start_boundary=following_boundary,
                )
                or _boundary_before_reference_after(
                    initial_reference,
                    baseline_lines,
                )
            )
        )
        if len(source_span.span) == 1 and initial_reference is not None
        else _nearest_following_explicit_boundary(
            (
                reference
                for _source_line, reference in references.items()
                if reference is not None
            ),
            source_occurrences=source_occurrences,
            start_boundary=following_boundary,
        )
    ) or refined_first_reference
    if shared_reference is not None:
        shared_reference = _reanchor_explicit_block_at_blank_boundary(
            shared_reference,
            source_span=source_span,
            source_lines=source_content_lines,
            baseline_lines=baseline_lines,
        )
    if not ownership.presence_claims:
        return ownership
    canonical_claim = ownership.presence_claims[0]
    ownership.presence_claims[:] = [canonical_claim]
    canonical_claim.source_lines = presence_lines.to_range_strings()
    canonical_claim.baseline_references.clear()
    if shared_reference is not None:
        for source_line in range(
            first_source_line,
            source_span.span.end.offset + 1,
        ):
            canonical_claim.baseline_references[source_line] = shared_reference
    return ownership


def _infer_explicit_source_span_from_owned_prefix(
    rewritten_span: SnapshotSpan[RewrittenWorktreeSpace],
    *,
    rewritten_lines: Sequence[bytes],
    source_lines: Sequence[bytes],
    owned_presence: LineRanges,
    target_snapshot: FileSnapshot[BatchSourceSpace],
) -> SnapshotSpan[BatchSourceSpace] | None:
    """Find the rewritten span from its owned leading lines."""
    owned_ranges = owned_presence.ranges()
    rewritten_count = len(rewritten_span.span)
    if len(owned_ranges) != 1 or rewritten_count == 0:
        return None
    owned_start, owned_end = owned_ranges[0]
    owned_count = owned_end - owned_start + 1
    source_end = owned_start + rewritten_count - 1
    if owned_count > rewritten_count or source_end > len(source_lines):
        return None
    rewritten_view = LineRangeView(
        rewritten_lines,
        rewritten_span.span.start.offset,
        rewritten_span.span.end.offset,
    )
    if not line_slice_equals(source_lines, owned_start - 1, rewritten_view):
        return None
    return SnapshotSpan(
        target_snapshot,
        LineSpan(
            LineBoundary(owned_start - 1),
            LineBoundary(source_end),
        ),
    )


def _presence_is_explicit_span_prefix_with_blank_suffix(
    owned_presence: LineRanges,
    *,
    explicit_presence: LineRanges,
    source_lines: Sequence[bytes],
) -> bool:
    """Return whether the only unowned following lines are blank."""
    owned_ranges = owned_presence.ranges()
    explicit_ranges = explicit_presence.ranges()
    if len(owned_ranges) != 1 or len(explicit_ranges) != 1:
        return False
    owned_start, owned_end = owned_ranges[0]
    explicit_start, explicit_end = explicit_ranges[0]
    if (
        owned_start != explicit_start
        or owned_end >= explicit_end
        or explicit_end > len(source_lines)
    ):
        return False
    return all(
        not normalized_line_payload(source_lines[line_number - 1])
        for line_number in range(owned_end + 1, explicit_end + 1)
    )


def _nearest_following_explicit_boundary(
    references: Iterable[BaselineReference],
    *,
    source_occurrences: LinePayloadOccurrenceIndex,
    start_boundary: int,
) -> BaselineReference | None:
    """Find the nearest saved boundary after the selection."""
    best_position: int | None = None
    best_reference: BaselineReference | None = None
    for reference in references:
        if reference.after_content is None or reference.before_content is None:
            continue
        position = source_occurrences.first_adjacent_boundary_position(
            reference.after_content,
            reference.before_content,
            start_position=max(start_boundary, 1),
        )
        if position is not None and (best_position is None or position < best_position):
            best_position = position
            best_reference = reference
            if position == max(start_boundary, 1):
                break
    return best_reference


def _boundary_before_reference_after(
    reference: BaselineReference,
    baseline_lines: Sequence[bytes],
) -> BaselineReference | None:
    """Move an insertion from after a line to just before it."""
    before_line = reference.after_line
    if before_line is None or before_line > len(baseline_lines):
        return None
    after_line = before_line - 1 or None
    return BaselineReference(
        after_line=after_line,
        after_content=(
            bytes(baseline_lines[after_line - 1]) if after_line is not None else None
        ),
        has_after_line=True,
        before_line=before_line,
        before_content=bytes(baseline_lines[before_line - 1]),
        has_before_line=True,
    )


def _reanchor_explicit_block_at_blank_boundary(
    reference: BaselineReference,
    *,
    source_span: SnapshotSpan[BatchSourceSpace],
    source_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
) -> BaselineReference:
    """Keep a blank-delimited saved block on the structural side it replaced."""
    source_start = source_span.span.start.offset
    source_end = source_span.span.end.offset
    after_line = reference.after_line
    before_line = reference.before_line
    if (
        source_start <= 0
        or source_end >= len(source_lines)
        or normalized_line_payload(source_lines[source_start - 1])
        or normalized_line_payload(source_lines[source_end])
        or after_line is None
        or before_line != after_line + 1
        or after_line > len(baseline_lines)
        or before_line > len(baseline_lines)
        or not normalized_line_payload(baseline_lines[after_line - 1])
        or normalized_line_payload(baseline_lines[before_line - 1])
    ):
        return reference

    if after_line > 1 and not normalized_line_payload(baseline_lines[after_line - 2]):
        return _boundary_before_reference_after(reference, baseline_lines) or reference

    following_line = before_line + 1
    if following_line > len(baseline_lines):
        return reference
    return BaselineReference(
        after_line=before_line,
        after_content=bytes(baseline_lines[before_line - 1]),
        has_after_line=True,
        before_line=following_line,
        before_content=bytes(baseline_lines[following_line - 1]),
        has_before_line=True,
    )


def _exact_owned_prefix_source_range(
    selection: DiscardLineReplacementSelection,
    source_lines: LineBuffer,
    *,
    translate_working_range: Callable[
        [int, int],
        tuple[int, int] | None,
    ],
    following_source_range: tuple[int, int] | None = None,
) -> tuple[int, int] | None:
    """Translate and verify a preserved prefix in the advanced source."""
    prefix_range = _explicit_owned_prefix_range(selection)
    if prefix_range is None:
        return None
    prefix_start, prefix_end = prefix_range
    prefix_lines = LineRangeView(
        selection.rewritten_working_lines,
        prefix_start - 1,
        prefix_end,
    )
    if following_source_range is not None:
        following_start, _following_end = following_source_range
        adjacent_start = following_start - len(prefix_lines)
        adjacent_end = following_start - 1
        if adjacent_start >= 1 and line_slice_equals(
            source_lines,
            adjacent_start - 1,
            prefix_lines,
        ):
            return adjacent_start, adjacent_end
    source_range = translate_working_range(prefix_start, prefix_end)
    if source_range is None:
        return None
    source_start, source_end = source_range
    if not line_slice_equals(source_lines, source_start - 1, prefix_lines):
        return None
    return source_start, source_end


def _exact_alternative_source_range(
    selection: DiscardLineReplacementSelection,
    source_lines: LineBuffer,
    *,
    translate_working_range: Callable[
        [int, int],
        tuple[int, int] | None,
    ],
) -> tuple[int, int] | None:
    """Translate and verify the live alternative in the advanced source."""
    alternative_range = _explicit_alternative_range(selection)
    if alternative_range is None:
        return None
    alternative_start, alternative_end = alternative_range
    source_range = translate_working_range(
        alternative_start,
        alternative_end,
    )
    if source_range is None:
        return None
    source_start, source_end = source_range
    alternative_lines = LineRangeView(
        selection.rewritten_working_lines,
        alternative_start - 1,
        alternative_end,
    )
    if not line_slice_equals(source_lines, source_start - 1, alternative_lines):
        return None
    return source_start, source_end


def _explicit_owned_prefix_range(
    selection: DiscardLineReplacementSelection,
) -> tuple[int, int] | None:
    """Return the preserved prefix's one-based rewritten source range."""
    alternatives = selection.replacement_alternatives
    return alternatives.saved_range if alternatives is not None else None


def _explicit_alternative_range(
    selection: DiscardLineReplacementSelection,
) -> tuple[int, int] | None:
    """Return the rewritten range retained as the live alternative."""
    alternatives = selection.replacement_alternatives
    return alternatives.live_range if alternatives is not None else None


def _explicit_source_alternative_reference(
    selection: DiscardLineReplacementSelection,
) -> BaselineReference:
    """Describe the live alternative after the owned prefix is removed."""
    prefix_range = _explicit_owned_prefix_range(selection)
    alternative_range = _explicit_alternative_range(selection)
    if prefix_range is None or alternative_range is None:
        raise ValueError("explicit source alternative has incomplete coordinates")

    prefix_start, prefix_end = prefix_range
    alternative_start, alternative_end = alternative_range
    if alternative_start != prefix_end + 1:
        raise ValueError("explicit source alternatives are not contiguous")

    prefix_line_count = prefix_end - prefix_start + 1
    target_alternative_start = alternative_start - prefix_line_count
    target_alternative_end = alternative_end - prefix_line_count
    after_line = target_alternative_start - 1 if target_alternative_start > 1 else None
    before_line = (
        target_alternative_end + 1
        if alternative_end < len(selection.rewritten_working_lines)
        else None
    )
    return BaselineReference(
        after_line=after_line,
        after_content=(
            bytes(selection.rewritten_working_lines[prefix_start - 2])
            if after_line is not None
            else None
        ),
        has_after_line=True,
        before_line=before_line,
        before_content=(
            bytes(selection.rewritten_working_lines[alternative_end])
            if before_line is not None
            else None
        ),
        has_before_line=True,
    )


def _add_explicit_source_alternative_replacement(
    ownership: BatchOwnership,
    *,
    selection: DiscardLineReplacementSelection,
    presence_range: tuple[int, int],
    alternative_range: tuple[int, int],
) -> BatchOwnership:
    """Couple an owned explicit prefix to its retained source alternative."""
    presence_start, presence_end = presence_range
    alternative_start, alternative_end = alternative_range
    explicit_alternative_range = _explicit_alternative_range(selection)
    if explicit_alternative_range is None:
        raise ValueError("explicit source alternative has no rewritten range")
    explicit_alternative_start, explicit_alternative_end = explicit_alternative_range
    if (
        alternative_start != presence_end + 1
        or alternative_end - alternative_start
        != explicit_alternative_end - explicit_alternative_start
    ):
        raise ValueError(
            "advanced batch source split the explicit replacement alternatives"
        )

    deletions = list(ownership.deletions)
    deletions.append(
        AbsenceClaim(
            anchor_line=presence_start - 1 if presence_start > 1 else None,
            content_lines=LineRangeView(
                selection.rewritten_working_lines,
                explicit_alternative_start - 1,
                explicit_alternative_end,
            ),
            baseline_reference=_explicit_source_alternative_reference(selection),
            source_alternative=True,
        )
    )
    replacement_units = list(ownership.replacement_units)
    explicit_presence = LineRanges.from_ranges((presence_range,))
    expanded_presence = LineRanges.from_ranges(
        chain(
            ownership.presence_line_set().ranges(),
            explicit_presence.ranges(),
        )
    )
    expanded_presence_claims = presence_claims_from_source_lines(
        expanded_presence,
        ownership.presence_baseline_references(),
    )
    replacement_units.append(
        ReplacementUnit(
            presence_lines=explicit_presence.to_range_strings(),
            deletion_indices=[len(deletions) - 1],
        )
    )
    normalized_replacement_units = normalize_replacement_units(
        replacement_units,
        deletion_count=len(deletions),
    )
    source_alternative_index = len(deletions) - 1
    replacement_units_with_current_provenance = [
        (
            ReplacementUnit(
                presence_lines=unit.presence_lines,
                deletion_indices=unit.deletion_indices,
                origin_evidence=NoReplacementUnitOrigin(),
            )
            if source_alternative_index in unit.deletion_indices
            else unit
        )
        for unit in normalized_replacement_units
    ]
    return BatchOwnership(
        presence_claims=expanded_presence_claims,
        deletions=deletions,
        replacement_units=replacement_units_with_current_provenance,
    )
