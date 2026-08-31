"""Find changed sections between two versions of a file."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from itertools import chain
from pathlib import Path

from .line_mapping import IntVector, LineMapping
from .match import match_lines
from .match_workspace import MatcherWorkspace
from .occurrence_index import LinePayloadOccurrenceIndex
from ...core.models import LineLevelChange


_SMALL_UNMAPPED_LINE_COUNT = 64


class SemanticChangeKind(Enum):
    """How one changed section differs between the two files."""

    PRESENCE = auto()
    """Lines added to the target."""

    DELETION = auto()
    """Lines removed from the source."""

    REPLACEMENT = auto()
    """Old source lines replaced by new target lines."""


@dataclass(frozen=True, slots=True)
class SemanticChangeRun:
    """One changed section found while comparing two files.

    ``source_anchor`` is the source line immediately before the change.
    ``target_anchor`` is the target line immediately before it. Both are
    ``None`` when the change starts the file.

    All line numbers are 1-indexed.
    """

    kind: SemanticChangeKind
    source_start: int | None = None
    source_end: int | None = None
    target_start: int | None = None
    target_end: int | None = None
    source_anchor: int | None = None
    target_anchor: int | None = None

    def __post_init__(self) -> None:
        _validate_range_pair(self.source_start, self.source_end, "source")
        _validate_range_pair(self.target_start, self.target_end, "target")

    def has_source_line(self, line_number: int | None) -> bool:
        if (
            line_number is None
            or self.source_start is None
            or self.source_end is None
        ):
            return False
        return self.source_start <= line_number <= self.source_end

    def has_target_line(self, line_number: int | None) -> bool:
        if (
            line_number is None
            or self.target_start is None
            or self.target_end is None
        ):
            return False
        return self.target_start <= line_number <= self.target_end


def _validate_range_pair(
    start: int | None,
    end: int | None,
    name: str,
) -> None:
    if (start is None) != (end is None):
        raise ValueError(f"{name} range requires both start and end")
    if start is not None and end is not None and start > end:
        raise ValueError(f"{name} range start must be <= end")


def _trusted_matched_pairs(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    *,
    spool_dir: str | Path | None = None,
) -> Iterator[tuple[int, int]]:
    """Yield line pairs accepted by matching in both directions."""
    with match_lines(
        source_lines=source_lines,
        target_lines=target_lines,
        spool_dir=spool_dir,
    ) as alignment:
        if (
            not alignment.may_have_unmapped_equal_lines
            or not _unmapped_lines_share_content(
                alignment,
                source_lines=source_lines,
                target_lines=target_lines,
                spool_dir=spool_dir,
            )
        ):
            yield from alignment.mapped_line_pairs()
            return

        with match_lines(
            source_lines=target_lines,
            target_lines=source_lines,
            spool_dir=spool_dir,
        ) as reverse_alignment:
            for source_line, target_line in alignment.mapped_line_pairs():
                reverse_source_line = (
                    reverse_alignment.get_target_line_from_source_line(
                        target_line
                    )
                )
                if reverse_source_line == source_line:
                    yield source_line, target_line


def _unmapped_lines_share_content(
    alignment: LineMapping,
    *,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    spool_dir: str | Path | None,
) -> bool:
    """Return whether an unmatched line occurs in both files.

    If none do, matching in reverse cannot reject a forward pair.
    """
    source_unmapped_count, bounded_source_indexes = (
        _bounded_unmapped_indexes(alignment.source_to_target)
    )
    target_unmapped_count, bounded_target_indexes = (
        _bounded_unmapped_indexes(alignment.target_to_source)
    )
    smaller_count = min(source_unmapped_count, target_unmapped_count)
    if smaller_count == 0:
        return False

    if smaller_count <= _SMALL_UNMAPPED_LINE_COUNT:
        return _small_unmapped_lines_share_content(
            alignment,
            source_lines=source_lines,
            target_lines=target_lines,
            source_unmapped_count=source_unmapped_count,
            target_unmapped_count=target_unmapped_count,
            bounded_source_indexes=bounded_source_indexes,
            bounded_target_indexes=bounded_target_indexes,
        )

    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        if source_unmapped_count <= target_unmapped_count:
            occurrence_index = LinePayloadOccurrenceIndex(
                workspace,
                source_lines,
                normalize_payloads=False,
                target_indexes=_unmapped_indexes(alignment.source_to_target),
            )
            return any(
                occurrence_index.occurrence_count(target_lines[target_index])
                for target_index in _unmapped_indexes(
                    alignment.target_to_source
                )
            )

        occurrence_index = LinePayloadOccurrenceIndex(
            workspace,
            target_lines,
            normalize_payloads=False,
            target_indexes=_unmapped_indexes(alignment.target_to_source),
        )
        return any(
            occurrence_index.occurrence_count(source_lines[source_index])
            for source_index in _unmapped_indexes(alignment.source_to_target)
        )


def _small_unmapped_lines_share_content(
    alignment: LineMapping,
    *,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    source_unmapped_count: int,
    target_unmapped_count: int,
    bounded_source_indexes: Sequence[int],
    bounded_target_indexes: Sequence[int],
) -> bool:
    """Check a bounded number of unmapped lines without an index."""
    if source_unmapped_count <= target_unmapped_count:
        smaller_lines = [
            source_lines[source_index]
            for source_index in bounded_source_indexes
        ]
        target_indexes: Iterable[int] = bounded_target_indexes
        if target_unmapped_count > _SMALL_UNMAPPED_LINE_COUNT:
            target_indexes = _unmapped_indexes(alignment.target_to_source)
        return any(
            any(
                target_lines[target_index] == source_line
                for source_line in smaller_lines
            )
            for target_index in target_indexes
        )

    smaller_lines = [
        target_lines[target_index]
        for target_index in bounded_target_indexes
    ]
    source_indexes: Iterable[int] = bounded_source_indexes
    if source_unmapped_count > _SMALL_UNMAPPED_LINE_COUNT:
        source_indexes = _unmapped_indexes(alignment.source_to_target)
    return any(
        any(
            source_lines[source_index] == target_line
            for target_line in smaller_lines
        )
        for source_index in source_indexes
    )


def _bounded_unmapped_indexes(mapping: IntVector) -> tuple[int, list[int]]:
    """Count zero entries and retain at most a fixed number of indexes."""
    count = 0
    indexes: list[int] = []
    for index in range(len(mapping)):
        if mapping[index] != 0:
            continue
        count += 1
        if len(indexes) < _SMALL_UNMAPPED_LINE_COUNT:
            indexes.append(index)
    return count, indexes


def _unmapped_indexes(mapping: IntVector) -> Iterator[int]:
    """Yield zero-based indexes absent from a line-mapping vector."""
    for index in range(len(mapping)):
        if mapping[index] == 0:
            yield index


def derive_semantic_change_runs(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    *,
    spool_dir: str | Path | None = None,
) -> list[SemanticChangeRun]:
    """Return the changed sections between source and target.

    Matching lines divide the files into gaps. A gap on both sides is a
    replacement; a one-sided gap is an addition or deletion.
    """
    return list(
        stream_semantic_change_runs(
            source_lines,
            target_lines,
            spool_dir=spool_dir,
        )
    )


def stream_semantic_change_runs(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    *,
    spool_dir: str | Path | None = None,
) -> Iterator[SemanticChangeRun]:
    """Yield one changed section at a time."""
    previous_source = 0
    previous_target = 0

    matched_pairs = _trusted_matched_pairs(
        source_lines,
        target_lines,
        spool_dir=spool_dir,
    )
    sentinel_pair = ((len(source_lines) + 1, len(target_lines) + 1),)

    try:
        for source_line, target_line in chain(matched_pairs, sentinel_pair):
            source_gap_start = previous_source + 1
            source_gap_end = source_line - 1
            target_gap_start = previous_target + 1
            target_gap_end = target_line - 1
            has_source_gap = source_gap_start <= source_gap_end
            has_target_gap = target_gap_start <= target_gap_end
            source_anchor = previous_source if previous_source != 0 else None
            target_anchor = previous_target if previous_target != 0 else None

            if has_source_gap and has_target_gap:
                yield SemanticChangeRun(
                    kind=SemanticChangeKind.REPLACEMENT,
                    source_start=source_gap_start,
                    source_end=source_gap_end,
                    target_start=target_gap_start,
                    target_end=target_gap_end,
                    source_anchor=source_anchor,
                    target_anchor=target_anchor,
                )
            elif has_source_gap:
                yield SemanticChangeRun(
                    kind=SemanticChangeKind.DELETION,
                    source_start=source_gap_start,
                    source_end=source_gap_end,
                    source_anchor=source_anchor,
                    target_anchor=target_anchor,
                )
            elif has_target_gap:
                yield SemanticChangeRun(
                    kind=SemanticChangeKind.PRESENCE,
                    target_start=target_gap_start,
                    target_end=target_gap_end,
                    source_anchor=source_anchor,
                    target_anchor=target_anchor,
                )

            previous_source = source_line
            previous_target = target_line
    finally:
        close = getattr(matched_pairs, "close", None)
        if close is not None:
            close()


def derive_display_id_run_sets_from_lines(
    line_changes: LineLevelChange,
    *,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
) -> list[set[int]]:
    """Map each changed section to its selectable display IDs."""
    semantic_runs = derive_semantic_change_runs(
        source_lines,
        target_lines,
    )
    return _display_id_run_sets_from_semantic_runs(line_changes, semantic_runs)


def _display_id_run_sets_from_semantic_runs(
    line_changes: LineLevelChange,
    semantic_runs: Iterable[SemanticChangeRun],
) -> list[set[int]]:
    run_sets: list[set[int]] = []
    for run in semantic_runs:
        display_ids = {
            line.id
            for line in line_changes.lines
            if line.id is not None and (
                (
                    run.kind == SemanticChangeKind.REPLACEMENT
                    and (
                        (
                            line.kind == "-"
                            and run.has_source_line(line.old_line_number)
                        )
                        or (
                            line.kind == "+"
                            and run.has_target_line(line.new_line_number)
                        )
                    )
                )
                or (
                    run.kind == SemanticChangeKind.DELETION
                    and line.kind == "-"
                    and run.has_source_line(line.old_line_number)
                )
                or (
                    run.kind == SemanticChangeKind.PRESENCE
                    and line.kind == "+"
                    and run.has_target_line(line.new_line_number)
                )
            )
        }
        if display_ids:
            run_sets.append(display_ids)

    return run_sets
