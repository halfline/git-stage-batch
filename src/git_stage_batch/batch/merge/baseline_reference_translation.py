"""Translate ownership references between saved baseline files."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ...core.mapped_storage import (
    MappedIntVector,
    MappedRecordVector,
    sort_mapped_records,
)
from ...core.text_lines import (
    as_acquirable_line_sequence,
    normalize_line_endings,
    normalize_line_sequence_endings,
)
from ..line_matching.line_range_view import LineRangeView
from ..line_matching.line_mapping import LineMapping
from ..line_matching.match import match_lines
from ..ownership.absence_content import build_absence_content_from_range
from ..ownership.absence_claims import AbsenceClaim
from ..ownership.model import BatchOwnership
from ..ownership.references import BaselineReference
from ..ownership.replacement_units import ReplacementUnitOrigin
from .baseline_reference_positions import (
    baseline_reference_insertion_position,
)


def _reference_for_insertion(
    target_lines: Sequence[bytes],
    position: int,
) -> BaselineReference:
    """Return an exact two-sided reference for one target gap."""
    after_line = position or None
    before_line = position + 1 if position < len(target_lines) else None
    return BaselineReference(
        after_line=after_line,
        after_content=(
            bytes(target_lines[position - 1]) if position > 0 else None
        ),
        has_after_line=True,
        before_line=before_line,
        before_content=(
            bytes(target_lines[position])
            if position < len(target_lines)
            else None
        ),
        has_before_line=True,
    )


def _reference_for_removal(
    target_lines: Sequence[bytes],
    position: int,
    line_count: int,
) -> BaselineReference:
    """Return an exact two-sided reference for one target line range."""
    before_position = position + line_count
    return BaselineReference(
        after_line=position or None,
        after_content=(
            bytes(target_lines[position - 1]) if position > 0 else None
        ),
        has_after_line=True,
        before_line=(
            before_position + 1
            if before_position < len(target_lines)
            else None
        ),
        before_content=(
            bytes(target_lines[before_position])
            if before_position < len(target_lines)
            else None
        ),
        has_before_line=True,
    )


def _line_payload(content: bytes) -> bytes:
    normalized = normalize_line_endings(bytes(content))
    if normalized.endswith(b"\n"):
        return normalized[:-1]
    return normalized


def _reference_content_matches(
    line: bytes,
    reference_content: bytes | None,
) -> bool:
    return (
        reference_content is not None
        and _line_payload(line) == _line_payload(reference_content)
    )


def _recorded_removal_position(
    reference: BaselineReference | None,
    content_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
) -> int | None:
    """Return a removal position recorded against this exact baseline."""
    if reference is None or not reference.has_after_line or not content_lines:
        return None

    position = reference.after_line or 0
    if position < 0 or position + len(content_lines) > len(baseline_lines):
        return None
    if any(
        baseline_lines[position + offset] != content
        for offset, content in enumerate(content_lines)
    ):
        return None

    if reference.after_line is not None and reference.after_content is not None:
        if position == 0 or not _reference_content_matches(
            baseline_lines[position - 1],
            reference.after_content,
        ):
            return None

    if reference.has_before_line:
        before_position = position + len(content_lines)
        if reference.before_line is None:
            if before_position != len(baseline_lines):
                return None
        elif reference.before_content is not None:
            if before_position >= len(baseline_lines) or not (
                _reference_content_matches(
                    baseline_lines[before_position],
                    reference.before_content,
                )
            ):
                return None

    return position


def _mapped_removal_position(
    source_position: int,
    line_count: int,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    mapping: LineMapping,
) -> int | None:
    """Map one exact source range into a coherent target range."""
    candidates: set[int] = set()
    first_target_line: int | None = None
    content_range_is_contiguous = line_count > 0
    for offset, source_line in enumerate(range(
        source_position + 1,
        source_position + line_count + 1,
    )):
        target_line = mapping.get_target_line_from_source_line(source_line)
        if target_line is None:
            content_range_is_contiguous = False
            break
        if first_target_line is None:
            first_target_line = target_line
        elif target_line != first_target_line + offset:
            content_range_is_contiguous = False
            break
    if content_range_is_contiguous and first_target_line is not None:
        candidates.add(first_target_line - 1)

    previous_target_line = (
        None
        if source_position == 0
        else mapping.get_target_line_from_source_line(source_position)
    )
    next_source_line = source_position + line_count + 1
    next_target_line = (
        None
        if next_source_line > len(source_lines)
        else mapping.get_target_line_from_source_line(next_source_line)
    )
    if previous_target_line is not None and next_target_line is not None:
        if next_target_line - previous_target_line == line_count + 1:
            candidates.add(previous_target_line)
    elif source_position == 0 and next_target_line == line_count + 1:
        candidates.add(0)
    elif (
        next_source_line > len(source_lines)
        and previous_target_line is not None
        and len(target_lines) - previous_target_line == line_count
    ):
        candidates.add(previous_target_line)

    if len(candidates) != 1:
        return None
    target_position = next(iter(candidates))

    if previous_target_line is not None and target_position != previous_target_line:
        return None
    if (
        next_target_line is not None
        and target_position + line_count != next_target_line - 1
    ):
        return None
    if target_position < 0 or target_position + line_count > len(target_lines):
        return None
    return target_position


def _translate_removal_reference(
    reference: BaselineReference | None,
    content_lines: Sequence[bytes],
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    mapping: LineMapping,
    *,
    allow_content_change: bool = False,
) -> tuple[BaselineReference, int] | None:
    source_position = _recorded_removal_position(
        reference,
        content_lines,
        source_lines,
    )
    if source_position is None:
        target_position = _recorded_removal_position(
            reference,
            content_lines,
            target_lines,
        )
        if target_position is None:
            return None
        assert reference is not None
        return reference, target_position

    target_position = _mapped_removal_position(
        source_position,
        len(content_lines),
        source_lines,
        target_lines,
        mapping,
    )
    if target_position is None or (
        not allow_content_change
        and any(
            target_lines[target_position + offset] != content
            for offset, content in enumerate(content_lines)
        )
    ):
        return None
    return (
        _reference_for_removal(
            target_lines,
            target_position,
            len(content_lines),
        ),
        target_position,
    )


def _translate_presence_references(
    ownership: BatchOwnership,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    mapping: LineMapping,
) -> None:
    reference_count = sum(
        len(claim.baseline_references)
        for claim in ownership.presence_claims
    )
    with (
        MappedRecordVector(reference_count, "QQQ") as referenced_lines,
        MappedRecordVector(reference_count, "QQ") as dropped_references,
    ):
        previous_source_position = -1
        records_are_sorted = True
        for claim_index, claim in enumerate(ownership.presence_claims):
            for claimed_line, reference in claim.baseline_references.items():
                source_position = baseline_reference_insertion_position(
                    reference,
                    source_lines,
                )
                if source_position is None:
                    if baseline_reference_insertion_position(
                        reference,
                        target_lines,
                    ) is None:
                        dropped_references.append((
                            claim_index,
                            claimed_line,
                        ))
                    continue
                if source_position < previous_source_position:
                    records_are_sorted = False
                referenced_lines.append((
                    source_position,
                    claim_index,
                    claimed_line,
                ))
                previous_source_position = source_position

        for claim_index, claimed_line in dropped_references:
            ownership.presence_claims[
                claim_index
            ].baseline_references.pop(claimed_line, None)

        if not records_are_sorted:
            sort_mapped_records(referenced_lines)
        mapped_pairs = mapping.mapped_line_pairs()
        previous_pair: tuple[int, int] | None = None
        next_pair = next(mapped_pairs, None)
        for source_position, claim_index, claimed_line in referenced_lines:
            while next_pair is not None and next_pair[0] <= source_position:
                previous_pair = next_pair
                next_pair = next(mapped_pairs, None)

            target_position = (
                previous_pair[1] if previous_pair is not None else 0
            )
            target_before_line = (
                next_pair[1]
                if next_pair is not None
                else len(target_lines) + 1
            )
            claim = ownership.presence_claims[claim_index]
            if target_before_line != target_position + 1:
                claim.baseline_references.pop(claimed_line, None)
                continue
            claim.baseline_references[claimed_line] = _reference_for_insertion(
                target_lines,
                target_position,
            )


def _mark_origin_backed_deletions(
    ownership: BatchOwnership,
    origin_backed: MappedIntVector,
) -> None:
    """Mark deletion indexes whose coordinates come from replacement origins."""
    for unit in ownership.replacement_units:
        if unit.origin is None:
            continue
        for deletion_index in unit.deletion_indices:
            if (
                type(deletion_index) is int
                and 0 <= deletion_index < len(origin_backed)
            ):
                origin_backed[deletion_index] = 1


def _translate_deletion_references(
    ownership: BatchOwnership,
    deletion_indices: Iterable[int],
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    mapping: LineMapping,
) -> None:
    """Translate the selected deletion references in one baseline space."""
    for deletion_index in deletion_indices:
        if deletion_index < 0 or deletion_index >= len(ownership.deletions):
            continue
        deletion = ownership.deletions[deletion_index]
        content_lines = normalize_line_sequence_endings(
            deletion.content_lines
        )
        translated = _translate_removal_reference(
            deletion.baseline_reference,
            content_lines,
            source_lines,
            target_lines,
            mapping,
        )
        ownership.deletions[deletion_index] = _translated_deletion(
            deletion,
            content_lines,
            target_lines,
            translated,
        )


def _translated_deletion(
    deletion: AbsenceClaim,
    content_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    translated: tuple[BaselineReference, int] | None,
) -> AbsenceClaim:
    """Return projected reference and target-baseline removal content."""
    if translated is None:
        return AbsenceClaim(
            anchor=deletion.anchor,
            content_lines=deletion.content_lines,
            baseline_reference=None,
            source_alternative=deletion.source_alternative,
        )

    baseline_reference, target_position = translated
    translated_content = deletion.content_lines
    if any(
        target_lines[target_position + offset] != content
        for offset, content in enumerate(content_lines)
    ):
        translated_content = build_absence_content_from_range(
            target_lines,
            target_position,
            target_position + len(content_lines),
        )
    return AbsenceClaim(
        anchor=deletion.anchor,
        content_lines=translated_content,
        baseline_reference=baseline_reference,
        source_alternative=deletion.source_alternative,
    )


def _translate_deletion_from_origin_offset(
    deletion: AbsenceClaim,
    origin: ReplacementUnitOrigin,
    target_lines: Sequence[bytes],
) -> tuple[BaselineReference, int] | None:
    """Project one split replacement by its offset inside the parent."""
    reference = deletion.baseline_reference
    origin_reference = origin.baseline_reference
    old_start = origin.old_start
    old_line_count = origin.old_line_count
    if (
        reference is None
        or not reference.has_after_line
        or origin_reference is None
        or not origin_reference.has_after_line
        or type(old_start) is not int
        or type(old_line_count) is not int
        or old_line_count <= 0
    ):
        return None

    source_position = reference.after_line or 0
    relative_offset = source_position - (old_start - 1)
    line_count = len(deletion.content_lines)
    if (
        relative_offset < 0
        or line_count <= 0
        or relative_offset + line_count > old_line_count
    ):
        return None

    target_position = (origin_reference.after_line or 0) + relative_offset
    if target_position + line_count > len(target_lines):
        return None
    return (
        _reference_for_removal(
            target_lines,
            target_position,
            line_count,
        ),
        target_position,
    )


def _translate_origin_backed_deletion_references(
    ownership: BatchOwnership,
    origin_backed: MappedIntVector,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    mapping: LineMapping,
) -> None:
    """Translate replacement deletions from live HEAD, with parent fallback."""
    for unit in ownership.replacement_units:
        origin = unit.origin
        if origin is None:
            continue
        for deletion_index in unit.deletion_indices:
            if (
                type(deletion_index) is not int
                or deletion_index < 0
                or deletion_index >= len(ownership.deletions)
                or origin_backed[deletion_index] != 1
            ):
                continue

            deletion = ownership.deletions[deletion_index]
            content_lines = normalize_line_sequence_endings(
                deletion.content_lines
            )
            translated = _translate_removal_reference(
                deletion.baseline_reference,
                content_lines,
                source_lines,
                target_lines,
                mapping,
                allow_content_change=True,
            )
            if translated is None:
                translated = _translate_deletion_from_origin_offset(
                    deletion,
                    origin,
                    target_lines,
                )
            ownership.deletions[deletion_index] = _translated_deletion(
                deletion,
                content_lines,
                target_lines,
                translated,
            )
            origin_backed[deletion_index] = 2


def _translate_replacement_origin_references(
    ownership: BatchOwnership,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    mapping: LineMapping,
) -> None:
    """Translate each shared replacement-parent reference exactly once."""
    with MappedRecordVector(
        len(ownership.replacement_units),
        "QQ",
    ) as origin_units:
        for unit_index, unit in enumerate(ownership.replacement_units):
            if unit.origin is not None:
                origin_units.append((id(unit.origin), unit_index))
        sort_mapped_records(origin_units)

        previous_origin_id: int | None = None
        for origin_id, unit_index in origin_units:
            if origin_id == previous_origin_id:
                continue
            previous_origin_id = origin_id
            origin = ownership.replacement_units[unit_index].origin
            assert origin is not None

            old_start = origin.old_start
            old_end = origin.old_end
            if (
                type(old_start) is not int
                or type(old_end) is not int
                or old_start < 1
                or old_end < old_start
                or old_end > len(source_lines)
            ):
                origin.baseline_reference = None
                continue

            content_lines = LineRangeView(
                source_lines,
                old_start - 1,
                old_end,
            )
            translated = _translate_removal_reference(
                origin.baseline_reference,
                content_lines,
                source_lines,
                target_lines,
                mapping,
                allow_content_change=True,
            )
            origin.baseline_reference = (
                translated[0] if translated is not None else None
            )


def _require_projected_replacement_references(
    ownership: BatchOwnership,
) -> None:
    """Reject replacement ownership that lost its persisted baseline identity."""
    for unit in ownership.replacement_units:
        if (
            unit.origin is not None
            and unit.origin.baseline_reference is None
        ):
            raise ValueError(
                "replacement origin could not be projected onto the batch baseline"
            )
        for deletion_index in unit.deletion_indices:
            if (
                type(deletion_index) is not int
                or deletion_index < 0
                or deletion_index >= len(ownership.deletions)
                or ownership.deletions[
                    deletion_index
                ].baseline_reference is None
            ):
                raise ValueError(
                    "replacement deletion could not be projected onto "
                    "the batch baseline"
                )


def translate_ownership_baseline_references(
    ownership: BatchOwnership,
    source_baseline_lines: Sequence[bytes],
    target_baseline_lines: Sequence[bytes],
    *,
    replacement_origin_source_lines: Sequence[bytes] | None = None,
) -> None:
    """Rebase newly translated ownership references onto a batch baseline.

    Ordinary claims use the selected view's comparison base. File-derived
    replacement origins use live HEAD coordinates and require their separate
    source sequence when available.
    """
    with MappedIntVector(
        len(ownership.deletions),
        width=4,
        fill=0,
    ) as origin_backed:
        _mark_origin_backed_deletions(ownership, origin_backed)
        normalized_source = normalize_line_sequence_endings(
            source_baseline_lines
        )
        normalized_target = normalize_line_sequence_endings(
            target_baseline_lines
        )
        source_sequence = as_acquirable_line_sequence(normalized_source)
        target_sequence = as_acquirable_line_sequence(normalized_target)

        with (
            source_sequence.acquire_lines() as source_lines,
            target_sequence.acquire_lines() as target_lines,
            match_lines(source_lines, target_lines) as mapping,
        ):
            _translate_presence_references(
                ownership,
                source_lines,
                target_lines,
                mapping,
            )

            _translate_deletion_references(
                ownership,
                (
                    deletion_index
                    for deletion_index in range(len(ownership.deletions))
                    if origin_backed[deletion_index] == 0
                ),
                source_lines,
                target_lines,
                mapping,
            )

        if (
            replacement_origin_source_lines is None
            or not ownership.replacement_units
        ):
            _require_projected_replacement_references(ownership)
            return

        normalized_origin_source = normalize_line_sequence_endings(
            replacement_origin_source_lines
        )
        origin_source_sequence = as_acquirable_line_sequence(
            normalized_origin_source
        )

        with (
            origin_source_sequence.acquire_lines() as origin_source_lines,
            target_sequence.acquire_lines() as target_lines,
            match_lines(origin_source_lines, target_lines) as mapping,
        ):
            _translate_replacement_origin_references(
                ownership,
                origin_source_lines,
                target_lines,
                mapping,
            )
            _translate_origin_backed_deletion_references(
                ownership,
                origin_backed,
                origin_source_lines,
                target_lines,
                mapping,
            )
        _require_projected_replacement_references(ownership)
