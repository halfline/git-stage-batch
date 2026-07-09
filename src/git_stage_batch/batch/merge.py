"""Structural batch merge using Long Common Subsequence-based alignment."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import TYPE_CHECKING, Any

from .absence_constraints import (
    AbsenceChoice as _MergeAbsenceChoice,
    absence_ambiguity_key as _merge_absence_ambiguity_key,
    absence_choices_for_claim as _merge_absence_choices_for_claim,
    apply_absence_constraints as _apply_merge_absence_constraints,
)
from . import baseline_edits as _baseline_edits
from . import presence_constraints as _presence_constraints
from .baseline_edits import ReplacementOriginChoice as _BaselineReplacementOriginChoice
from .baseline_correspondence import (
    build_baseline_correspondence as _build_discard_baseline_correspondence,
)
from .discard_reversal import (
    reverse_presence_constraints as _reverse_batch_presence_constraints,
)
from .merge_candidates import (
    MergeCandidate as _MergeCandidate,
    MergeCandidateSet as _MergeCandidateSet,
    MergeResolution as _MergeResolution,
    MergeResolutionDecision as _MergeResolutionDecision,
)
from .merge_validation import (
    check_structural_validity as _check_merge_structural_validity,
)
from .match import LineMapping, match_lines
from .realized_entries import (
    RealizedEntry as _RealizedEntry,
    _RealizedEntries,
    _as_realized_entries,
    _entry_target_line_at,
    realized_entry_content_chunks as _realized_entry_content_chunks,
)
from . import realized_mapping as _realized_mapping
from .realized_boundaries import (
    find_boundary_after_source_line as _locate_boundary_after_source_line,
    sequence_present_at_boundary as _boundary_sequence_present,
)
from ..core.line_selection import LineRanges, LineSelection, coerce_line_ranges
from ..core.buffer import (
    LineBuffer,
    buffer_has_data,
)
from ..editor.line_endings import (
    choose_line_ending,
    restore_line_endings_in_chunks,
)
from ..exceptions import (
    AmbiguousAnchorError as _AmbiguousAnchorError,
    MergeError as _MergeError,
    MissingAnchorError as _MissingAnchorError,
)
from ..i18n import _
from ..utils.text import (
    AcquirableLineSequence,
    normalize_line_sequence_endings,
    normalize_line_endings,
)

if TYPE_CHECKING:
    from .ownership import BatchOwnership, AbsenceClaim


_MERGE_CANDIDATE_CAP = 50


def _byte_chunks(chunks: Iterable[Any]) -> Iterator[bytes]:
    for chunk in chunks:
        yield bytes(chunk)


def _merge_result_line_ending_from_lines(
    primary_lines: Sequence[bytes],
    fallback_lines: Sequence[bytes],
) -> bytes | None:
    """Choose the line ending style for line sequence merge output."""
    return choose_line_ending(primary_lines, fallback_lines)


def _discard_result_line_ending_from_lines(
    working_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
    source_lines: Sequence[bytes],
) -> bytes | None:
    """Choose the line ending style for line sequence discard output."""
    result_line_ending = choose_line_ending(working_lines)
    if result_line_ending is not None:
        return result_line_ending
    if buffer_has_data(baseline_lines):
        return choose_line_ending(baseline_lines)
    return choose_line_ending(source_lines)


def _replacement_origin_candidate_set(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    presence_line_set: LineSelection,
    deletion_claims: list['AbsenceClaim'],
    *,
    max_candidates: int,
) -> _MergeCandidateSet:
    """Enumerate reviewed placements for one unresolved split replacement."""
    owned_mapping = match_lines(source_lines, working_lines)
    try:
        selected_presence = coerce_line_ranges(presence_line_set)
        unresolved: list[
            tuple[list[int], int, str, tuple[_BaselineReplacementOriginChoice, ...]]
        ] = []
        for unit_index, unit in enumerate(getattr(ownership, "replacement_units", [])):
            if getattr(unit, "origin", None) is None:
                continue

            claimed_selection = LineRanges.from_specs(unit.presence_lines)
            claimed_lines = list(selected_presence.intersection(claimed_selection))
            if not claimed_lines:
                continue
            if all(
                1 <= claimed_line <= len(source_lines)
                and owned_mapping.get_target_line_from_source_line(claimed_line) is not None
                for claimed_line in claimed_lines
            ):
                continue
            if len(unit.deletion_indices) != 1:
                raise _MergeError(_("Batch was created from a different version of the file"))

            deletion_index = unit.deletion_indices[0]
            if deletion_index < 0 or deletion_index >= len(deletion_claims):
                raise _MergeError(_("Batch was created from a different version of the file"))
            key, choices = _baseline_edits.replacement_origin_choices_for_unit(
                deletion_claims[deletion_index],
                unit_index,
                unit,
                claimed_lines,
                working_lines,
                max_results=max_candidates + 1,
            )
            if key is None:
                continue
            unresolved.append((claimed_lines, deletion_index, key, choices))
    finally:
        owned_mapping.close()

    if not unresolved:
        return _MergeCandidateSet(())
    if len(unresolved) > 1:
        raise _MergeError(_("Multiple split replacement placements need review"))

    claimed_lines, deletion_index, key, choices = unresolved[0]
    if len(choices) > max_candidates:
        raise _MergeError(_("Too many merge candidates to preview safely"))

    valid_choices: list[_BaselineReplacementOriginChoice] = []
    for choice in choices:
        resolution = _MergeResolution({key: choice.choice_index})
        try:
            for _chunk in _merge_batch_acquired_line_chunks(
                source_lines,
                ownership,
                working_lines,
                resolution=resolution,
            ):
                pass
        except _MergeError:
            continue
        valid_choices.append(choice)

    if not valid_choices:
        return _MergeCandidateSet(())

    count = len(valid_choices)
    claim = deletion_claims[deletion_index]
    line_count = len(claim.content_lines)
    source_start = min(claimed_lines)
    source_end = max(claimed_lines)
    ambiguity_target_line_range = (
        min(choice.position + 1 for choice in valid_choices),
        max(choice.position + line_count for choice in valid_choices),
    )
    candidates: list[_MergeCandidate] = []
    for ordinal, choice in enumerate(valid_choices, start=1):
        target_start = choice.position + 1
        target_end = choice.position + line_count
        source_range = (
            str(source_start)
            if source_start == source_end else
            f"{source_start}-{source_end}"
        )
        target_range = (
            str(target_start)
            if target_start == target_end else
            f"{target_start}-{target_end}"
        )
        summary = _(
            "replace target lines {target} with source lines {source}"
        ).format(target=target_range, source=source_range)
        candidates.append(
            _MergeCandidate(
                ordinal=ordinal,
                count=count,
                decisions=(
                    _MergeResolutionDecision(
                        ambiguity_key=key,
                        choice_index=choice.choice_index,
                        choice_label=summary,
                    ),
                ),
                summary=summary,
                source_line_range=(source_start, source_end),
                target_after_line=choice.target_after_line,
                target_before_line=choice.target_before_line,
                explanation=_(
                    "original replacement boundary is not present; "
                    "selected replacement content has multiple compatible placements"
                ),
                ambiguity_target_line_range=ambiguity_target_line_range,
            )
        )
    return _MergeCandidateSet(tuple(candidates))


def merge_batch_from_line_sequences_as_buffer(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    *,
    source_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
) -> LineBuffer:
    """Merge line sequences and return a buffer with destination line endings."""
    result_line_ending = _merge_result_line_ending_from_lines(
        working_lines,
        source_lines,
    )
    normalized_source_lines = normalize_line_sequence_endings(source_lines)
    normalized_working_lines = normalize_line_sequence_endings(working_lines)
    return LineBuffer.from_chunks(
        restore_line_endings_in_chunks(
            _merge_batch_line_chunks(
                normalized_source_lines,
                ownership,
                normalized_working_lines,
                source_to_working_mapping=source_to_working_mapping,
                resolution=resolution,
            ),
            result_line_ending,
        )
    )


def can_merge_batch_from_line_sequences(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    *,
    source_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
) -> bool:
    """Return whether a normalized line merge can be applied."""
    normalized_source_lines = normalize_line_sequence_endings(source_lines)
    normalized_working_lines = normalize_line_sequence_endings(working_lines)
    try:
        for _chunk in _merge_batch_line_chunks(
            normalized_source_lines,
            ownership,
            normalized_working_lines,
            source_to_working_mapping=source_to_working_mapping,
            resolution=resolution,
        ):
            pass
    except _MergeError:
        return False
    return True


def _merge_batch_line_chunks(
    source_lines: AcquirableLineSequence[Any],
    ownership: 'BatchOwnership',
    working_lines: AcquirableLineSequence[Any],
    *,
    source_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
) -> Iterator[bytes]:
    """Merge normalized byte-line sequences and yield normalized chunks."""
    with (
        source_lines.acquire_lines() as acquired_source_lines,
        working_lines.acquire_lines() as acquired_working_lines,
    ):
        yield from _merge_batch_acquired_line_chunks(
            acquired_source_lines,
            ownership,
            acquired_working_lines,
            source_to_working_mapping=source_to_working_mapping,
            resolution=resolution,
        )


def _merge_batch_acquired_line_chunks(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    *,
    source_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
) -> Iterator[bytes]:
    """Merge acquired normalized line sequences and yield normalized chunks."""
    resolved = ownership.resolve()
    presence_line_set = resolved.presence_line_set
    deletion_claims = resolved.deletion_claims

    fallback_chunks = _baseline_edits.try_apply_baseline_replacement_units(
        source_lines,
        working_lines,
        ownership,
        presence_line_set,
        deletion_claims,
        resolution=resolution,
        max_resolution_choices=_MERGE_CANDIDATE_CAP + 1,
    )
    if fallback_chunks is not None:
        yield from _byte_chunks(fallback_chunks)
        return

    owned_mapping: LineMapping | None = None
    mapping = source_to_working_mapping
    if mapping is None:
        owned_mapping = match_lines(source_lines, working_lines)
        mapping = owned_mapping
    try:
        if _baseline_edits.has_missing_origin_replacement_claims(
            ownership,
            presence_line_set,
            source_lines,
            mapping,
        ):
            raise _MergeError(
                _(
                    "Cannot reliably place split replacement: original replacement "
                    "boundary is not present"
                )
            )

        try:
            _check_merge_structural_validity(
                mapping,
                presence_line_set,
                deletion_claims,
                source_lines,
                working_lines
            )
        except _MergeError:
            if resolution is None:
                raise

        realized_entries = _presence_constraints.satisfy_constraints(
            source_lines,
            working_lines,
            presence_line_set,
            deletion_claims,
            source_to_working_mapping=mapping,
            resolution=resolution,
        )
    except _MergeError:
        fallback_chunks = _baseline_edits.try_apply_baseline_replacement_units(
            source_lines,
            working_lines,
            ownership,
            presence_line_set,
            deletion_claims,
            resolution=resolution,
            max_resolution_choices=_MERGE_CANDIDATE_CAP + 1,
        )
        if fallback_chunks is not None:
            yield from _byte_chunks(fallback_chunks)
            return
        raise
    finally:
        if owned_mapping is not None:
            owned_mapping.close()

    try:
        yield from _realized_entry_content_chunks(realized_entries)
    finally:
        realized_entries.close()


def enumerate_merge_batch_candidates_from_line_sequences(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    *,
    max_candidates: int = _MERGE_CANDIDATE_CAP,
) -> _MergeCandidateSet:
    """Enumerate safe merge candidates for an otherwise-refused merge.

    The normal merge path remains ambiguity-intolerant. This helper first
    verifies that the ordinary merge refuses, then enumerates supported
    ambiguity choices one at a time.
    """
    normalized_source_lines = normalize_line_sequence_endings(source_lines)
    normalized_working_lines = normalize_line_sequence_endings(working_lines)
    with (
        normalized_source_lines.acquire_lines() as acquired_source_lines,
        normalized_working_lines.acquire_lines() as acquired_working_lines,
    ):
        try:
            for _chunk in _merge_batch_acquired_line_chunks(
                acquired_source_lines,
                ownership,
                acquired_working_lines,
            ):
                pass
            return _MergeCandidateSet(())
        except _MergeError:
            pass

        return _enumerate_merge_batch_candidates_acquired(
            acquired_source_lines,
            ownership,
            acquired_working_lines,
            max_candidates=max_candidates,
        )


def _enumerate_merge_batch_candidates_acquired(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    *,
    max_candidates: int,
) -> _MergeCandidateSet:
    resolved = ownership.resolve()
    presence_line_set = resolved.presence_line_set
    deletion_claims = resolved.deletion_claims

    replacement_candidates = _replacement_origin_candidate_set(
        source_lines,
        ownership,
        working_lines,
        presence_line_set,
        deletion_claims,
        max_candidates=max_candidates,
    )
    if replacement_candidates.candidates:
        return replacement_candidates

    presence_mapping = match_lines(source_lines, working_lines)
    try:
        presence_key, presence_choices = (
            _presence_constraints.presence_choices_for_missing_claimed_run(
                source_lines,
                working_lines,
                presence_line_set,
                presence_mapping,
                max_results=max_candidates + 1,
            )
        )
    finally:
        presence_mapping.close()

    if presence_key is not None and len(presence_choices) > max_candidates:
        raise _MergeError(_("Too many merge candidates to preview safely"))
    if presence_key is not None and len(presence_choices) > 1:
        valid_choices: list[_presence_constraints.PresenceChoice] = []
        for choice in presence_choices:
            resolution = _MergeResolution({presence_key: choice.choice_index})
            try:
                for _chunk in _merge_batch_acquired_line_chunks(
                    source_lines,
                    ownership,
                    working_lines,
                    resolution=resolution,
                ):
                    pass
            except _MergeError:
                continue
            valid_choices.append(choice)
        if len(valid_choices) > 1:
            count = len(valid_choices)
            ambiguity_target_line_range = (
                _presence_constraints.presence_ambiguity_target_line_range(
                    valid_choices,
                    len(working_lines),
                )
            )
            candidates = []
            for ordinal, choice in enumerate(valid_choices, start=1):
                summary = _(
                    "insert source lines {start}-{end} after target line {after}, before target line {before}"
                ).format(
                    start=choice.run_start,
                    end=choice.run_end,
                    after=choice.target_after_line or "start",
                    before=choice.target_before_line or "end",
                )
                candidates.append(
                    _MergeCandidate(
                        ordinal=ordinal,
                        count=count,
                        decisions=(
                            _MergeResolutionDecision(
                                ambiguity_key=presence_key,
                                choice_index=choice.choice_index,
                                choice_label=summary,
                            ),
                        ),
                        summary=summary,
                        source_line_range=(choice.run_start, choice.run_end),
                        target_after_line=choice.target_after_line,
                        target_before_line=choice.target_before_line,
                        explanation=_("surrounding source context has multiple compatible placements"),
                        ambiguity_target_line_range=ambiguity_target_line_range,
                    )
                )
            return _MergeCandidateSet(tuple(candidates))

    if not deletion_claims:
        return _MergeCandidateSet(())

    if len([claim for claim in deletion_claims if claim.content_lines]) != 1:
        raise _MergeError(_("Batch was created from a different version of the file"))

    owned_mapping = match_lines(source_lines, working_lines)
    try:
        _check_merge_structural_validity(
            owned_mapping,
            presence_line_set,
            deletion_claims,
            source_lines,
            working_lines,
        )
        realized_entries = _presence_constraints.apply_presence_constraints(
            source_lines,
            working_lines,
            presence_line_set,
            source_to_working_mapping=owned_mapping,
        )
    finally:
        owned_mapping.close()

    try:
        enumerable_claims = [
            (index, claim)
            for index, claim in enumerate(deletion_claims)
            if claim.content_lines
        ]
        claim_index, claim = enumerable_claims[0]
        forbidden_sequence = [
            normalize_line_endings(line)
            for line in claim.content_lines
        ]
        ambiguity_key = _merge_absence_ambiguity_key(
            claim_index,
            claim.anchor_line,
            forbidden_sequence,
        )
        choices = _merge_absence_choices_for_claim(
            realized_entries,
            claim.anchor_line,
            forbidden_sequence,
            max_results=max_candidates + 1,
        )
        if len(choices) > max_candidates:
            raise _MergeError(_("Too many merge candidates to preview safely"))
        if len(choices) <= 1:
            return _MergeCandidateSet(())

        valid_choices: list[_MergeAbsenceChoice] = []
        for choice in choices:
            resolution = _MergeResolution({ambiguity_key: choice.choice_index})
            try:
                for _chunk in _merge_batch_acquired_line_chunks(
                    source_lines,
                    ownership,
                    working_lines,
                    resolution=resolution,
                ):
                    pass
            except _MergeError:
                continue
            valid_choices.append(choice)

        if len(valid_choices) <= 1:
            return _MergeCandidateSet(())

        count = len(valid_choices)
        ambiguity_target_line_range = (
            min(choice.position + 1 for choice in valid_choices),
            max(choice.position + len(forbidden_sequence) for choice in valid_choices),
        )
        candidates: list[_MergeCandidate] = []
        for ordinal, choice in enumerate(valid_choices, start=1):
            target_start = choice.position + 1
            target_end = choice.position + len(forbidden_sequence)
            summary = (
                _("delete target lines {start}-{end}").format(
                    start=target_start,
                    end=target_end,
                )
                if target_start != target_end
                else _("delete target line {line}").format(line=target_start)
            )
            candidates.append(
                _MergeCandidate(
                    ordinal=ordinal,
                    count=count,
                    decisions=(
                        _MergeResolutionDecision(
                            ambiguity_key=ambiguity_key,
                            choice_index=choice.choice_index,
                            choice_label=summary,
                        ),
                    ),
                    summary=summary,
                    source_line_range=(
                        (claim.anchor_line, claim.anchor_line)
                        if claim.anchor_line is not None
                        else None
                    ),
                    target_after_line=choice.target_after_line,
                    target_before_line=choice.target_before_line,
                    explanation=_("deletion anchor has multiple compatible target placements"),
                    ambiguity_target_line_range=ambiguity_target_line_range,
                )
            )
        return _MergeCandidateSet(tuple(candidates))
    finally:
        realized_entries.close()


def discard_batch_from_line_sequences_as_buffer(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
) -> LineBuffer:
    """Discard ownership and return a buffer with destination line endings."""
    result_line_ending = _discard_result_line_ending_from_lines(
        working_lines,
        baseline_lines,
        source_lines,
    )
    normalized_source_lines = normalize_line_sequence_endings(source_lines)
    normalized_working_lines = normalize_line_sequence_endings(working_lines)
    normalized_baseline_lines = normalize_line_sequence_endings(baseline_lines)
    return LineBuffer.from_chunks(
        restore_line_endings_in_chunks(
            _discard_batch_line_chunks(
                normalized_source_lines,
                ownership,
                normalized_working_lines,
                normalized_baseline_lines,
            ),
            result_line_ending,
        ),
    )


def _discard_batch_line_chunks(
    source_lines: AcquirableLineSequence[Any],
    ownership: 'BatchOwnership',
    working_lines: AcquirableLineSequence[Any],
    baseline_lines: AcquirableLineSequence[Any],
) -> Iterator[bytes]:
    """Discard ownership from normalized byte-line sequences."""
    with (
        source_lines.acquire_lines() as acquired_source_lines,
        working_lines.acquire_lines() as acquired_working_lines,
        baseline_lines.acquire_lines() as acquired_baseline_lines,
    ):
        yield from _discard_batch_acquired_line_chunks(
            acquired_source_lines,
            ownership,
            acquired_working_lines,
            acquired_baseline_lines,
        )


def _discard_batch_acquired_line_chunks(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
) -> Iterator[bytes]:
    """Discard ownership from acquired normalized byte-line sequences."""
    resolved = ownership.resolve()
    presence_line_set = resolved.presence_line_set
    deletion_claims = resolved.deletion_claims

    with match_lines(source_lines, working_lines) as working_to_source:
        correspondence = _build_discard_baseline_correspondence(
            baseline_lines,
            source_lines
        )

        realized_entries = _build_realized_entries_for_discard(
            source_lines,
            working_lines,
            working_to_source
        )

    try:
        updated_entries = _reverse_batch_presence_constraints(
            realized_entries,
            presence_line_set,
            correspondence
        )
        if updated_entries is not realized_entries:
            realized_entries.close()
        realized_entries = updated_entries

        updated_entries = _restore_absence_constraints(
            realized_entries,
            deletion_claims
        )
        if updated_entries is not realized_entries:
            realized_entries.close()
        realized_entries = updated_entries

        yield from _realized_entry_content_chunks(realized_entries)
    finally:
        realized_entries.close()


def _build_realized_entries_for_discard(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    working_to_source: 'LineMapping'
) -> _RealizedEntries:
    """Build structured entries from working tree with source provenance.

    This creates a realized representation of the current working tree content,
    tagging each entry with its source-space provenance (if any). This allows
    subsequent discard operations to reason about which entries are batch-owned.

    Args:
        source_lines: Batch source lines (bytes with newlines)
        working_lines: Working tree lines (bytes with newlines)
        working_to_source: Mapping from source to working tree

    Returns:
        Realized entries representing working tree with source provenance
    """
    result = _RealizedEntries()
    _realized_mapping.append_working_range_with_mapping(
        result,
        working_lines,
        working_to_source,
        0,
        len(working_lines),
        set(),
    )

    return result


def _restore_absence_constraints(
    entries: Sequence[_RealizedEntry],
    deletion_claims: list['AbsenceClaim']
) -> _RealizedEntries:
    """Restore absence constraints: insert deleted sequences at anchored boundaries.

    For each absence claim, this function:
    1. Finds the exact boundary "after source line N" (or start-of-file)
    2. Checks if the deleted sequence is already present at that boundary
    3. If absent: inserts it at the exact boundary
    4. If present: no-op (already restored)
    5. If anchor not present: skip gracefully (claim not applicable)
    6. If anchor is ambiguous: raise error (structural problem)

    This is the inverse of absence constraint enforcement: where merge suppresses
    sequences at anchored boundaries, discard restores them.

    Anchor handling:
    - Missing anchor: Skip claim gracefully.
    - Ambiguous anchor: Raise AmbiguousAnchorError.

    Args:
        entries: Realized entries with source provenance
        deletion_claims: Absence constraints to restore

    Returns:
        Entries with deleted sequences restored at anchored boundaries

    Raises:
        AmbiguousAnchorError: If anchor boundary is ambiguous
        (MissingAnchorError is caught and skipped gracefully)
    """
    result = _as_realized_entries(entries)
    if not deletion_claims:
        return result

    for claim in deletion_claims:
        try:
            boundary = _locate_boundary_after_source_line(result, claim.anchor_line)
        except _MissingAnchorError:
            continue
        except _AmbiguousAnchorError:
            raise

        if _boundary_sequence_present(result, boundary, claim.content_lines):
            continue

        with _RealizedEntries() as restored_entries:
            restored_entries.append_line_range_from(
                claim.content_lines,
                0,
                len(claim.content_lines),
                source_line_start=None,
                is_claimed=False,
            )

            old_result = result
            result = result.insert_entries(boundary, restored_entries)
        if old_result is not entries:
            old_result.close()

    return result
