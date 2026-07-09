"""Boundary lookup helpers for realized batch entries."""

from __future__ import annotations

from collections.abc import Sequence

from .realized_entries import (
    RealizedEntry as _RealizedEntry,
    _RealizedEntries,
    _entry_is_claimed_at,
    _entry_source_line_at,
)
from ..exceptions import (
    AmbiguousAnchorError as _AmbiguousAnchorError,
    MissingAnchorError as _MissingAnchorError,
)
from ..i18n import _


def find_realization_fallback_boundary(
    entries: Sequence[_RealizedEntry],
    source_line: int | None,
) -> int:
    """Find a lenient boundary for realization when an anchor is absent."""
    if source_line is None:
        return 0

    prior_source_line: int | None = None
    if isinstance(entries, _RealizedEntries):
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
            entry_source_line = _entry_source_line_at(entries, index)
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

    matching_indices = []
    claimed_indices = []

    if isinstance(entries, _RealizedEntries):
        for run in entries.provenance_runs():
            if run.source_start == 0:
                continue
            run_length = run.dest_end - run.dest_start
            if not run.source_start <= source_line < run.source_start + run_length:
                continue
            index = run.dest_start + (source_line - run.source_start)
            matching_indices.append(index)
            if run.is_claimed:
                claimed_indices.append(index)
    else:
        for i in range(len(entries)):
            if _entry_source_line_at(entries, i) == source_line:
                matching_indices.append(i)
                if _entry_is_claimed_at(entries, i):
                    claimed_indices.append(i)

    if not matching_indices:
        raise _MissingAnchorError(
            _(
                "Cannot locate anchor boundary after source line {line}: "
                "anchor not present in realized content"
            ).format(line=source_line)
        )

    if len(matching_indices) > 1:
        if len(claimed_indices) == 1:
            return claimed_indices[0] + 1
        if len(claimed_indices) == 0:
            raise _AmbiguousAnchorError(
                _(
                    "Anchor ambiguity: source line {line} appears {count} times "
                    "in realized content but none are claimed"
                ).format(line=source_line, count=len(matching_indices))
            )
        raise _AmbiguousAnchorError(
            _("Anchor ambiguity: source line {line} claimed {count} times").format(
                line=source_line,
                count=len(claimed_indices),
            )
        )

    return matching_indices[0] + 1


def boundary_choices_after_source_line(
    entries: Sequence[_RealizedEntry],
    source_line: int | None,
) -> tuple[int, ...]:
    """Return all concrete boundary positions after a source line."""
    if source_line is None:
        return (0,)

    matching_indices: list[int] = []
    if isinstance(entries, _RealizedEntries):
        for run in entries.provenance_runs():
            if run.source_start == 0:
                continue
            run_length = run.dest_end - run.dest_start
            if not run.source_start <= source_line < run.source_start + run_length:
                continue
            matching_indices.append(run.dest_start + (source_line - run.source_start))
    else:
        for index in range(len(entries)):
            if _entry_source_line_at(entries, index) == source_line:
                matching_indices.append(index)

    if not matching_indices:
        raise _MissingAnchorError(
            _(
                "Cannot locate anchor boundary after source line {line}: "
                "anchor not present in realized content"
            ).format(line=source_line)
        )

    return tuple(index + 1 for index in matching_indices)
