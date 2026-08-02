"""Merge candidate enumeration for reviewed ambiguity resolution."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
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
from .baseline_replacement_ranges import (
    collect_replacement_source_ranges as _collect_replacement_source_ranges,
    selected_replacement_source_ranges as _selected_replacement_source_ranges,
)
from ..line_matching.match import match_lines
from ..line_matching.match_workspace import MatcherWorkspace
from .candidates import (
    MergeCandidate as _MergeCandidate,
    MergeCandidateSet as _MergeCandidateSet,
    MergeResolution as _MergeResolution,
    MergeResolutionDecision as _MergeResolutionDecision,
)
from .coordinate_strategy import (
    AMBIGUITY_KEY as _COORDINATE_STRATEGY_AMBIGUITY_KEY,
    CoordinateStrategyChoice as _CoordinateStrategyChoice,
    presence_lines_requiring_distinctive_context as _distinctive_context_lines,
)
from .validation import (
    check_structural_validity as _check_merge_structural_validity,
)
from . import presence_placement_choices as _presence_placement_choices
from ...core.line_selection import LineSelection, coerce_line_ranges
from ...exceptions import MergeError as _MergeError
from ...i18n import _
from ...core.text_lines import normalize_line_endings

if TYPE_CHECKING:
    from ..ownership.model import BatchOwnership
    from ..ownership.absence_claims import AbsenceClaim


_MergeResolutionValidator = Callable[[_MergeResolution], bool]


@dataclass(frozen=True, slots=True)
class _UnresolvedReplacementOrigin:
    """One replacement origin that still needs an explicit placement."""

    source_start: int
    source_end: int
    deletion_index: int
    ambiguity_key: str
    choices: tuple[_BaselineReplacementOriginChoice, ...]


def _coordinate_strategy_candidate_set(
    *,
    strategies_differ: bool,
    max_candidates: int,
) -> _MergeCandidateSet:
    """Offer both merge strategies when their valid results disagree."""
    if not strategies_differ:
        return _MergeCandidateSet.refused()
    if max_candidates < 2:
        raise _MergeError(_("Too many merge candidates to preview safely"))

    explanation = _(
        "recorded baseline coordinates and structural content matching "
        "produce different results"
    )
    choices = (
        (
            _CoordinateStrategyChoice.STRUCTURAL,
            _("use structural content matching"),
        ),
        (
            _CoordinateStrategyChoice.RECORDED_COORDINATES,
            _("use recorded baseline coordinates"),
        ),
    )
    return _MergeCandidateSet.review_required(
        tuple(
            _MergeCandidate(
                ordinal=ordinal,
                count=len(choices),
                decisions=(
                    _MergeResolutionDecision(
                        ambiguity_key=_COORDINATE_STRATEGY_AMBIGUITY_KEY,
                        choice_index=choice_index.value,
                    ),
                ),
                summary=summary,
                source_line_range=None,
                target_after_line=None,
                target_before_line=None,
                explanation=explanation,
                ambiguity_target_line_range=None,
            )
            for ordinal, (choice_index, summary) in enumerate(
                choices,
                start=1,
            )
        )
    )


def _find_unresolved_replacement_origin(
    source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    working_lines: Sequence[bytes],
    presence_line_set: LineSelection,
    deletion_claims: list["AbsenceClaim"],
    *,
    max_candidates: int,
    spool_dir: str | Path | None,
) -> _UnresolvedReplacementOrigin | None:
    """Return the only unresolved split replacement, if there is one."""
    owned_mapping = match_lines(
        source_lines,
        working_lines,
        spool_dir=spool_dir,
    )
    try:
        range_workspace = MatcherWorkspace(spool_dir=spool_dir)
        try:
            selected_presence = coerce_line_ranges(presence_line_set)
            unresolved = None
            for unit_index, unit in enumerate(ownership.replacement_units):
                if unit.origin is None:
                    continue

                claimed_ranges = _collect_replacement_source_ranges(
                    range_workspace,
                    unit.presence_lines,
                )
                if claimed_ranges is None:
                    raise _MergeError(
                        _("Batch was created from a different version of the file")
                    )
                try:
                    source_start: int | None = None
                    source_end: int | None = None
                    all_claimed_lines_are_mapped = True
                    for (
                        claimed_start,
                        claimed_end,
                    ) in _selected_replacement_source_ranges(
                        claimed_ranges,
                        selected_presence,
                    ):
                        if source_start is None:
                            source_start = claimed_start
                        source_end = claimed_end
                        if not all_claimed_lines_are_mapped:
                            continue
                        for claimed_line in range(claimed_start, claimed_end + 1):
                            if (
                                claimed_line > len(source_lines)
                                or owned_mapping.get_target_line_from_source_line(
                                    claimed_line
                                )
                                is None
                            ):
                                all_claimed_lines_are_mapped = False
                                break

                    if source_start is None or source_end is None:
                        continue
                    if all_claimed_lines_are_mapped:
                        continue
                    if len(unit.deletion_indices) != 1:
                        raise _MergeError(
                            _("Batch was created from a different version of the file")
                        )

                    deletion_index = unit.deletion_indices[0]
                    if type(deletion_index) is not int:
                        raise _MergeError(
                            _("Batch was created from a different version of the file")
                        )
                    if (
                        deletion_index < 0
                        or deletion_index >= len(deletion_claims)
                    ):
                        raise _MergeError(
                            _("Batch was created from a different version of the file")
                        )
                    key, choices = _replacement_origin_choices_for_unit(
                        deletion_claims[deletion_index],
                        unit_index,
                        unit,
                        _selected_replacement_source_ranges(
                            claimed_ranges,
                            selected_presence,
                        ),
                        working_lines,
                        max_results=max_candidates + 1,
                    )
                    if key is None:
                        continue
                    candidate = _UnresolvedReplacementOrigin(
                        source_start=source_start,
                        source_end=source_end,
                        deletion_index=deletion_index,
                        ambiguity_key=key,
                        choices=choices,
                    )
                    if unresolved is not None:
                        raise _MergeError(
                            _("Multiple split replacement placements need review")
                        )
                    unresolved = candidate
                finally:
                    range_workspace.close_resource(claimed_ranges)
            return unresolved
        finally:
            range_workspace.close()
    finally:
        owned_mapping.close()


def _replacement_origin_candidate_set(
    source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    working_lines: Sequence[bytes],
    presence_line_set: LineSelection,
    deletion_claims: list["AbsenceClaim"],
    *,
    resolution_is_valid: _MergeResolutionValidator,
    max_candidates: int,
    spool_dir: str | Path | None,
) -> _MergeCandidateSet:
    """Enumerate reviewed placements for one unresolved split replacement."""
    unresolved = _find_unresolved_replacement_origin(
        source_lines,
        ownership,
        working_lines,
        presence_line_set,
        deletion_claims,
        max_candidates=max_candidates,
        spool_dir=spool_dir,
    )
    if unresolved is None:
        return _MergeCandidateSet.refused()

    source_start = unresolved.source_start
    source_end = unresolved.source_end
    deletion_index = unresolved.deletion_index
    key = unresolved.ambiguity_key
    choices = unresolved.choices
    if len(choices) > max_candidates:
        raise _MergeError(_("Too many merge candidates to preview safely"))

    valid_choices: list[_BaselineReplacementOriginChoice] = []
    for choice in choices:
        resolution = _MergeResolution({key: choice.choice_index})
        if resolution_is_valid(resolution):
            valid_choices.append(choice)

    if not valid_choices:
        return _MergeCandidateSet.refused()

    count = len(valid_choices)
    claim = deletion_claims[deletion_index]
    line_count = len(claim.content_lines)
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
    return _MergeCandidateSet.review_required(tuple(candidates))


def _presence_candidate_set(
    source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    working_lines: Sequence[bytes],
    presence_line_set: LineSelection,
    deletion_claims: list["AbsenceClaim"],
    *,
    resolution_is_valid: _MergeResolutionValidator,
    max_candidates: int,
    spool_dir: str | Path | None,
) -> _MergeCandidateSet:
    distinctive_context_lines = _distinctive_context_lines(
        ownership,
        presence_line_set,
        deletion_claims,
        spool_dir=spool_dir,
    )
    presence_mapping = match_lines(
        source_lines,
        working_lines,
        spool_dir=spool_dir,
    )
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
                distinctive_context_lines=distinctive_context_lines,
                spool_dir=spool_dir,
            )
        )
    finally:
        presence_mapping.close()

    if presence_key is not None and len(presence_choices) > max_candidates:
        raise _MergeError(_("Too many merge candidates to preview safely"))
    if presence_key is None or len(presence_choices) <= 1:
        return _MergeCandidateSet.refused()

    valid_choices: list[_presence_placement_choices.PresenceChoice] = []
    for choice in presence_choices:
        resolution = _MergeResolution({presence_key: choice.choice_index})
        if resolution_is_valid(resolution):
            valid_choices.append(choice)

    if len(valid_choices) <= 1:
        return _MergeCandidateSet.refused()

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
    return _MergeCandidateSet.review_required(tuple(candidates))


def _absence_candidate_set(
    source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    working_lines: Sequence[bytes],
    presence_line_set: LineSelection,
    deletion_claims: list["AbsenceClaim"],
    *,
    resolution_is_valid: _MergeResolutionValidator,
    max_candidates: int,
    spool_dir: str | Path | None,
) -> _MergeCandidateSet:
    if not deletion_claims:
        return _MergeCandidateSet.refused()

    if len([claim for claim in deletion_claims if claim.content_lines]) != 1:
        raise _MergeError(_("Batch was created from a different version of the file"))

    distinctive_context_lines = _distinctive_context_lines(
        ownership,
        presence_line_set,
        deletion_claims,
        spool_dir=spool_dir,
    )

    owned_mapping = match_lines(
        source_lines,
        working_lines,
        spool_dir=spool_dir,
    )
    try:
        contextual_placements = _check_merge_structural_validity(
            owned_mapping,
            presence_line_set,
            deletion_claims,
            source_lines,
            working_lines,
            distinctive_presence_context_lines=distinctive_context_lines,
            spool_dir=spool_dir,
        )
        realized_entries = _presence_constraints.apply_presence_constraints(
            source_lines,
            working_lines,
            presence_line_set,
            source_to_working_mapping=owned_mapping,
            distinctive_context_lines=distinctive_context_lines,
            contextual_placements=contextual_placements,
            spool_dir=spool_dir,
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
            return _MergeCandidateSet.refused()

        valid_choices: list[_MergeAbsenceChoice] = []
        for choice in choices:
            resolution = _MergeResolution({ambiguity_key: choice.choice_index})
            if resolution_is_valid(resolution):
                valid_choices.append(choice)

        if len(valid_choices) <= 1:
            return _MergeCandidateSet.refused()

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
        return _MergeCandidateSet.review_required(tuple(candidates))
    finally:
        realized_entries.close()


def enumerate_merge_batch_candidates_for_lines(
    source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    working_lines: Sequence[bytes],
    *,
    resolution_is_valid: _MergeResolutionValidator,
    max_candidates: int,
    coordinate_strategies_differ: bool = False,
    spool_dir: str | Path | None = None,
) -> _MergeCandidateSet:
    """Enumerate merge candidates for acquired normalized line sequences."""
    coordinate_strategy_candidates = _coordinate_strategy_candidate_set(
        strategies_differ=coordinate_strategies_differ,
        max_candidates=max_candidates,
    )
    if coordinate_strategy_candidates.candidates:
        return coordinate_strategy_candidates

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
        spool_dir=spool_dir,
    )
    if replacement_candidates.candidates:
        return replacement_candidates

    presence_candidates = _presence_candidate_set(
        source_lines,
        ownership,
        working_lines,
        presence_line_set,
        deletion_claims,
        resolution_is_valid=resolution_is_valid,
        max_candidates=max_candidates,
        spool_dir=spool_dir,
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
        spool_dir=spool_dir,
    )
