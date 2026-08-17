"""Context-sensitive placement for missing presence claims."""

from __future__ import annotations

from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..line_matching.line_mapping import LineMapping
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.occurrence_index import LinePayloadOccurrenceIndex
from .presence_missing_claims import mapped_missing_source_lines
from ...core.line_selection import LineRanges, LineSelection
from ...core.mapped_storage import MappedIntVector
from ...core.resource_cleanup import close_resources_preserving_first
from ...exceptions import MergeError
from ...i18n import _


_CONTEXTUAL_LEADING_GAP = 3
_DIRECT_DISTINCTIVE_CONTEXT_CHECK_LIMIT = 8


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
    exact_context_gap: bool = False


@dataclass(frozen=True)
class ContextualPresenceAmbiguity:
    """A missing claimed run with multiple context-compatible target gaps."""

    run_start: int
    run_end: int
    before_source_line: int | None
    after_source_line: int | None
    start_gap: int
    end_gap: int


class PresencePlacementAmbiguityError(MergeError):
    """Raised when structural context supports multiple presence placements."""


@dataclass(frozen=True)
class _PresenceRunAnalysis:
    run_start: int
    run_end: int
    before: tuple[int, int] | None
    after: tuple[int, int] | None
    gap_index: int | None
    exact_context_gap: bool = False


@dataclass(frozen=True, slots=True)
class _MissingPresenceCluster:
    """Missing selected runs between the same two mapped source lines."""

    run_start_index: int
    run_stop_index: int
    before: tuple[int, int] | None
    after: tuple[int, int] | None
    unclaimed_source_line_count: int

    def has_locally_collapsed_target_gap(self) -> bool:
        """Return whether local source context proves one empty target gap."""
        return (
            self.run_stop_index > self.run_start_index + 1
            and self.before is not None
            and self.after is not None
            and self.after[1] == self.before[1] + 1
            and self.unclaimed_source_line_count < _CONTEXTUAL_LEADING_GAP
        )


class _DistinctiveContextResolver:
    """Find nearby globally distinctive mappings with bounded heap state."""

    def __init__(
        self,
        source_lines: Sequence[bytes],
        target_lines: Sequence[bytes],
        mapping: LineMapping,
        trusted_source_lines: Collection[int],
        *,
        spool_dir: str | Path | None,
    ) -> None:
        self._source_lines = source_lines
        self._target_lines = target_lines
        self._mapping = mapping
        self._trusted_source_lines = trusted_source_lines
        self._spool_dir = spool_dir
        self._direct_check_count = 0
        self._direct_results: dict[int, bool] = {}
        self._workspace: MatcherWorkspace | None = None
        self._source_occurrences: LinePayloadOccurrenceIndex | None = None
        self._target_occurrences: LinePayloadOccurrenceIndex | None = None
        self._nearest_distinctive_before: MappedIntVector | None = None
        self._nearest_distinctive_after: MappedIntVector | None = None

    def close(self) -> None:
        """Release lazily allocated occurrence indexes."""
        if self._workspace is not None:
            self._workspace.close()
            self._workspace = None
            self._source_occurrences = None
            self._target_occurrences = None
            self._nearest_distinctive_before = None
            self._nearest_distinctive_after = None

    def nearest_before(self, run_start: int) -> tuple[int, int] | None:
        """Return the nearest distinctive mapped line before a source run."""
        self._build_distinctive_neighbor_indexes()
        assert self._nearest_distinctive_before is not None
        source_line = self._nearest_distinctive_before[run_start]
        if source_line == 0:
            return None
        target_line = self._mapping.get_target_line_from_source_line(source_line)
        assert target_line is not None
        return source_line, target_line

    def nearest_after(self, run_end: int) -> tuple[int, int] | None:
        """Return the nearest distinctive mapped line after a source run."""
        self._build_distinctive_neighbor_indexes()
        assert self._nearest_distinctive_after is not None
        source_line = self._nearest_distinctive_after[run_end]
        if source_line == 0:
            return None
        target_line = self._mapping.get_target_line_from_source_line(source_line)
        assert target_line is not None
        return source_line, target_line

    def nearest_around(
        self,
        run_start: int,
        run_end: int,
        before: tuple[int, int] | None,
        after: tuple[int, int] | None,
        *,
        trust_before: bool = False,
        trust_after: bool = False,
    ) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        """Return nearest distinctive mappings around a source run."""
        candidates = tuple(pair[0] for pair in (before, after) if pair is not None)
        distinctive = self._distinctive_results(candidates)

        if before is not None and not trust_before and not distinctive[0]:
            before = self.nearest_before(run_start)
        if after is not None and not trust_after and not distinctive[-1]:
            after = self.nearest_after(run_end)
        return before, after

    def _build_distinctive_neighbor_indexes(self) -> None:
        """Cache nearest distinctive mappings for constant-time run queries."""
        if self._nearest_distinctive_before is not None:
            return
        if self._source_occurrences is None:
            self._build_occurrence_indexes()
        assert self._workspace is not None
        source_line_count = len(self._source_lines)
        before = self._workspace.int_vector(
            source_line_count + 1,
            width=8,
            fill=0,
        )
        after = self._workspace.int_vector(
            source_line_count + 1,
            width=8,
            fill=0,
        )

        nearest = 0
        for source_line in range(1, source_line_count + 1):
            before[source_line] = nearest
            if self._mapping.get_target_line_from_source_line(
                source_line
            ) is not None and self._is_distinctive_from_indexes(source_line):
                nearest = source_line

        nearest = 0
        for source_line in range(source_line_count, 0, -1):
            after[source_line] = nearest
            if self._mapping.get_target_line_from_source_line(
                source_line
            ) is not None and self._is_distinctive_from_indexes(source_line):
                nearest = source_line

        self._nearest_distinctive_before = before
        self._nearest_distinctive_after = after

    def _is_distinctive_from_indexes(self, source_line: int) -> bool:
        """Return exact distinctiveness using the shared full-file indexes."""
        if source_line in self._trusted_source_lines:
            return True
        assert self._source_occurrences is not None
        assert self._target_occurrences is not None
        content = self._source_lines[source_line - 1]
        return (
            self._source_occurrences.occurrence_count(content) == 1
            and self._target_occurrences.occurrence_count(content) == 1
        )

    def _distinctive_results(
        self,
        source_lines: Sequence[int],
    ) -> tuple[bool, ...]:
        results: list[bool | None] = []
        unchecked_lines: list[int] = []
        for source_line in source_lines:
            if source_line in self._trusted_source_lines:
                results.append(True)
            elif source_line in self._direct_results:
                results.append(self._direct_results[source_line])
            else:
                results.append(None)
                unchecked_lines.append(source_line)

        if not unchecked_lines:
            return tuple(result is True for result in results)

        if (
            self._source_occurrences is None
            and self._direct_check_count + len(unchecked_lines)
            <= _DIRECT_DISTINCTIVE_CONTEXT_CHECK_LIMIT
        ):
            contents = [
                self._source_lines[source_line - 1] for source_line in unchecked_lines
            ]
            source_counts = _line_occurrence_counts(
                self._source_lines,
                contents,
            )
            target_counts = _line_occurrence_counts(
                self._target_lines,
                contents,
            )
            for source_line, source_count, target_count in zip(
                unchecked_lines,
                source_counts,
                target_counts,
                strict=True,
            ):
                self._direct_results[source_line] = (
                    source_count == 1 and target_count == 1
                )
            self._direct_check_count += len(unchecked_lines)
        else:
            if self._source_occurrences is None:
                self._build_occurrence_indexes()
            assert self._source_occurrences is not None
            assert self._target_occurrences is not None
            for result_index, (source_line, result) in enumerate(
                zip(source_lines, results, strict=True)
            ):
                if result is not None:
                    continue
                content = self._source_lines[source_line - 1]
                results[result_index] = (
                    self._source_occurrences.occurrence_count(content) == 1
                    and self._target_occurrences.occurrence_count(content) == 1
                )

        for result_index, (source_line, result) in enumerate(
            zip(source_lines, results, strict=True)
        ):
            if result is None:
                results[result_index] = self._direct_results[source_line]
        return tuple(result is True for result in results)

    def _build_occurrence_indexes(self) -> None:
        self._workspace = MatcherWorkspace(spool_dir=self._spool_dir)
        try:
            self._source_occurrences = LinePayloadOccurrenceIndex(
                self._workspace,
                self._source_lines,
                normalize_payloads=False,
            )
            self._target_occurrences = LinePayloadOccurrenceIndex(
                self._workspace,
                self._target_lines,
                normalize_payloads=False,
            )
        except BaseException:
            self.close()
            raise


def _line_occurrence_counts(
    lines: Sequence[bytes],
    contents: Sequence[bytes],
) -> tuple[int, ...]:
    """Count a bounded set of exact lines, capping each count at two."""
    counts = [0] * len(contents)
    content_hashes = tuple(hash(content) for content in contents)
    for line_index in range(len(lines)):
        line = lines[line_index]
        line_hash = hash(line)
        for content_index, content in enumerate(contents):
            if (
                counts[content_index] < 2
                and line_hash == content_hashes[content_index]
                and line == content
            ):
                counts[content_index] += 1
    return tuple(counts)


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


def _iter_missing_presence_clusters(
    missing: LineRanges,
    mapping: LineMapping,
) -> Iterator[_MissingPresenceCluster]:
    """Yield missing runs grouped by their nearest mapped source boundaries.

    The compact range input and monotonic cluster scan keep this proportional
    to the number of selected ranges.  No per-line Python collection is built.
    """
    missing_ranges = missing.ranges()
    source_line_count = len(mapping.source_to_target)
    run_index = 0
    while run_index < len(missing_ranges):
        run_start, run_end = missing_ranges[run_index]
        before = _nearest_mapped_before(mapping, run_start)
        after = _nearest_mapped_after(
            mapping,
            run_end,
            source_line_count,
        )
        after_source_line = source_line_count + 1 if after is None else after[0]
        selected_line_count = run_end - run_start + 1
        run_stop_index = run_index + 1
        while (
            run_stop_index < len(missing_ranges)
            and missing_ranges[run_stop_index][0] < after_source_line
        ):
            sibling_start, sibling_end = missing_ranges[run_stop_index]
            selected_line_count += sibling_end - sibling_start + 1
            run_stop_index += 1

        before_source_line = 0 if before is None else before[0]
        source_gap_line_count = after_source_line - before_source_line - 1
        yield _MissingPresenceCluster(
            run_start_index=run_index,
            run_stop_index=run_stop_index,
            before=before,
            after=after,
            unclaimed_source_line_count=(source_gap_line_count - selected_line_count),
        )
        run_index = run_stop_index


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


def _extend_exact_cluster_context(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    source_selection: LineSelection,
    before: tuple[int, int] | None,
    after: tuple[int, int] | None,
) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """Extend mapped cluster edges through exact unselected local context.

    The primary matcher deliberately leaves repeated lines unmapped.  Within
    an already ordered pair of mapped boundaries, however, an exact prefix or
    suffix still proves where an adjacent selected run belongs.  Stop at the
    first selected line or mismatch so this cannot infer an ordering across
    unrelated source-only and target-only content.
    """
    source_before = before[0] if before is not None else 0
    target_before = before[1] if before is not None else 0
    source_after = after[0] if after is not None else len(source_lines) + 1
    target_after = after[1] if after is not None else len(target_lines) + 1

    while (
        source_before + 1 < source_after
        and target_before + 1 < target_after
        and source_before + 1 not in source_selection
        and source_lines[source_before] == target_lines[target_before]
    ):
        source_before += 1
        target_before += 1
        before = (source_before, target_before)

    while (
        source_after - 1 > source_before
        and target_after - 1 > target_before
        and source_after - 1 not in source_selection
        and source_lines[source_after - 2] == target_lines[target_after - 2]
    ):
        source_after -= 1
        target_after -= 1
        after = (source_after, target_after)

    return before, after


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
    *,
    require_distinctive_context: bool = False,
    distinctive_context_lines: LineSelection | None = None,
    recorded_context_lines: LineSelection | None = None,
    collapsing_target_spans: Sequence[tuple[int, ...]] = (),
    spool_dir: str | Path | None = None,
) -> tuple[LineRanges, tuple[_PresenceRunAnalysis, ...]]:
    missing = mapped_missing_source_lines(
        source_selection,
        len(source_lines),
        mapping,
    )
    distinctive_context: _DistinctiveContextResolver | None = None
    analyses: list[_PresenceRunAnalysis] = []
    analysis_completed = False

    try:
        missing_ranges = missing.ranges()
        for cluster in _iter_missing_presence_clusters(missing, mapping):
            locally_collapsed = cluster.has_locally_collapsed_target_gap()
            exact_before, exact_after = _extend_exact_cluster_context(
                source_lines,
                target_lines,
                source_selection,
                cluster.before,
                cluster.after,
            )
            exact_target_gap_collapsed = (
                exact_before[1] if exact_before is not None else 0
            ) == (exact_after[1] - 1 if exact_after is not None else len(target_lines))
            for run_index in range(
                cluster.run_start_index,
                cluster.run_stop_index,
            ):
                run_start, run_end = missing_ranges[run_index]
                gap_index: int | None
                mapped_before = cluster.before
                mapped_after = cluster.after
                leading_gap = (
                    run_start - mapped_before[0] - 1 if mapped_before is not None else 0
                )
                run_requires_distinctive_context = require_distinctive_context or (
                    distinctive_context_lines is not None
                    and distinctive_context_lines.count(
                        run_start,
                        run_end,
                    )
                    > 0
                )
                run_has_recorded_context = (
                    recorded_context_lines is not None
                    and recorded_context_lines.count(run_start, run_end)
                    == run_end - run_start + 1
                )
                exact_context_gap = False

                if locally_collapsed:
                    before = mapped_before
                    after = mapped_after
                    assert before is not None
                    gap_index = before[1]
                elif (
                    run_has_recorded_context
                    and exact_target_gap_collapsed
                    and (
                        (exact_before is not None and exact_before[0] == run_start - 1)
                        or (exact_after is not None and exact_after[0] == run_end + 1)
                    )
                ):
                    before = exact_before
                    after = exact_after
                    gap_index = _choose_insertion_gap(
                        run_start=run_start,
                        run_end=run_end,
                        source_line_count=len(source_lines),
                        target_line_count=len(target_lines),
                        before=before,
                        after=after,
                    )
                    exact_context_gap = gap_index is not None
                elif (
                    not run_requires_distinctive_context
                    and leading_gap < _CONTEXTUAL_LEADING_GAP
                ):
                    before = mapped_before
                    after = mapped_after
                    gap_index = before[1] if before is not None else 0
                else:
                    if distinctive_context is None:
                        distinctive_context = _DistinctiveContextResolver(
                            source_lines,
                            target_lines,
                            mapping,
                            trusted_source_lines,
                            spool_dir=spool_dir,
                        )
                    before, after = distinctive_context.nearest_around(
                        run_start,
                        run_end,
                        mapped_before,
                        mapped_after,
                        trust_before=(
                            mapped_before is not None
                            and mapped_before[0] == run_start - 1
                            and recorded_context_lines is not None
                            and mapped_before[0] in recorded_context_lines
                        ),
                        trust_after=(
                            mapped_after is not None
                            and mapped_after[0] == run_end + 1
                            and recorded_context_lines is not None
                            and mapped_after[0] in recorded_context_lines
                        ),
                    )
                    gap_index = _choose_insertion_gap(
                        run_start=run_start,
                        run_end=run_end,
                        source_line_count=len(source_lines),
                        target_line_count=len(target_lines),
                        before=before,
                        after=after,
                    )

                analyses.append(
                    _PresenceRunAnalysis(
                        run_start=run_start,
                        run_end=run_end,
                        before=before,
                        after=after,
                        gap_index=gap_index,
                        exact_context_gap=exact_context_gap,
                    )
                )
        analysis_completed = True
    finally:
        close_resources_preserving_first(
            (distinctive_context,),
            suppress_errors=not analysis_completed,
        )

    _resolve_collapsing_target_spans(
        analyses,
        collapsing_target_spans,
        len(target_lines),
    )
    return missing, tuple(analyses)


def _resolve_collapsing_target_spans(
    analyses: list[_PresenceRunAnalysis],
    target_spans: Sequence[tuple[int, ...]],
    target_line_count: int,
) -> None:
    """Place ambiguous runs at a verified removal span's start boundary."""
    span_iterator = _iter_collapsing_target_spans(target_spans)
    current_span = next(span_iterator, None)
    for index, analysis in enumerate(analyses):
        if analysis.gap_index is not None:
            continue
        before_gap = analysis.before[1] if analysis.before is not None else 0
        after_gap = (
            analysis.after[1] - 1 if analysis.after is not None else target_line_count
        )
        while current_span is not None and current_span[1] <= before_gap:
            current_span = next(span_iterator, None)
        if current_span is None:
            return
        span_start, span_end = current_span
        if span_start <= before_gap and after_gap <= span_end:
            analyses[index] = _PresenceRunAnalysis(
                run_start=analysis.run_start,
                run_end=analysis.run_end,
                before=analysis.before,
                after=analysis.after,
                gap_index=span_start,
                exact_context_gap=analysis.exact_context_gap,
            )


def _iter_collapsing_target_spans(
    target_spans: Sequence[tuple[int, ...]],
) -> Iterator[tuple[int, int]]:
    """Yield ordered removal spans with overlapping spans coalesced."""
    current_start: int | None = None
    current_end: int | None = None
    for span_start, span_end in target_spans:
        if span_start < 0 or span_end <= span_start:
            raise ValueError("collapsing target spans must be nonempty")
        if current_start is None or current_end is None:
            current_start, current_end = span_start, span_end
            continue
        if span_start < current_start:
            raise ValueError("collapsing target spans must be ordered")
        if span_start < current_end:
            current_end = max(current_end, span_end)
            continue
        yield current_start, current_end
        current_start, current_end = span_start, span_end

    if current_start is not None and current_end is not None:
        yield current_start, current_end


def contextual_presence_ambiguities(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    source_selection: LineSelection,
    mapping: LineMapping,
    *,
    trusted_source_lines: Collection[int] = (),
    require_distinctive_context: bool = False,
    distinctive_context_lines: LineSelection | None = None,
    recorded_context_lines: LineSelection | None = None,
    spool_dir: str | Path | None = None,
) -> tuple[ContextualPresenceAmbiguity, ...]:
    """Return bounded placement ambiguities for suspicious missing runs."""
    _, analyses = _analyze_presence_runs(
        source_lines,
        target_lines,
        source_selection,
        mapping,
        trusted_source_lines,
        require_distinctive_context=require_distinctive_context,
        distinctive_context_lines=distinctive_context_lines,
        recorded_context_lines=recorded_context_lines,
        spool_dir=spool_dir,
    )
    ambiguities: list[ContextualPresenceAmbiguity] = []

    for analysis in analyses:
        if analysis.gap_index is not None:
            continue
        ambiguities.append(
            _contextual_ambiguity(
                run_start=analysis.run_start,
                run_end=analysis.run_end,
                target_line_count=len(target_lines),
                before=analysis.before,
                after=analysis.after,
            )
        )

    return tuple(ambiguities)


def contextual_presence_placements(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    source_selection: LineSelection,
    mapping: LineMapping,
    *,
    trusted_source_lines: Collection[int] = (),
    require_distinctive_context: bool = False,
    distinctive_context_lines: LineSelection | None = None,
    recorded_context_lines: LineSelection | None = None,
    collapsing_target_spans: Sequence[tuple[int, ...]] = (),
    spool_dir: str | Path | None = None,
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
        require_distinctive_context=require_distinctive_context,
        distinctive_context_lines=distinctive_context_lines,
        recorded_context_lines=recorded_context_lines,
        collapsing_target_spans=collapsing_target_spans,
        spool_dir=spool_dir,
    )
    if not missing:
        return missing, ()

    placements: list[PresenceRunPlacement] = []

    for analysis in analyses:
        if analysis.gap_index is None:
            raise PresencePlacementAmbiguityError(
                _("Batch was created from a different version of the file")
            )
        placements.append(
            PresenceRunPlacement(
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
                exact_context_gap=analysis.exact_context_gap,
            )
        )

    placements.sort(key=lambda placement: (placement.gap_index, placement.run_start))
    return missing, tuple(placements)
