"""Boundary lookup helpers for realized batch entries."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from .entries import RealizedEntry as _RealizedEntry
from .entry_storage import (
    RealizedEntries,
    realized_entry_is_claimed_at,
    realized_entry_source_line_at,
)
from ...exceptions import (
    AmbiguousAnchorError as _AmbiguousAnchorError,
    MissingAnchorError as _MissingAnchorError,
)
from ...i18n import _, ngettext


def find_realization_fallback_boundary(
    entries: Sequence[_RealizedEntry],
    source_line: int | None,
) -> int:
    """Find a lenient boundary for realization when an anchor is absent."""
    if source_line is None:
        return 0

    prior_source_line: int | None = None
    if isinstance(entries, RealizedEntries):
        for run in entries.provenance_runs():
            if run.source_start == 0:
                continue
            run_length = run.dest_end - run.dest_start
            run_source_end = run.source_start + run_length
            if run.source_start >= source_line:
                continue
            candidate = min(source_line - 1, run_source_end - 1)
            if candidate >= run.source_start:
                prior_source_line = max(prior_source_line or candidate, candidate)
    else:
        for index in range(len(entries)):
            entry_source_line = realized_entry_source_line_at(entries, index)
            if entry_source_line is not None and entry_source_line < source_line:
                prior_source_line = max(
                    prior_source_line or entry_source_line,
                    entry_source_line,
                )

    if prior_source_line is None:
        return 0

    return find_boundary_after_source_line(entries, prior_source_line)


def find_boundary_after_source_line(
    entries: Sequence[_RealizedEntry],
    source_line: int | None,
) -> int:
    """Find the index representing the boundary after a source line."""
    if source_line is None:
        return 0

    matching_count = 0
    claimed_count = 0
    matching_index = 0
    claimed_index = 0

    if isinstance(entries, RealizedEntries):
        for run in entries.provenance_runs():
            if run.source_start == 0:
                continue
            run_length = run.dest_end - run.dest_start
            if not run.source_start <= source_line < run.source_start + run_length:
                continue
            index = run.dest_start + (source_line - run.source_start)
            matching_count += 1
            matching_index = index
            if run.is_claimed:
                claimed_count += 1
                claimed_index = index
    else:
        for i in range(len(entries)):
            if realized_entry_source_line_at(entries, i) == source_line:
                matching_count += 1
                matching_index = i
                if realized_entry_is_claimed_at(entries, i):
                    claimed_count += 1
                    claimed_index = i

    if matching_count == 0:
        raise _MissingAnchorError(
            _(
                "Cannot locate anchor boundary after source line {line}: "
                "anchor not present in realized content"
            ).format(line=source_line)
        )

    if matching_count > 1:
        if claimed_count == 1:
            return claimed_index + 1
        if claimed_count == 0:
            raise _AmbiguousAnchorError(
                ngettext(
                    "Anchor ambiguity: source line {line} appears {count} time "
                    "in realized content but is not claimed",
                    "Anchor ambiguity: source line {line} appears {count} times "
                    "in realized content but none are claimed",
                    matching_count,
                ).format(line=source_line, count=matching_count)
            )
        raise _AmbiguousAnchorError(
            ngettext(
                "Anchor ambiguity: source line {line} claimed {count} time",
                "Anchor ambiguity: source line {line} claimed {count} times",
                claimed_count,
            ).format(
                line=source_line,
                count=claimed_count,
            )
        )

    return matching_index + 1


def boundary_choices_after_source_line(
    entries: Sequence[_RealizedEntry],
    source_line: int | None,
) -> Iterator[int]:
    """Yield concrete boundary positions without collecting every match."""
    if source_line is None:
        yield 0
        return

    found = False
    if isinstance(entries, RealizedEntries):
        for run in entries.provenance_runs():
            if run.source_start == 0:
                continue
            run_length = run.dest_end - run.dest_start
            if not run.source_start <= source_line < run.source_start + run_length:
                continue
            found = True
            yield run.dest_start + (source_line - run.source_start) + 1
    else:
        for index in range(len(entries)):
            if realized_entry_source_line_at(entries, index) == source_line:
                found = True
                yield index + 1

    if not found:
        raise _MissingAnchorError(
            _(
                "Cannot locate anchor boundary after source line {line}: "
                "anchor not present in realized content"
            ).format(line=source_line)
        )
