"""Baseline-coordinate edit fallback for batch merge."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...core.line_selection import LineRanges, LineSelection, coerce_line_ranges
from ...core.mapped_storage import sort_mapped_records
from ...exceptions import MergeError as _MergeError
from ...i18n import _
from ...core.text_lines import normalize_line_sequence_endings
from .baseline_reference_positions import (
    baseline_reference_absence_position as _find_baseline_absence_position,
    baseline_reference_insertion_position as _find_baseline_insertion_position,
)
from .baseline_replacement_choices import (
    replacement_origin_choices_for_unit as _replacement_origin_choices_for_unit,
)
from ..line_matching.sequence_equality import (
    line_sequences_equal as _line_sequences_match,
    line_slice_equals as _line_slice_matches,
)
from ..line_matching.line_mapping import LineMapping
from ..line_matching.match import match_lines as _match_lines
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.occurrence_index import (
    LinePayloadOccurrenceIndex,
    normalized_line_payload as _reference_line_payload,
)
from .candidates import MergeResolution as _MergeResolution

if TYPE_CHECKING:
    from ..ownership.model import BatchOwnership
    from ..ownership.absence_claims import AbsenceClaim


_BaselineLineEdit = tuple[int, int, list[bytes]]
_DEFAULT_RESOLUTION_CHOICE_LIMIT = 51


def _selection_outside_bounds(lines: LineSelection, max_line: int) -> bool:
    for line in lines:
        if line < 1 or line > max_line:
            return True
    return False


@contextmanager
def acquire_deletion_anchor_pairs_for_target(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    deletion_claims: Sequence[AbsenceClaim],
    *,
    trust_baseline_coordinates: bool = False,
    spool_dir: str | Path | None = None,
) -> Iterator[Sequence[tuple[int, int]]]:
    """Return coherent source-to-target anchors recorded by deletion claims.

    Exact baseline realization may trust recorded coordinates after checking
    the deleted bytes. Live targets additionally require the stored boundary
    identity to match before a coordinate can constrain structural alignment.
    """
    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        anchor_pairs = workspace.record_vector(
            len(deletion_claims),
            "QQ",
        )
        occurrence_index: LinePayloadOccurrenceIndex | None = None
        for claim in deletion_claims:
            source_line = claim.anchor_line
            reference = claim.baseline_reference
            target_line = None if reference is None else reference.after_line
            if type(source_line) is not int or type(target_line) is not int:
                continue
            if source_line < 1 or source_line > len(source_lines):
                continue
            if target_line < 1 or target_line > len(target_lines):
                continue
            if source_lines[source_line - 1] != target_lines[target_line - 1]:
                continue

            if trust_baseline_coordinates:
                forbidden_sequence = normalize_line_sequence_endings(
                    claim.content_lines
                )
                if not forbidden_sequence or not _line_slice_matches(
                    target_lines,
                    target_line,
                    forbidden_sequence,
                ):
                    continue
            else:
                removal_edit = _baseline_removal_edit(claim, target_lines)
                if removal_edit is None:
                    continue
                if (
                    not _removal_boundary_is_fixed_to_file_edge(claim)
                    and occurrence_index is None
                ):
                    occurrence_index = LinePayloadOccurrenceIndex(
                        workspace,
                        target_lines,
                    )
                if not _live_removal_boundary_is_unique(
                    claim,
                    target_lines,
                    removal_edit[0],
                    occurrence_index,
                ):
                    continue

            anchor_pairs.append((source_line, target_line))

        sort_mapped_records(anchor_pairs)
        previous_pair: tuple[int, int] | None = None
        previous_source = 0
        previous_target = 0
        anchor_count = 0
        for pair in anchor_pairs:
            if pair == previous_pair:
                continue
            source_line, target_line = pair
            if source_line <= previous_source or target_line <= previous_target:
                anchor_pairs.truncate(0)
                yield anchor_pairs
                return
            anchor_pairs[anchor_count] = pair
            anchor_count += 1
            previous_pair = pair
            previous_source = source_line
            previous_target = target_line

        anchor_pairs.truncate(anchor_count)
        yield anchor_pairs


def _removal_boundary_is_fixed_to_file_edge(claim: AbsenceClaim) -> bool:
    """Return whether a boundary marker admits only one target position."""
    reference = claim.baseline_reference
    return reference is not None and (
        reference.after_line is None
        or (
            reference.has_before_line
            and reference.before_line is None
        )
    )


def _baseline_removal_edit(
    claim: AbsenceClaim,
    working_lines: Sequence[bytes],
) -> _BaselineLineEdit | None:
    if not claim.content_lines:
        return None

    forbidden_sequence = normalize_line_sequence_endings(claim.content_lines)
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


def _removal_boundary_identity_matches_at(
    claim: AbsenceClaim,
    target_lines: Sequence[bytes],
    position: int,
    forbidden_sequence: Sequence[bytes],
) -> bool:
    """Return whether one target position has the claim's full identity."""
    reference = claim.baseline_reference
    if reference is None or not reference.has_after_line:
        return False
    if not _line_slice_matches(target_lines, position, forbidden_sequence):
        return False

    if reference.after_line is None:
        if position != 0:
            return False
    else:
        if position == 0 or reference.after_content is None:
            return False
        if _reference_line_payload(target_lines[position - 1]) != (
            _reference_line_payload(reference.after_content)
        ):
            return False

    if not reference.has_before_line:
        return True

    before_position = position + len(forbidden_sequence)
    if reference.before_line is None:
        return before_position == len(target_lines)
    if before_position >= len(target_lines) or reference.before_content is None:
        return False
    return _reference_line_payload(target_lines[before_position]) == (
        _reference_line_payload(reference.before_content)
    )


def _live_removal_boundary_is_unique(
    claim: AbsenceClaim,
    target_lines: Sequence[bytes],
    expected_position: int,
    occurrence_index: LinePayloadOccurrenceIndex | None,
) -> bool:
    """Require a live deletion boundary to identify one target occurrence."""
    forbidden_sequence = normalize_line_sequence_endings(claim.content_lines)
    if not forbidden_sequence or not _removal_boundary_identity_matches_at(
        claim,
        target_lines,
        expected_position,
        forbidden_sequence,
    ):
        return False
    if _removal_boundary_is_fixed_to_file_edge(claim):
        return True
    if occurrence_index is None:
        return False

    reference = claim.baseline_reference
    assert reference is not None
    rarest_content: bytes | None = None
    rarest_boundary_delta = 0
    rarest_count = len(target_lines) + 1

    def consider(content: bytes | None, boundary_delta: int) -> None:
        nonlocal rarest_content
        nonlocal rarest_boundary_delta
        nonlocal rarest_count
        if content is None:
            return
        count = occurrence_index.occurrence_count(content)
        if count < rarest_count:
            rarest_content = content
            rarest_boundary_delta = boundary_delta
            rarest_count = count

    if reference.after_line is not None:
        consider(reference.after_content, 1)
    for offset in range(len(forbidden_sequence)):
        consider(forbidden_sequence[offset], -offset)
    if reference.has_before_line and reference.before_line is not None:
        consider(reference.before_content, -len(forbidden_sequence))

    if rarest_content is None or rarest_count == 0:
        return False
    if rarest_count == 1:
        return True

    last_position = len(target_lines) - len(forbidden_sequence)
    for line_index in occurrence_index.matching_line_indexes(rarest_content):
        position = line_index + rarest_boundary_delta
        if (
            position < 0
            or position > last_position
            or position == expected_position
        ):
            continue
        if _removal_boundary_identity_matches_at(
            claim,
            target_lines,
            position,
            forbidden_sequence,
        ):
            return False
    return True


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

    if not claimed_lines:
        return None

    first_claimed_line = claimed_lines[0]
    if any(
        claimed_line != first_claimed_line + offset
        for offset, claimed_line in enumerate(claimed_lines)
    ):
        return None

    forbidden_sequence = normalize_line_sequence_endings(claim.content_lines)
    if len(forbidden_sequence) != len(claimed_lines):
        return None

    parent_bounds = _replacement_origin_absence_bounds(origin, working_lines)
    if parent_bounds is None:
        return None

    claim_reference = claim.baseline_reference
    origin_reference = getattr(origin, "baseline_reference", None)
    if (
        claim_reference is not None
        and getattr(claim_reference, "has_after_line", False)
        and origin_reference is not None
        and getattr(origin_reference, "has_after_line", False)
    ):
        relative_offset = (
            (getattr(claim_reference, "after_line", None) or 0)
            - (getattr(origin_reference, "after_line", None) or 0)
        )
    else:
        relative_offset = first_claimed_line - new_start

    if (
        relative_offset < 0
        or relative_offset + len(claimed_lines) > new_line_count
    ):
        return None

    parent_start, parent_end = parent_bounds
    start = parent_start + relative_offset
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
    forbidden_sequence = normalize_line_sequence_endings(claim.content_lines)
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
    spool_dir: str | Path | None = None,
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

    if _line_sequences_match(
        source_lines, working_lines
    ) and _has_complete_baseline_references(
        ownership,
        presence_line_set,
        deletion_claims,
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
        has_mapped_claimed_lines = False
        for claimed_line in sorted(remaining_claimed_lines):
            if claimed_line < 1 or claimed_line > len(source_lines):
                return None
            reference = claimed_line_references.get(claimed_line)
            position = _find_baseline_insertion_position(
                reference,
                working_lines,
            )
            if position is None:
                has_mapped_claimed_lines = True
                continue
            grouped_insertions.setdefault(position, []).append(claimed_line)

        if has_mapped_claimed_lines:
            with _match_lines(
                source_lines,
                working_lines,
                spool_dir=spool_dir,
            ) as mapping:
                for claimed_line in remaining_claimed_lines:
                    reference = claimed_line_references.get(claimed_line)
                    if _find_baseline_insertion_position(
                        reference,
                        working_lines,
                    ) is not None:
                        continue
                    target_line = mapping.get_target_line_from_source_line(
                        claimed_line
                    )
                    if target_line is None or any(
                        start <= target_line - 1 < end
                        for start, end, _replacement_lines in edits
                    ):
                        return None

        for position, claimed_lines in grouped_insertions.items():
            insertion_lines = [
                source_lines[claimed_line - 1] for claimed_line in claimed_lines
            ]
            if _line_slice_matches(working_lines, position, insertion_lines):
                continue
            edits.append(
                (
                    position,
                    position,
                    insertion_lines,
                )
            )

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
