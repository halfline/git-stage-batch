"""Baseline-coordinate edit fallback for batch merge."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...core.line_selection import LineRanges, LineSelection, coerce_line_ranges
from ...core.mapped_storage import MappedRecordVector, sort_mapped_records
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


def _insertion_boundary_identity_matches_at(
    reference: Any,
    target_lines: Sequence[bytes],
    position: int,
) -> bool:
    """Return whether one insertion position has the reference's full identity."""
    if reference is None or not getattr(reference, "has_after_line", False):
        return False

    after_line = getattr(reference, "after_line", None)
    if after_line is None:
        if position != 0:
            return False
    else:
        after_content = getattr(reference, "after_content", None)
        if (
            position == 0
            or after_content is None
            or _reference_line_payload(target_lines[position - 1])
            != _reference_line_payload(after_content)
        ):
            return False

    if getattr(reference, "has_before_line", False):
        before_line = getattr(reference, "before_line", None)
        if before_line is None:
            return position == len(target_lines)
        before_content = getattr(reference, "before_content", None)
        return (
            position < len(target_lines)
            and before_content is not None
            and _reference_line_payload(target_lines[position])
            == _reference_line_payload(before_content)
        )

    return position == len(target_lines)


def _live_insertion_boundary_is_unique(
    reference: Any,
    target_lines: Sequence[bytes],
    expected_position: int,
    occurrence_index: LinePayloadOccurrenceIndex,
) -> bool:
    """Require an insertion reference to identify one live target boundary."""
    if not _insertion_boundary_identity_matches_at(
        reference,
        target_lines,
        expected_position,
    ):
        return False

    after_line = getattr(reference, "after_line", None)
    before_line = getattr(reference, "before_line", None)
    if after_line is None or (
        getattr(reference, "has_before_line", False)
        and before_line is None
    ):
        return True

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

    if after_line is not None:
        consider(getattr(reference, "after_content", None), 1)
    if getattr(reference, "has_before_line", False) and before_line is not None:
        consider(getattr(reference, "before_content", None), 0)

    if rarest_content is None or rarest_count == 0:
        return False
    if rarest_count == 1:
        return True

    for line_index in occurrence_index.matching_line_indexes(rarest_content):
        position = line_index + rarest_boundary_delta
        if (
            position < 0
            or position > len(target_lines)
            or position == expected_position
        ):
            continue
        if _insertion_boundary_identity_matches_at(
            reference,
            target_lines,
            position,
        ):
            return False
    return True


def _replacement_origin_boundary_identity_matches_at(
    origin: Any,
    target_lines: Sequence[bytes],
    position: int,
) -> bool:
    """Return whether one target span has the replacement parent's identity."""
    reference = getattr(origin, "baseline_reference", None)
    old_line_count = getattr(origin, "old_line_count", None)
    if (
        reference is None
        or not getattr(reference, "has_after_line", False)
        or type(old_line_count) is not int
        or old_line_count <= 0
        or position < 0
        or position + old_line_count > len(target_lines)
    ):
        return False

    after_line = getattr(reference, "after_line", None)
    if after_line is None:
        if position != 0:
            return False
    else:
        after_content = getattr(reference, "after_content", None)
        if (
            position == 0
            or after_content is None
            or _reference_line_payload(target_lines[position - 1])
            != _reference_line_payload(after_content)
        ):
            return False

    if not getattr(reference, "has_before_line", False):
        return True

    before_line = getattr(reference, "before_line", None)
    before_position = position + old_line_count
    if before_line is None:
        return before_position == len(target_lines)
    before_content = getattr(reference, "before_content", None)
    return (
        before_position < len(target_lines)
        and before_content is not None
        and _reference_line_payload(target_lines[before_position])
        == _reference_line_payload(before_content)
    )


def _live_replacement_origin_boundary_is_unique(
    origin: Any,
    target_lines: Sequence[bytes],
    expected_position: int,
    occurrence_index: LinePayloadOccurrenceIndex,
) -> bool:
    """Require a replacement parent to identify one live target span."""
    if not _replacement_origin_boundary_identity_matches_at(
        origin,
        target_lines,
        expected_position,
    ):
        return False

    reference = origin.baseline_reference
    after_line = reference.after_line
    before_line = reference.before_line
    if after_line is None or (
        reference.has_before_line
        and before_line is None
    ):
        return True

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

    if after_line is not None:
        consider(reference.after_content, 1)
    if reference.has_before_line and before_line is not None:
        consider(reference.before_content, -origin.old_line_count)

    if rarest_content is None or rarest_count == 0:
        return False
    if rarest_count == 1:
        return True

    last_position = len(target_lines) - origin.old_line_count
    for line_index in occurrence_index.matching_line_indexes(rarest_content):
        position = line_index + rarest_boundary_delta
        if (
            position < 0
            or position > last_position
            or position == expected_position
        ):
            continue
        if _replacement_origin_boundary_identity_matches_at(
            origin,
            target_lines,
            position,
        ):
            return False
    return True


def _live_coordinate_edits_are_safe(
    ownership: BatchOwnership,
    working_lines: Sequence[bytes],
    deletion_claims: Sequence[AbsenceClaim],
    deletion_edit_bounds: Sequence[tuple[int, ...]],
    insertion_groups: Mapping[int, Sequence[int]] | None,
    claimed_line_references: Mapping[int, Any],
    *,
    spool_dir: str | Path | None,
) -> bool:
    """Return whether every coordinate edit has one live target boundary."""
    if not deletion_claims and not insertion_groups:
        return True

    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        occurrence_index = LinePayloadOccurrenceIndex(
            workspace,
            working_lines,
        )
        replacement_reference_count = sum(
            len(unit.deletion_indices)
            for unit in ownership.replacement_units
        )
        replacement_units_by_deletion = workspace.record_vector(
            replacement_reference_count,
            "QQ",
        )
        for unit_index, unit in enumerate(ownership.replacement_units):
            for deletion_index in unit.deletion_indices:
                if (
                    type(deletion_index) is int
                    and 0 <= deletion_index < len(deletion_claims)
                ):
                    replacement_units_by_deletion.append((
                        deletion_index,
                        unit_index,
                    ))
        sort_mapped_records(replacement_units_by_deletion)

        replacement_reference_index = 0
        for deletion_index, claim in enumerate(deletion_claims):
            (
                has_actual_bounds,
                actual_start,
                actual_end,
                coordinate_was_reviewed,
            ) = (
                deletion_edit_bounds[deletion_index]
            )
            if not has_actual_bounds:
                return False
            actual_bounds = (actual_start, actual_end)
            while (
                replacement_reference_index
                < len(replacement_units_by_deletion)
                and replacement_units_by_deletion[
                    replacement_reference_index
                ][0] < deletion_index
            ):
                replacement_reference_index += 1
            next_replacement_reference = replacement_reference_index
            while (
                next_replacement_reference
                < len(replacement_units_by_deletion)
                and replacement_units_by_deletion[
                    next_replacement_reference
                ][0] == deletion_index
            ):
                next_replacement_reference += 1
            if coordinate_was_reviewed:
                replacement_reference_index = next_replacement_reference
                continue

            removal_edit = _baseline_removal_edit(claim, working_lines)
            if (
                removal_edit is not None
                and actual_bounds == removal_edit[:2]
                and _live_removal_boundary_is_unique(
                    claim,
                    working_lines,
                    removal_edit[0],
                    occurrence_index,
                )
            ):
                replacement_reference_index = next_replacement_reference
                continue

            origin_is_safe = False
            for reference_index in range(
                replacement_reference_index,
                next_replacement_reference,
            ):
                unit_index = replacement_units_by_deletion[reference_index][1]
                unit = ownership.replacement_units[unit_index]
                origin = getattr(unit, "origin", None)
                parent_bounds = _replacement_origin_absence_bounds(
                    origin,
                    working_lines,
                )
                if (
                    parent_bounds is not None
                    and parent_bounds[0] <= actual_bounds[0]
                    and actual_bounds[1] <= parent_bounds[1]
                    and _live_replacement_origin_boundary_is_unique(
                        origin,
                        working_lines,
                        parent_bounds[0],
                        occurrence_index,
                    )
                ):
                    origin_is_safe = True
                    break
            if not origin_is_safe:
                return False
            replacement_reference_index = next_replacement_reference

        if insertion_groups is not None:
            for position, claimed_lines in insertion_groups.items():
                for claimed_line in claimed_lines:
                    reference = claimed_line_references.get(claimed_line)
                    if not _live_insertion_boundary_is_unique(
                        reference,
                        working_lines,
                        position,
                        occurrence_index,
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
) -> tuple[_BaselineLineEdit, bool] | None:
    origin = getattr(unit, "origin", None)
    guarded_edit = _replacement_edit_with_origin_guard(
        claim,
        origin,
        working_lines,
    )
    if guarded_edit is not None:
        return guarded_edit, False

    offset_edit = _replacement_edit_from_parent_offset(
        claim,
        origin,
        claimed_lines,
        working_lines,
    )
    if offset_edit is not None:
        return offset_edit, False

    reviewed_edit = _replacement_edit_from_origin_resolution(
        claim,
        unit_index,
        unit,
        claimed_lines,
        working_lines,
        resolution,
        max_results=max_resolution_choices,
    )
    if reviewed_edit is None:
        return None
    return reviewed_edit, True


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


def _all_deletions_are_already_absent(
    deletion_claims: Sequence[AbsenceClaim],
    working_lines: Sequence[bytes],
) -> bool:
    """Return whether baseline and source anchors prove removals satisfied."""
    for claim in deletion_claims:
        if not claim.content_lines:
            continue
        if _baseline_removal_edit(claim, working_lines) is not None:
            return False

        forbidden_sequence = normalize_line_sequence_endings(
            claim.content_lines
        )
        if len(forbidden_sequence) > len(working_lines):
            continue

        anchor_line = claim.anchor_line
        if anchor_line is None:
            source_position = 0
        elif (
            type(anchor_line) is not int
            or anchor_line < 1
            or anchor_line > len(working_lines)
        ):
            return False
        else:
            source_position = anchor_line

        if _line_slice_matches(
            working_lines,
            source_position,
            forbidden_sequence,
        ):
            return False

    return True


def try_apply_baseline_replacement_units(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: list[AbsenceClaim],
    *,
    resolution: _MergeResolution | None = None,
    max_resolution_choices: int = _DEFAULT_RESOLUTION_CHOICE_LIMIT,
    trust_baseline_coordinates: bool = False,
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
    ) and _all_deletions_are_already_absent(
        deletion_claims,
        working_lines,
    ):
        return iter(working_lines)

    with MappedRecordVector(
        len(deletion_claims),
        "QQQQ",
        length=len(deletion_claims),
        spool_dir=spool_dir,
    ) as deletion_edit_bounds:
        return _try_apply_baseline_replacement_units(
            source_lines,
            working_lines,
            ownership,
            presence_line_set,
            deletion_claims,
            deletion_edit_bounds,
            resolution=resolution,
            max_resolution_choices=max_resolution_choices,
            trust_baseline_coordinates=trust_baseline_coordinates,
            spool_dir=spool_dir,
        )


def _try_apply_baseline_replacement_units(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: list[AbsenceClaim],
    deletion_edit_bounds: MappedRecordVector,
    *,
    resolution: _MergeResolution | None,
    max_resolution_choices: int,
    trust_baseline_coordinates: bool,
    spool_dir: str | Path | None,
) -> Iterator[bytes] | None:
    """Build and validate exact-coordinate fallback edits."""
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

        replacement_edit = _replacement_baseline_edit(
            deletion_claims[deletion_index],
            unit_index,
            unit,
            claimed_lines,
            working_lines,
            resolution,
            max_resolution_choices=max_resolution_choices,
        )
        if replacement_edit is None:
            return None
        removal_edit, coordinate_was_reviewed = replacement_edit
        start, end, _removed_lines = removal_edit
        edits.append((start, end, replacement_lines))
        deletion_edit_bounds[deletion_index] = (
            1,
            start,
            end,
            coordinate_was_reviewed,
        )
        unit_claimed_lines = unit_claimed_lines.union(claimed_selection)
        unit_deletion_indices.add(deletion_index)

    for deletion_index, claim in enumerate(deletion_claims):
        if deletion_index in unit_deletion_indices:
            continue
        removal_edit = _baseline_removal_edit(claim, working_lines)
        if removal_edit is None:
            return None
        edits.append(removal_edit)
        deletion_edit_bounds[deletion_index] = (
            1,
            removal_edit[0],
            removal_edit[1],
            0,
        )

    presence_lines = coerce_line_ranges(presence_line_set)
    remaining_claimed_lines = presence_lines.difference(unit_claimed_lines)
    claimed_line_references = ownership.presence_baseline_references()
    grouped_insertions: dict[int, list[int]] | None = None
    if remaining_claimed_lines:
        grouped_insertions = {}
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
            if (
                not trust_baseline_coordinates
                and _line_slice_matches(
                    working_lines,
                    position,
                    insertion_lines,
                )
            ):
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
    if (
        not trust_baseline_coordinates
        and not _live_coordinate_edits_are_safe(
            ownership,
            working_lines,
            deletion_claims,
            deletion_edit_bounds,
            grouped_insertions,
            claimed_line_references,
            spool_dir=spool_dir,
        )
    ):
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
