"""Baseline-coordinate edit fallback for batch merge."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
import hashlib
from typing import TYPE_CHECKING, Any

from ..core.line_selection import LineRanges, LineSelection
from ..exceptions import MergeError as _MergeError
from ..i18n import _
from ..utils.text import normalize_line_endings
from .match import LineMapping
from .merge_candidates import MergeResolution as _MergeResolution

if TYPE_CHECKING:
    from .ownership import AbsenceClaim, BatchOwnership


_BaselineLineEdit = tuple[int, int, list[bytes]]
_DEFAULT_RESOLUTION_CHOICE_LIMIT = 51


@dataclass(frozen=True)
class ReplacementOriginChoice:
    """Concrete target placement for an origin-tracked replacement."""

    choice_index: int
    position: int
    target_after_line: int | None
    target_before_line: int | None


def _as_line_ranges(lines: LineSelection | Iterable[int]) -> LineRanges:
    if isinstance(lines, LineRanges):
        return lines
    ranges = getattr(lines, "ranges", None)
    if ranges is not None:
        return LineRanges.from_ranges(ranges())
    return LineRanges.from_lines(lines)


def _selection_outside_bounds(lines: LineSelection, max_line: int) -> bool:
    for line in lines:
        if line < 1 or line > max_line:
            return True
    return False


def _replacement_origin_ambiguity_key(
    unit_index: int,
    deletion_index: int,
    origin: Any,
    claimed_lines: Sequence[int],
    forbidden_sequence: Sequence[bytes],
) -> str:
    claimed = ",".join(str(line) for line in claimed_lines)
    digest = _sequence_digest(forbidden_sequence)
    return (
        f"replacement-origin:{unit_index}:delete:{deletion_index}:"
        f"claimed:{claimed}:old:{origin.old_start}-{origin.old_end}:"
        f"new:{origin.new_start}-{origin.new_end}:{digest}"
    )


def _sequence_digest(lines: Sequence[bytes]) -> str:
    return hashlib.sha256(b"".join(lines)).hexdigest()[:12]


def _line_payload_for_reference_match(content: Any) -> bytes:
    """Normalize one line for insertion-boundary identity checks."""
    normalized = normalize_line_endings(bytes(content))
    if normalized.endswith(b"\n"):
        return normalized[:-1]
    return normalized


def _reference_line_matches(
    target_line: bytes,
    reference_content: bytes | None,
) -> bool:
    if reference_content is None:
        return False
    return (
        _line_payload_for_reference_match(target_line)
        == _line_payload_for_reference_match(reference_content)
    )


def _baseline_reference_insertion_position(
    reference: Any,
    working_lines: Sequence[bytes],
) -> int | None:
    """Return the proven insertion position for a baseline reference."""
    if reference is None or not getattr(reference, "has_after_line", False):
        return None

    after_line = getattr(reference, "after_line", None)
    position = after_line or 0
    if position < 0 or position > len(working_lines):
        return None

    verified_boundary = False
    if after_line is not None:
        if after_line < 1 or after_line > len(working_lines):
            return None
        if not _reference_line_matches(
            working_lines[after_line - 1],
            getattr(reference, "after_content", None),
        ):
            return None
        verified_boundary = True

    if getattr(reference, "has_before_line", False):
        before_line = getattr(reference, "before_line", None)
        if before_line is None:
            if position != len(working_lines):
                return None
            verified_boundary = True
        else:
            if position >= len(working_lines):
                return None
            if not _reference_line_matches(
                working_lines[position],
                getattr(reference, "before_content", None),
            ):
                return None
            verified_boundary = True

    if not verified_boundary:
        return None
    return position


def _baseline_reference_absence_position(
    reference: Any,
    working_lines: Sequence[bytes],
    sequence_length: int,
) -> int | None:
    """Return the proven removal position for a baseline reference."""
    if reference is None or not getattr(reference, "has_after_line", False):
        return None

    after_line = getattr(reference, "after_line", None)
    position = after_line or 0
    if position < 0 or position + sequence_length > len(working_lines):
        return None

    after_content = getattr(reference, "after_content", None)
    if after_line is not None and after_content is not None:
        if after_line < 1 or after_line > len(working_lines):
            return None
        if not _reference_line_matches(
            working_lines[after_line - 1],
            after_content,
        ):
            return None

    if getattr(reference, "has_before_line", False):
        before_line = getattr(reference, "before_line", None)
        before_position = position + sequence_length
        if before_line is None:
            if before_position != len(working_lines):
                return None
        else:
            if before_position >= len(working_lines):
                return None
            if not _reference_line_matches(
                working_lines[before_position],
                getattr(reference, "before_content", None),
            ):
                return None

    return position


def _line_sequences_equal(
    left: Sequence[bytes],
    right: Sequence[bytes],
) -> bool:
    """Return whether two line sequences contain the same bytes."""
    return len(left) == len(right) and all(
        left[index] == right[index]
        for index in range(len(left))
    )


def _line_slice_equals(
    lines: Sequence[bytes],
    start: int,
    expected: Sequence[bytes],
) -> bool:
    """Return whether a sequence slice equals the expected byte lines."""
    if start < 0 or start + len(expected) > len(lines):
        return False
    return all(
        lines[start + offset] == expected[offset]
        for offset in range(len(expected))
    )


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
    position = _baseline_reference_absence_position(
        claim.baseline_reference,
        working_lines,
        len(forbidden_sequence),
    )
    if position is None:
        return None
    if not _line_slice_equals(working_lines, position, forbidden_sequence):
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

    position = _baseline_reference_absence_position(
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
    if not _line_slice_equals(working_lines, start, forbidden_sequence):
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

    key, choices = replacement_origin_choices_for_unit(
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


def replacement_origin_choices_for_unit(
    claim: AbsenceClaim,
    unit_index: int,
    unit: Any,
    claimed_lines: Sequence[int],
    working_lines: Sequence[bytes],
    *,
    max_results: int | None = None,
) -> tuple[str | None, tuple[ReplacementOriginChoice, ...]]:
    """Return explicit target placements for an origin-tracked replacement."""
    origin = getattr(unit, "origin", None)
    if origin is None or not claim.content_lines:
        return None, ()

    forbidden_sequence = [
        normalize_line_endings(line)
        for line in claim.content_lines
    ]
    if not forbidden_sequence:
        return None, ()
    if len(forbidden_sequence) > len(working_lines):
        return None, ()

    choices: list[ReplacementOriginChoice] = []
    for position in range(0, len(working_lines) - len(forbidden_sequence) + 1):
        if not _line_slice_equals(working_lines, position, forbidden_sequence):
            continue
        choices.append(
            ReplacementOriginChoice(
                choice_index=len(choices) + 1,
                position=position,
                target_after_line=None if position == 0 else position,
                target_before_line=(
                    None
                    if position + len(forbidden_sequence) >= len(working_lines)
                    else position + len(forbidden_sequence) + 1
                ),
            )
        )
        if max_results is not None and len(choices) >= max_results:
            break

    if not choices:
        return None, ()

    deletion_indices = getattr(unit, "deletion_indices", [])
    if len(deletion_indices) != 1:
        return None, ()

    key = _replacement_origin_ambiguity_key(
        unit_index,
        deletion_indices[0],
        origin,
        claimed_lines,
        forbidden_sequence,
    )
    return key, tuple(choices)


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
        _line_sequences_equal(source_lines, working_lines)
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

    presence_lines = _as_line_ranges(presence_line_set)
    remaining_claimed_lines = presence_lines.difference(unit_claimed_lines)
    claimed_line_references = ownership.presence_baseline_references()
    if remaining_claimed_lines:
        grouped_insertions: dict[int, list[int]] = {}
        for claimed_line in sorted(remaining_claimed_lines):
            if claimed_line < 1 or claimed_line > len(source_lines):
                return None
            reference = claimed_line_references.get(claimed_line)
            position = _baseline_reference_insertion_position(
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
            if _line_slice_equals(working_lines, position, insertion_lines):
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
    selected_presence = _as_line_ranges(presence_line_set)
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
