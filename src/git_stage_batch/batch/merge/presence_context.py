"""Context-sensitive placement for missing presence claims."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from ..line_matching.line_mapping import LineMapping
from .presence_missing_claims import mapped_missing_source_lines
from ...core.line_selection import LineRanges, LineSelection
from ...exceptions import MergeError
from ...i18n import _


_CONTEXTUAL_LEADING_GAP = 3


@dataclass(frozen=True)
class PresenceRunPlacement:
    """One missing claimed run with a context-supported target gap."""

    run_start: int
    run_end: int
    gap_index: int
    before_source_line: int | None
    after_source_line: int | None
    before_target_line: int | None
    after_target_line: int | None


@dataclass(frozen=True)
class ContextualPresenceAmbiguity:
    """A missing claimed run with multiple context-compatible target gaps."""

    run_start: int
    run_end: int
    before_source_line: int | None
    after_source_line: int | None
    start_gap: int
    end_gap: int


@dataclass(frozen=True)
class _PresenceRunAnalysis:
    run_start: int
    run_end: int
    before: tuple[int, int] | None
    after: tuple[int, int] | None
    gap_index: int | None


def _distinctive_mapped_source_lines(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    mapping: LineMapping,
    trusted_source_lines: Collection[int],
) -> list[tuple[int, int]]:
    """Return mapped pairs whose line identity is safe as a boundary.

    A raw alignment can map repeated punctuation through an equal prefix or
    suffix.  Such a mapping is useful for preserving content, but it does not
    identify which structural occurrence owns an insertion boundary.  A line
    is therefore a contextual boundary only when it is unique in both file
    versions, or when another constraint explicitly anchors that source line.
    """
    source_counts = Counter(source_lines)
    target_counts = Counter(target_lines)
    trusted = set(trusted_source_lines)
    pairs: list[tuple[int, int]] = []

    for source_line, target_line in mapping.mapped_line_pairs():
        content = source_lines[source_line - 1]
        if (
            source_line in trusted
            or (
                source_counts[content] == 1
                and target_counts[content] == 1
            )
        ):
            pairs.append((source_line, target_line))

    return pairs


def _nearest_context_before(
    pairs: Sequence[tuple[int, int]],
    run_start: int,
) -> tuple[int, int] | None:
    nearest = None
    for source_line, target_line in pairs:
        if source_line >= run_start:
            break
        nearest = (source_line, target_line)
    return nearest


def _nearest_context_after(
    pairs: Sequence[tuple[int, int]],
    run_end: int,
) -> tuple[int, int] | None:
    for source_line, target_line in pairs:
        if source_line > run_end:
            return source_line, target_line
    return None


def _nearest_mapped_before(
    mapping: LineMapping,
    run_start: int,
) -> tuple[int, int] | None:
    for source_line in range(run_start - 1, 0, -1):
        target_line = mapping.get_target_line_from_source_line(source_line)
        if target_line is not None:
            return source_line, target_line
    return None


def _nearest_mapped_after(
    mapping: LineMapping,
    run_end: int,
    source_line_count: int,
) -> tuple[int, int] | None:
    for source_line in range(run_end + 1, source_line_count + 1):
        target_line = mapping.get_target_line_from_source_line(source_line)
        if target_line is not None:
            return source_line, target_line
    return None


def _choose_insertion_gap(
    *,
    run_start: int,
    run_end: int,
    source_line_count: int,
    target_line_count: int,
    before: tuple[int, int] | None,
    after: tuple[int, int] | None,
) -> int | None:
    """Choose the only target gap supported by distinctive context.

    Target-only lines and source-only lines may occupy the same interval
    between anchors.  In that situation their relative order is knowable only
    when the claimed run is directly adjacent to one boundary.  File edges are
    boundaries too, which keeps edge insertions deterministic.
    """
    before_source_line = before[0] if before is not None else 0
    before_gap = before[1] if before is not None else 0
    after_source_line = after[0] if after is not None else source_line_count + 1
    after_gap = after[1] - 1 if after is not None else target_line_count

    if before_gap > after_gap:
        raise MergeError(_("Batch was created from a different version of the file"))

    if before_gap == after_gap:
        return before_gap

    adjacent_to_before = run_start == before_source_line + 1
    adjacent_to_after = run_end + 1 == after_source_line

    # A file edge fixes ordering even when the opposite contextual anchor is
    # also adjacent.  This preserves deterministic prepend and append merges.
    if run_start == 1:
        return 0
    if run_end == source_line_count:
        return target_line_count

    if adjacent_to_before and not adjacent_to_after:
        return before_gap
    if adjacent_to_after and not adjacent_to_before:
        return after_gap

    # Both-adjacent real anchors with target-only content between them admit
    # two valid orders.  Neither-adjacent runs have no context tying them to a
    # side.  Automatic placement must not choose either shape silently, but a
    # reviewed merge can enumerate the bounded gaps.
    return None


def _contextual_ambiguity(
    *,
    run_start: int,
    run_end: int,
    target_line_count: int,
    before: tuple[int, int] | None,
    after: tuple[int, int] | None,
) -> ContextualPresenceAmbiguity:
    start_gap = before[1] if before is not None else 0
    end_gap = after[1] - 1 if after is not None else target_line_count
    return ContextualPresenceAmbiguity(
        run_start=run_start,
        run_end=run_end,
        before_source_line=before[0] if before is not None else None,
        after_source_line=after[0] if after is not None else None,
        start_gap=start_gap,
        end_gap=end_gap,
    )


def _analyze_presence_runs(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    source_selection: LineSelection,
    mapping: LineMapping,
    trusted_source_lines: Collection[int],
) -> tuple[LineRanges, tuple[_PresenceRunAnalysis, ...]]:
    missing = mapped_missing_source_lines(
        source_selection,
        len(source_lines),
        mapping,
    )
    distinctive_pairs = None
    analyses: list[_PresenceRunAnalysis] = []

    for run_start, run_end in missing.ranges():
        mapped_before = _nearest_mapped_before(mapping, run_start)
        mapped_after = _nearest_mapped_after(mapping, run_end, len(source_lines))
        leading_gap = (
            run_start - mapped_before[0] - 1
            if mapped_before is not None
            else 0
        )

        if leading_gap < _CONTEXTUAL_LEADING_GAP:
            before = mapped_before
            after = mapped_after
            gap_index = before[1] if before is not None else 0
        else:
            if distinctive_pairs is None:
                distinctive_pairs = _distinctive_mapped_source_lines(
                    source_lines,
                    target_lines,
                    mapping,
                    trusted_source_lines,
                )
            before = _nearest_context_before(distinctive_pairs, run_start)
            after = _nearest_context_after(distinctive_pairs, run_end)
            gap_index = _choose_insertion_gap(
                run_start=run_start,
                run_end=run_end,
                source_line_count=len(source_lines),
                target_line_count=len(target_lines),
                before=before,
                after=after,
            )

        analyses.append(_PresenceRunAnalysis(
            run_start=run_start,
            run_end=run_end,
            before=before,
            after=after,
            gap_index=gap_index,
        ))

    return missing, tuple(analyses)


def contextual_presence_ambiguities(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    source_selection: LineSelection,
    mapping: LineMapping,
    *,
    trusted_source_lines: Collection[int] = (),
) -> tuple[ContextualPresenceAmbiguity, ...]:
    """Return bounded placement ambiguities for suspicious missing runs."""
    _, analyses = _analyze_presence_runs(
        source_lines,
        target_lines,
        source_selection,
        mapping,
        trusted_source_lines,
    )
    ambiguities: list[ContextualPresenceAmbiguity] = []

    for analysis in analyses:
        if analysis.gap_index is not None:
            continue
        ambiguities.append(_contextual_ambiguity(
            run_start=analysis.run_start,
            run_end=analysis.run_end,
            target_line_count=len(target_lines),
            before=analysis.before,
            after=analysis.after,
        ))

    return tuple(ambiguities)


def contextual_presence_placements(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    source_selection: LineSelection,
    mapping: LineMapping,
    *,
    trusted_source_lines: Collection[int] = (),
) -> tuple[LineRanges, tuple[PresenceRunPlacement, ...]]:
    """Return missing claims and their context-supported insertion gaps.

    Ordinary missing runs retain their established placement immediately after
    the nearest preceding mapping.  When a substantial source-only region
    separates that mapping from the claim, globally distinctive mappings must
    instead identify which side of target-only content owns the insertion.
    This prevents a repeated brace or blank line from deciding how competing
    unmatched source and target regions should be interleaved.
    """
    missing, analyses = _analyze_presence_runs(
        source_lines,
        target_lines,
        source_selection,
        mapping,
        trusted_source_lines,
    )
    if not missing:
        return missing, ()

    placements: list[PresenceRunPlacement] = []

    for analysis in analyses:
        if analysis.gap_index is None:
            raise MergeError(
                _("Batch was created from a different version of the file")
            )
        placements.append(PresenceRunPlacement(
            run_start=analysis.run_start,
            run_end=analysis.run_end,
            gap_index=analysis.gap_index,
            before_source_line=(
                analysis.before[0] if analysis.before is not None else None
            ),
            after_source_line=(
                analysis.after[0] if analysis.after is not None else None
            ),
            before_target_line=(
                analysis.before[1] if analysis.before is not None else None
            ),
            after_target_line=(
                analysis.after[1] if analysis.after is not None else None
            ),
        ))

    placements.sort(key=lambda placement: (placement.gap_index, placement.run_start))
    return missing, tuple(placements)
