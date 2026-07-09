"""Baseline-coordinate edit fallback for batch merge."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import TYPE_CHECKING, Any

from ..core.line_selection import LineRanges, LineSelection, coerce_line_ranges
from ..exceptions import MergeError as _MergeError
from ..i18n import _
from ..core.text_lines import normalize_line_endings
from .baseline_reference_positions import (
    baseline_reference_absence_position as _find_baseline_absence_position,
    baseline_reference_insertion_position as _find_baseline_insertion_position,
)
from .baseline_replacement_choices import (
    replacement_origin_choices_for_unit as _replacement_origin_choices_for_unit,
)
from .line_sequence_equality import (
    line_sequences_equal as _line_sequences_match,
    line_slice_equals as _line_slice_matches,
)
from .line_mapping import LineMapping
from .merge_candidates import MergeResolution as _MergeResolution

if TYPE_CHECKING:
    from .ownership import BatchOwnership
    from .ownership_absence_claims import AbsenceClaim


_BaselineLineEdit = tuple[int, int, list[bytes]]
_DEFAULT_RESOLUTION_CHOICE_LIMIT = 51


def _selection_outside_bounds(lines: LineSelection, max_line: int) -> bool:
    for line in lines:
        if line < 1 or line > max_line:
            return True
    return False


def _baseline_removal_edit(
    claim: AbsenceClaim,
    working_lines: Sequence[bytes],
) -> _BaselineLineEdit | None:
    if not claim.content_lines:
        return None

    forbidden_sequence = [
        normalize_line_endings(line)
        for line in claim.content_lines
    ]
    position = _find_baseline_absence_position(
        claim.baseline_reference,
        working_lines,
        len(forbidden_sequence),
    )
    if position is None:
        return None
    if not _line_slice_matches(working_lines, position, forbidden_sequence):
        return None
    return position, position + len(forbidden_sequence), []


def _replacement_origin_absence_bounds(
    origin: Any,
    working_lines: Sequence[bytes],
) -> tuple[int, int] | None:
    """Return the target bounds of an original replacement parent, if provable."""
    if origin is None or getattr(origin, "baseline_reference", None) is None:
        return None
    old_line_count = getattr(origin, "old_line_count", None)
    if type(old_line_count) is not int or old_line_count <= 0:
        return None

    position = _find_baseline_absence_position(
        origin.baseline_reference,
        working_lines,
        old_line_count,
    )
    if position is None:
        return None
    return position, position + old_line_count


def _replacement_edit_with_origin_guard(
    claim: AbsenceClaim,
    origin: Any,
    working_lines: Sequence[bytes],
) -> _BaselineLineEdit | None:
    """Return a removal edit only if it fits inside the original parent unit."""
    removal_edit = _baseline_removal_edit(claim, working_lines)
    if removal_edit is None:
        return None

    if origin is None:
        return removal_edit

    parent_bounds = _replacement_origin_absence_bounds(origin, working_lines)
    if parent_bounds is None:
        return None

    start, end, replacement_lines = removal_edit
    parent_start, parent_end = parent_bounds
    if start < parent_start or end > parent_end:
        return None
    return start, end, replacement_lines


def _replacement_edit_from_parent_offset(
    claim: AbsenceClaim,
    origin: Any,
    claimed_lines: Sequence[int],
    working_lines: Sequence[bytes],
) -> _BaselineLineEdit | None:
    """Place an equal-size split replacement by offset inside its parent."""
    if origin is None or not claim.content_lines:
        return None

    old_line_count = getattr(origin, "old_line_count", None)
    new_start = getattr(origin, "new_start", None)
    new_end = getattr(origin, "new_end", None)
    if (
        type(old_line_count) is not int
        or type(new_start) is not int
        or type(new_end) is not int
        or old_line_count <= 0
        or new_end < new_start
    ):
        return None

    new_line_count = new_end - new_start + 1
    if old_line_count != new_line_count:
        return None

    relative_offsets = [
        claimed_line - new_start
        for claimed_line in sorted(claimed_lines)
    ]
    if (
        not relative_offsets
        or relative_offsets[0] < 0
        or relative_offsets[-1] >= new_line_count
    ):
        return None
    if relative_offsets != list(
        range(relative_offsets[0], relative_offsets[0] + len(relative_offsets))
    ):
        return None

    forbidden_sequence = [
        normalize_line_endings(line)
        for line in claim.content_lines
    ]
    if len(forbidden_sequence) != len(relative_offsets):
        return None

    parent_bounds = _replacement_origin_absence_bounds(origin, working_lines)
    if parent_bounds is None:
        return None

    parent_start, parent_end = parent_bounds
    start = parent_start + relative_offsets[0]
    end = start + len(forbidden_sequence)
    if start < parent_start or end > parent_end:
        return None
    if not _line_slice_matches(working_lines, start, forbidden_sequence):
        return None
    return start, end, []


def _replacement_edit_from_origin_resolution(
    claim: AbsenceClaim,
    unit_index: int,
    unit: Any,
    claimed_lines: Sequence[int],
    working_lines: Sequence[bytes],
    resolution: _MergeResolution | None,
    *,
    max_results: int,
) -> _BaselineLineEdit | None:
    """Return a replacement edit from a reviewed origin-placement choice."""
    if resolution is None:
        return None

    key, choices = _replacement_origin_choices_for_unit(
        claim,
        unit_index,
        unit,
        claimed_lines,
        working_lines,
        max_results=max_results,
    )
    if key is None or key not in resolution.decisions:
        return None

    choice_index = resolution.decisions[key]
    forbidden_sequence = [
        normalize_line_endings(line)
        for line in claim.content_lines
    ]
    for choice in choices:
        if choice.choice_index == choice_index:
            return (
                choice.position,
                choice.position + len(forbidden_sequence),
                [],
            )

    raise _MergeError(_("Selected merge resolution is no longer valid"))


def _replacement_baseline_edit(
    claim: AbsenceClaim,
    unit_index: int,
    unit: Any,
    claimed_lines: Sequence[int],
    working_lines: Sequence[bytes],
    resolution: _MergeResolution | None,
    *,
    max_resolution_choices: int,
) -> _BaselineLineEdit | None:
    origin = getattr(unit, "origin", None)
    offset_edit = _replacement_edit_from_parent_offset(
        claim,
        origin,
        claimed_lines,
        working_lines,
    )
    if offset_edit is not None:
        return offset_edit

    guarded_edit = _replacement_edit_with_origin_guard(
        claim,
        origin,
        working_lines,
    )
    if guarded_edit is not None:
        return guarded_edit

    return _replacement_edit_from_origin_resolution(
        claim,
        unit_index,
        unit,
        claimed_lines,
        working_lines,
        resolution,
        max_results=max_resolution_choices,
    )


def _apply_non_overlapping_baseline_edits(
    working_lines: Sequence[bytes],
    edits: list[_BaselineLineEdit],
) -> Iterator[bytes] | None:
    sorted_edits = sorted(edits, key=lambda edit: (edit[0], edit[1]))
    previous_end = 0
    for start, end, _replacement_lines in sorted_edits:
        if start < previous_end:
            return None
        previous_end = max(previous_end, end)

    return _iter_lines_with_baseline_edits(working_lines, sorted_edits)


def _iter_lines_with_baseline_edits(
    working_lines: Sequence[bytes],
    sorted_edits: Sequence[_BaselineLineEdit],
) -> Iterator[bytes]:
    position = 0
    for start, end, replacement_lines in sorted_edits:
        for index in range(position, start):
            yield working_lines[index]
        yield from replacement_lines
        position = end

    for index in range(position, len(working_lines)):
        yield working_lines[index]


def _has_complete_baseline_references(
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: list[AbsenceClaim],
) -> bool:
    claimed_line_references = ownership.presence_baseline_references()
    for claimed_line in presence_line_set:
        reference = claimed_line_references.get(claimed_line)
        if reference is None or not getattr(reference, "has_after_line", False):
            return False
    for claim in deletion_claims:
        reference = claim.baseline_reference
        if reference is None or not getattr(reference, "has_after_line", False):
            return False
    return bool(presence_line_set or deletion_claims)


def try_apply_baseline_replacement_units(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: list[AbsenceClaim],
    *,
    resolution: _MergeResolution | None = None,
    max_resolution_choices: int = _DEFAULT_RESOLUTION_CHOICE_LIMIT,
) -> Iterator[bytes] | None:
    """Apply baseline-coordinate edits when structural source anchors fail.

    This is a conservative fallback for same-source round trips where the batch
    source is the post-change file and the target is still the pre-change
    baseline/index. In that shape, source anchors can legitimately be absent
    even though the old baseline bytes still exist at an exact recorded
    coordinate.
    """
    if _selection_outside_bounds(presence_line_set, len(source_lines)):
        return None

    if (
        _line_sequences_match(source_lines, working_lines)
        and _has_complete_baseline_references(
            ownership,
            presence_line_set,
            deletion_claims,
        )
    ):
        return iter(working_lines)

    replacement_units = getattr(ownership, "replacement_units", [])
    edits: list[_BaselineLineEdit] = []
    unit_claimed_lines = LineRanges.empty()
    unit_deletion_indices: set[int] = set()

    for unit_index, unit in enumerate(replacement_units):
        claimed_selection = LineRanges.from_specs(unit.presence_lines)
        claimed_lines = list(claimed_selection)
        if not claimed_lines or len(unit.deletion_indices) != 1:
            return None

        deletion_index = unit.deletion_indices[0]
        if deletion_index < 0 or deletion_index >= len(deletion_claims):
            return None
        replacement_lines: list[bytes] = []
        for claimed_line in claimed_lines:
            if claimed_line < 1 or claimed_line > len(source_lines):
                return None
            replacement_lines.append(source_lines[claimed_line - 1])

        removal_edit = _replacement_baseline_edit(
            deletion_claims[deletion_index],
            unit_index,
            unit,
            claimed_lines,
            working_lines,
            resolution,
            max_resolution_choices=max_resolution_choices,
        )
        if removal_edit is None:
            return None
        start, end, _removed_lines = removal_edit
        edits.append((start, end, replacement_lines))
        unit_claimed_lines = unit_claimed_lines.union(claimed_selection)
        unit_deletion_indices.add(deletion_index)

    for deletion_index, claim in enumerate(deletion_claims):
        if deletion_index in unit_deletion_indices:
            continue
        removal_edit = _baseline_removal_edit(claim, working_lines)
        if removal_edit is None:
            return None
        edits.append(removal_edit)

    presence_lines = coerce_line_ranges(presence_line_set)
    remaining_claimed_lines = presence_lines.difference(unit_claimed_lines)
    claimed_line_references = ownership.presence_baseline_references()
    if remaining_claimed_lines:
        grouped_insertions: dict[int, list[int]] = {}
        for claimed_line in sorted(remaining_claimed_lines):
            if claimed_line < 1 or claimed_line > len(source_lines):
                return None
            reference = claimed_line_references.get(claimed_line)
            position = _find_baseline_insertion_position(
                reference,
                working_lines,
            )
            if position is None:
                return None
            grouped_insertions.setdefault(position, []).append(claimed_line)

        for position, claimed_lines in grouped_insertions.items():
            insertion_lines = [
                source_lines[claimed_line - 1]
                for claimed_line in claimed_lines
            ]
            if _line_slice_matches(working_lines, position, insertion_lines):
                continue
            edits.append((
                position,
                position,
                insertion_lines,
            ))

    if unit_claimed_lines.union(remaining_claimed_lines) != presence_lines:
        return None

    return _apply_non_overlapping_baseline_edits(working_lines, edits)


def has_missing_origin_replacement_claims(
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    source_lines: Sequence[bytes],
    mapping: LineMapping,
) -> bool:
    """Return whether parent-tracked replacement lines would need placement."""
    selected_presence = coerce_line_ranges(presence_line_set)
    for unit in getattr(ownership, "replacement_units", []):
        if getattr(unit, "origin", None) is None:
            continue
        claimed_selection = LineRanges.from_specs(unit.presence_lines)
        for claimed_line in selected_presence.intersection(claimed_selection):
            if claimed_line < 1 or claimed_line > len(source_lines):
                continue
            if mapping.get_target_line_from_source_line(claimed_line) is None:
                return True
    return False
