"""Validate baseline-coordinate anchors against merge targets."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ...core.mapped_storage import sort_mapped_records
from ...core.text_lines import normalize_line_sequence_endings
from .baseline_reference_positions import (
    baseline_reference_absence_position as _find_baseline_absence_position,
)
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.occurrence_index import (
    LinePayloadOccurrenceIndex,
    normalized_line_payload as _reference_line_payload,
)
from ..line_matching.sequence_equality import (
    line_slice_equals as _line_slice_matches,
)

if TYPE_CHECKING:
    from ..ownership.absence_claims import AbsenceClaim
    from ..ownership.model import BatchOwnership
    from ..ownership.references import BaselineReference
    from ..ownership.replacement_units import ReplacementUnitOrigin


BaselineRemovalEdit = tuple[int, int]


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
                removal_edit = baseline_removal_edit(claim, target_lines)
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
        for pair_record in anchor_pairs:
            source_line, target_line = pair_record
            pair = (source_line, target_line)
            if pair == previous_pair:
                continue
            if source_line <= previous_source or target_line <= previous_target:
                anchor_pairs.truncate(0)
                yield cast(Sequence[tuple[int, int]], anchor_pairs)
                return
            anchor_pairs[anchor_count] = pair
            anchor_count += 1
            previous_pair = pair
            previous_source = source_line
            previous_target = target_line

        anchor_pairs.truncate(anchor_count)
        yield cast(Sequence[tuple[int, int]], anchor_pairs)


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


def baseline_removal_edit(
    claim: AbsenceClaim,
    working_lines: Sequence[bytes],
) -> BaselineRemovalEdit | None:
    """Return the verified target span recorded by a removal claim."""
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
    return position, position + len(forbidden_sequence)


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


def _boundary_identity_occurs_once(
    occurrence_index: LinePayloadOccurrenceIndex,
    expected_position: int,
    last_position: int,
    *,
    after_content: bytes | None,
    span_contents: Sequence[bytes],
    before_content: bytes | None,
    before_delta: int,
    identity_matches_at: Callable[[int], bool],
) -> bool:
    """Return whether indexed boundary content identifies one full identity."""
    rarest_content: bytes | None = None
    rarest_boundary_delta = 0
    rarest_count: int | None = None

    def consider(content: bytes | None, boundary_delta: int) -> None:
        nonlocal rarest_content
        nonlocal rarest_boundary_delta
        nonlocal rarest_count
        if content is None:
            return
        count = occurrence_index.occurrence_count(content)
        if rarest_count is None or count < rarest_count:
            rarest_content = content
            rarest_boundary_delta = boundary_delta
            rarest_count = count

    consider(after_content, 1)
    for offset, content in enumerate(span_contents):
        consider(content, -offset)
    consider(before_content, before_delta)

    if rarest_content is None or rarest_count is None or rarest_count == 0:
        return False
    if rarest_count == 1:
        return True

    for line_index in occurrence_index.matching_line_indexes(rarest_content):
        position = line_index + rarest_boundary_delta
        if (
            position < 0
            or position > last_position
            or position == expected_position
        ):
            continue
        if identity_matches_at(position):
            return False
    return True


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
    return _boundary_identity_occurs_once(
        occurrence_index,
        expected_position,
        len(target_lines) - len(forbidden_sequence),
        after_content=(
            reference.after_content if reference.after_line is not None else None
        ),
        span_contents=forbidden_sequence,
        before_content=(
            reference.before_content
            if reference.has_before_line and reference.before_line is not None
            else None
        ),
        before_delta=-len(forbidden_sequence),
        identity_matches_at=lambda position: _removal_boundary_identity_matches_at(
            claim,
            target_lines,
            position,
            forbidden_sequence,
        ),
    )


def _insertion_boundary_identity_matches_at(
    reference: BaselineReference | None,
    target_lines: Sequence[bytes],
    position: int,
) -> bool:
    """Return whether one insertion position has the reference's full identity."""
    if reference is None or not reference.has_after_line:
        return False

    after_line = reference.after_line
    if after_line is None:
        if position != 0:
            return False
    else:
        after_content = reference.after_content
        if (
            position == 0
            or after_content is None
            or _reference_line_payload(target_lines[position - 1])
            != _reference_line_payload(after_content)
        ):
            return False

    if reference.has_before_line:
        before_line = reference.before_line
        if before_line is None:
            return position == len(target_lines)
        before_content = reference.before_content
        return (
            position < len(target_lines)
            and before_content is not None
            and _reference_line_payload(target_lines[position])
            == _reference_line_payload(before_content)
        )

    return position == len(target_lines)


def _live_insertion_boundary_is_unique(
    reference: BaselineReference | None,
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

    assert reference is not None
    after_line = reference.after_line
    before_line = reference.before_line
    if after_line is None or (
        reference.has_before_line
        and before_line is None
    ):
        return True

    return _boundary_identity_occurs_once(
        occurrence_index,
        expected_position,
        len(target_lines),
        after_content=(
            reference.after_content
            if after_line is not None
            else None
        ),
        span_contents=(),
        before_content=(
            reference.before_content
            if (
                reference.has_before_line and before_line is not None
            )
            else None
        ),
        before_delta=0,
        identity_matches_at=lambda position: _insertion_boundary_identity_matches_at(
            reference,
            target_lines,
            position,
        ),
    )


def _replacement_origin_boundary_identity_matches_at(
    origin: ReplacementUnitOrigin | None,
    target_lines: Sequence[bytes],
    position: int,
) -> bool:
    """Return whether one target span has the replacement parent's identity."""
    if origin is None:
        return False
    reference = origin.baseline_reference
    old_line_count = origin.old_line_count
    if (
        reference is None
        or not reference.has_after_line
        or old_line_count <= 0
        or position < 0
        or position + old_line_count > len(target_lines)
    ):
        return False

    after_line = reference.after_line
    if after_line is None:
        if position != 0:
            return False
    else:
        after_content = reference.after_content
        if (
            position == 0
            or after_content is None
            or _reference_line_payload(target_lines[position - 1])
            != _reference_line_payload(after_content)
        ):
            return False

    if not reference.has_before_line:
        return True

    before_line = reference.before_line
    before_position = position + old_line_count
    if before_line is None:
        return before_position == len(target_lines)
    before_content = reference.before_content
    return (
        before_position < len(target_lines)
        and before_content is not None
        and _reference_line_payload(target_lines[before_position])
        == _reference_line_payload(before_content)
    )


def _live_replacement_origin_boundary_is_unique(
    origin: ReplacementUnitOrigin | None,
    target_lines: Sequence[bytes],
    expected_position: int,
    occurrence_index: LinePayloadOccurrenceIndex,
) -> bool:
    """Require a replacement parent to identify one live target span."""
    if origin is None or not _replacement_origin_boundary_identity_matches_at(
        origin,
        target_lines,
        expected_position,
    ):
        return False

    reference = origin.baseline_reference
    if reference is None:
        return False
    after_line = reference.after_line
    before_line = reference.before_line
    if after_line is None or (
        reference.has_before_line
        and before_line is None
    ):
        return True

    return _boundary_identity_occurs_once(
        occurrence_index,
        expected_position,
        len(target_lines) - origin.old_line_count,
        after_content=(reference.after_content if after_line is not None else None),
        span_contents=(),
        before_content=(
            reference.before_content
            if reference.has_before_line and before_line is not None
            else None
        ),
        before_delta=-origin.old_line_count,
        identity_matches_at=lambda position: (
            _replacement_origin_boundary_identity_matches_at(
                origin,
                target_lines,
                position,
            )
        ),
    )


def live_coordinate_edits_are_safe(
    ownership: BatchOwnership,
    working_lines: Sequence[bytes],
    deletion_claims: Sequence[AbsenceClaim],
    deletion_edit_bounds: Sequence[tuple[int, ...]],
    positioned_insertion_lines: Sequence[tuple[int, ...]] | None,
    *,
    spool_dir: str | Path | None,
) -> bool:
    """Return whether every coordinate edit has one live target boundary."""
    if not deletion_claims and not positioned_insertion_lines:
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

            removal_edit = baseline_removal_edit(claim, working_lines)
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
                parent_bounds = replacement_origin_absence_bounds(
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

        if positioned_insertion_lines is not None:
            for position, claimed_line in positioned_insertion_lines:
                reference = ownership.presence_baseline_reference(
                    claimed_line
                )
                if not _live_insertion_boundary_is_unique(
                    reference,
                    working_lines,
                    position,
                    occurrence_index,
                ):
                    return False

    return True


def replacement_origin_absence_bounds(
    origin: ReplacementUnitOrigin | None,
    working_lines: Sequence[bytes],
) -> tuple[int, int] | None:
    """Return the target bounds of an original replacement parent, if provable."""
    if origin is None or origin.baseline_reference is None:
        return None
    old_line_count = origin.old_line_count
    if old_line_count <= 0:
        return None

    position = _find_baseline_absence_position(
        origin.baseline_reference,
        working_lines,
        old_line_count,
    )
    if position is None:
        return None
    return position, position + old_line_count
