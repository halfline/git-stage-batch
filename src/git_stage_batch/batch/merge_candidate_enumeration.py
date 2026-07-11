"""Merge candidate enumeration for reviewed ambiguity resolution."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from .absence_constraints import (
    AbsenceChoice as _MergeAbsenceChoice,
    absence_ambiguity_key as _merge_absence_ambiguity_key,
    absence_choices_for_claim as _merge_absence_choices_for_claim,
)
from . import presence_constraints as _presence_constraints
from .baseline_replacement_choices import (
    ReplacementOriginChoice as _BaselineReplacementOriginChoice,
    replacement_origin_choices_for_unit as _replacement_origin_choices_for_unit,
)
from .match import match_lines
from .merge_candidates import (
    MergeCandidate as _MergeCandidate,
    MergeCandidateSet as _MergeCandidateSet,
    MergeResolution as _MergeResolution,
    MergeResolutionDecision as _MergeResolutionDecision,
)
from .merge_validation import (
    check_structural_validity as _check_merge_structural_validity,
)
from . import presence_placement_choices as _presence_placement_choices
from ..core.line_selection import LineRanges, LineSelection, coerce_line_ranges
from ..exceptions import MergeError as _MergeError
from ..i18n import _
from ..core.text_lines import normalize_line_endings

if TYPE_CHECKING:
    from .ownership import BatchOwnership
    from .ownership_absence_claims import AbsenceClaim


_MergeResolutionValidator = Callable[[_MergeResolution], bool]


def _replacement_origin_candidate_set(
    source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    working_lines: Sequence[bytes],
    presence_line_set: LineSelection,
    deletion_claims: list["AbsenceClaim"],
    *,
    resolution_is_valid: _MergeResolutionValidator,
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
                and owned_mapping.get_target_line_from_source_line(claimed_line)
                is not None
                for claimed_line in claimed_lines
            ):
                continue
            if len(unit.deletion_indices) != 1:
                raise _MergeError(
                    _("Batch was created from a different version of the file")
                )

            deletion_index = unit.deletion_indices[0]
            if deletion_index < 0 or deletion_index >= len(deletion_claims):
                raise _MergeError(
                    _("Batch was created from a different version of the file")
                )
            key, choices = _replacement_origin_choices_for_unit(
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
        if resolution_is_valid(resolution):
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
            if source_start == source_end
            else f"{source_start}-{source_end}"
        )
        target_range = (
            str(target_start)
            if target_start == target_end
            else f"{target_start}-{target_end}"
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


def _presence_candidate_set(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    presence_line_set: LineSelection,
    deletion_claims: list["AbsenceClaim"],
    *,
    resolution_is_valid: _MergeResolutionValidator,
    max_candidates: int,
) -> _MergeCandidateSet:
    presence_mapping = match_lines(source_lines, working_lines)
    try:
        presence_key, presence_choices = (
            _presence_placement_choices.presence_choices_for_missing_claimed_run(
                source_lines,
                working_lines,
                presence_line_set,
                presence_mapping,
                max_results=max_candidates + 1,
                trusted_source_lines={
                    deletion.anchor_line
                    for deletion in deletion_claims
                    if deletion.anchor_line is not None
                },
            )
        )
    finally:
        presence_mapping.close()

    if presence_key is not None and len(presence_choices) > max_candidates:
        raise _MergeError(_("Too many merge candidates to preview safely"))
    if presence_key is None or len(presence_choices) <= 1:
        return _MergeCandidateSet(())

    valid_choices: list[_presence_placement_choices.PresenceChoice] = []
    for choice in presence_choices:
        resolution = _MergeResolution({presence_key: choice.choice_index})
        if resolution_is_valid(resolution):
            valid_choices.append(choice)

    if len(valid_choices) <= 1:
        return _MergeCandidateSet(())

    count = len(valid_choices)
    ambiguity_target_line_range = (
        _presence_placement_choices.presence_ambiguity_target_line_range(
            valid_choices,
            len(working_lines),
        )
    )
    candidates: list[_MergeCandidate] = []
    for ordinal, choice in enumerate(valid_choices, start=1):
        summary = _(
            "insert source lines {start}-{end} after target line {after}, "
            "before target line {before}"
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
                explanation=_(
                    "surrounding source context has multiple compatible placements"
                ),
                ambiguity_target_line_range=ambiguity_target_line_range,
            )
        )
    return _MergeCandidateSet(tuple(candidates))


def _absence_candidate_set(
    source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    working_lines: Sequence[bytes],
    presence_line_set: LineSelection,
    deletion_claims: list["AbsenceClaim"],
    *,
    resolution_is_valid: _MergeResolutionValidator,
    max_candidates: int,
) -> _MergeCandidateSet:
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
            if resolution_is_valid(resolution):
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
                    explanation=_(
                        "deletion anchor has multiple compatible target placements"
                    ),
                    ambiguity_target_line_range=ambiguity_target_line_range,
                )
            )
        return _MergeCandidateSet(tuple(candidates))
    finally:
        realized_entries.close()


def enumerate_merge_batch_candidates_for_lines(
    source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    working_lines: Sequence[bytes],
    *,
    resolution_is_valid: _MergeResolutionValidator,
    max_candidates: int,
) -> _MergeCandidateSet:
    """Enumerate merge candidates for acquired normalized line sequences."""
    resolved = ownership.resolve()
    presence_line_set = resolved.presence_line_set
    deletion_claims = resolved.deletion_claims

    replacement_candidates = _replacement_origin_candidate_set(
        source_lines,
        ownership,
        working_lines,
        presence_line_set,
        deletion_claims,
        resolution_is_valid=resolution_is_valid,
        max_candidates=max_candidates,
    )
    if replacement_candidates.candidates:
        return replacement_candidates

    presence_candidates = _presence_candidate_set(
        source_lines,
        working_lines,
        presence_line_set,
        deletion_claims,
        resolution_is_valid=resolution_is_valid,
        max_candidates=max_candidates,
    )
    if presence_candidates.candidates:
        return presence_candidates

    return _absence_candidate_set(
        source_lines,
        ownership,
        working_lines,
        presence_line_set,
        deletion_claims,
        resolution_is_valid=resolution_is_valid,
        max_candidates=max_candidates,
    )
