"""Presence constraint realization for batch-source merges."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from .absence_constraints import (
    apply_absence_constraints as _apply_merge_absence_constraints,
)
from .line_mapping import LineMapping
from .match import match_lines
from .merge_candidates import MergeResolution as _MergeResolution
from .presence_missing_claims import (
    mapped_missing_source_lines as _mapped_missing_source_lines,
)
from . import presence_placement_choices as _presence_placement_choices
from .realized_entries import (
    RealizedEntry as _RealizedEntry,
    _RealizedEntries,
    _RealizedEntryContentSequence,
    _entry_is_claimed_at,
    _entry_source_line_at,
)
from . import realized_mapping as _realized_mapping
from ..core.line_selection import LineRanges, LineSelection, coerce_line_ranges
from ..exceptions import MergeError as _MergeError
from ..i18n import _

if TYPE_CHECKING:
    from .ownership import AbsenceClaim


_PRESENCE_CANDIDATE_CAP = 50


def apply_presence_constraints(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    presence_line_set: LineSelection,
    *,
    source_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
) -> _RealizedEntries:
    """Apply presence constraints: ensure all claimed lines exist in result.

    Uses structural alignment to determine which claimed lines are already present
    and adds missing ones at appropriate positions. Returns structured entries
    that preserve batch-source provenance for anchored absence constraints.

    Args:
        source_lines: Batch source file lines (bytes with newlines)
        working_lines: Working tree file lines (bytes with newlines)
        presence_line_set: Source line numbers that must be present

    Returns:
        Realized entries with all claimed lines present and provenance preserved
    """
    owned_mapping: LineMapping | None = None
    mapping = source_to_working_mapping
    if mapping is None:
        owned_mapping = match_lines(source_lines, working_lines)
        mapping = owned_mapping

    try:
        return _apply_presence_constraints_with_mapping(
            source_lines,
            working_lines,
            presence_line_set,
            mapping,
            resolution=resolution,
        )
    finally:
        if owned_mapping is not None:
            owned_mapping.close()


def _apply_presence_constraints_with_mapping(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    presence_line_set: LineSelection,
    mapping: LineMapping,
    *,
    resolution: _MergeResolution | None = None,
) -> _RealizedEntries:
    """Apply presence constraints using an existing source-to-working mapping."""

    if not presence_line_set:
        result = _RealizedEntries()
        _realized_mapping.append_working_range_with_mapping(
            result,
            working_lines,
            mapping,
            0,
            len(working_lines),
            presence_line_set,
        )
        return result

    missing_claimed = _mapped_missing_source_lines(
        presence_line_set,
        len(source_lines),
        mapping,
    )

    if not missing_claimed:
        result = _RealizedEntries()
        _realized_mapping.append_working_range_with_mapping(
            result,
            working_lines,
            mapping,
            0,
            len(working_lines),
            presence_line_set,
        )
        return result

    if resolution is not None:
        presence_key, presence_choices = (
            _presence_placement_choices.presence_choices_for_missing_claimed_run(
                source_lines,
                working_lines,
                presence_line_set,
                mapping,
                max_results=_PRESENCE_CANDIDATE_CAP + 1,
            )
        )
        if presence_key is not None and presence_key in resolution.decisions:
            selected_choice_index = resolution.decisions[presence_key]
            for choice in presence_choices:
                if choice.choice_index == selected_choice_index:
                    result = _RealizedEntries()
                    _realized_mapping.append_working_range_with_mapping(
                        result,
                        working_lines,
                        mapping,
                        0,
                        choice.gap_index,
                        presence_line_set,
                    )
                    result.append_line_range_from(
                        source_lines,
                        choice.run_start - 1,
                        choice.run_end,
                        source_line_start=choice.run_start,
                        is_claimed=True,
                    )
                    _realized_mapping.append_working_range_with_mapping(
                        result,
                        working_lines,
                        mapping,
                        choice.gap_index,
                        len(working_lines),
                        presence_line_set,
                    )
                    return result
            raise _MergeError(_("Selected merge resolution is no longer valid"))

    result = _RealizedEntries()
    working_idx = 0

    for source_line in range(1, len(source_lines) + 1):
        working_line = mapping.get_target_line_from_source_line(source_line)

        if working_line is not None:
            if working_idx < working_line - 1:
                _realized_mapping.append_working_range_with_mapping(
                    result,
                    working_lines,
                    mapping,
                    working_idx,
                    working_line - 1,
                    presence_line_set,
                )
                working_idx = working_line - 1

            is_claimed = source_line in presence_line_set
            if is_claimed:
                result.append_line_from(
                    source_lines,
                    source_line - 1,
                    source_line=source_line,
                    target_line=working_idx + 1,
                    is_claimed=True
                )
            else:
                result.append_line_from(
                    working_lines,
                    working_idx,
                    source_line=source_line,
                    target_line=working_idx + 1,
                    is_claimed=False
                )
            working_idx += 1
        else:
            if source_line in missing_claimed:
                result.append_line_from(
                    source_lines,
                    source_line - 1,
                    source_line=source_line,
                    is_claimed=True
                )

    while working_idx < len(working_lines):
        _realized_mapping.append_working_range_with_mapping(
            result,
            working_lines,
            mapping,
            working_idx,
            len(working_lines),
            presence_line_set,
        )
        working_idx = len(working_lines)

    return result


def _missing_claimed_lines(
    entries: Sequence[_RealizedEntry],
    presence_line_set: LineSelection
) -> LineRanges:
    """Return claimed source lines that are not present as claimed entries."""
    claimed_ranges: list[tuple[int, int]] = []
    presence_lines = coerce_line_ranges(presence_line_set)

    if isinstance(entries, _RealizedEntries):
        for run in entries.provenance_runs():
            if not run.is_claimed or run.source_start == 0:
                continue
            claimed_ranges.append((
                run.source_start,
                run.source_start + (run.dest_end - run.dest_start) - 1,
            ))
        return presence_lines.difference(
            LineRanges.from_ranges(claimed_ranges)
        )

    for index in range(len(entries)):
        source_line = _entry_source_line_at(entries, index)
        if source_line is not None and _entry_is_claimed_at(entries, index):
            claimed_ranges.append((source_line, source_line))
    return presence_lines.difference(
        LineRanges.from_ranges(claimed_ranges)
    )


def satisfy_constraints(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    presence_line_set: LineSelection,
    deletion_claims: list["AbsenceClaim"],
    *,
    strict: bool = True,
    source_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
) -> _RealizedEntries:
    """Apply presence and absence constraints until claimed lines survive."""
    realized_entries = apply_presence_constraints(
        source_lines,
        working_lines,
        presence_line_set,
        source_to_working_mapping=source_to_working_mapping,
        resolution=resolution,
    )

    try:
        updated_entries = _apply_merge_absence_constraints(
            realized_entries,
            deletion_claims,
            strict=strict,
            resolution=resolution,
        )
        if updated_entries is not realized_entries:
            realized_entries.close()
        realized_entries = updated_entries

        if not _missing_claimed_lines(realized_entries, presence_line_set):
            return realized_entries

        previous_entries = realized_entries
        current_lines = _RealizedEntryContentSequence(previous_entries)
        try:
            updated_entries = apply_presence_constraints(
                source_lines,
                current_lines,
                presence_line_set,
                resolution=resolution,
            )
        finally:
            previous_entries.close()
        realized_entries = updated_entries

        updated_entries = _apply_merge_absence_constraints(
            realized_entries,
            deletion_claims,
            strict=strict,
            resolution=resolution,
        )
        if updated_entries is not realized_entries:
            realized_entries.close()
        realized_entries = updated_entries

        missing_claimed = _missing_claimed_lines(realized_entries, presence_line_set)
        if missing_claimed:
            if not strict:
                return realized_entries
            first_missing = missing_claimed.first()
            raise _MergeError(
                _("Cannot satisfy claimed line {line}: removed by absence constraints").format(
                    line=first_missing
                )
            )

        return realized_entries
    except Exception:
        realized_entries.close()
        raise
